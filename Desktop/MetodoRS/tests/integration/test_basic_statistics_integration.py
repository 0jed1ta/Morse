from __future__ import annotations

from collections import Counter
from pathlib import Path

from sqlalchemy import create_engine

from src.data.parsers.soccerstats_parser import SoccerStatsParser
from src.domain.enums import DataOrigin, DataStatus, SoccerStatsMatchStatus
from src.domain.models import SoccerStatsMatch, FetchResult
from src.persistence.database import build_session_factory, initialize_schema
from src.persistence.repository import SoccerStatsMatchRepository
from src.services.basic_statistics_service import BasicStatisticsService
from src.services.data_collection_service import DataCollectionService

SOURCE_URL = "https://www.soccerstats.com/results.asp?league=spain"
SOURCE_FILE_LABEL = "data/raw/Spain - LaLiga results(1).html"


class LocalFetchProvider:
    base_url = "https://www.soccerstats.com"

    def __init__(self, fetch_result: FetchResult) -> None:
        self.fetch_result = fetch_result
        self.requested_urls: list[str] = []

    def fetch(self, url: str) -> FetchResult:
        self.requested_urls.append(url)
        return self.fetch_result


def _real_cached_fetch_result() -> FetchResult:
    source_file = Path(__file__).resolve().parents[2] / "data" / "raw" / "Spain - LaLiga results(1).html"
    return FetchResult(
        status=DataStatus.OK,
        reason="Valid HTML served from local cache.",
        requested_url=SOURCE_URL,
        final_url=SOURCE_URL,
        content_type="text/html; charset=windows-1252",
        html=source_file.read_text(encoding="utf-8"),
        origin=DataOrigin.REAL,
        from_cache=True,
    )


def _build_persistence():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    initialize_schema(engine)
    repository = SoccerStatsMatchRepository(build_session_factory(engine))
    league = repository.ensure_league("Spain", "LaLiga", source_url=SOURCE_URL, canonical_url=SOURCE_URL)
    scope = repository.ensure_source_scope(
        league.league_id,
        "phase-1-14-basic-stats",
        description="Integration scope for basic statistics over persisted matches.",
        canonical_source_url=SOURCE_URL,
    )
    return engine, repository, scope


def test_basic_statistics_over_persisted_matches():
    engine, repository, scope = _build_persistence()
    try:
        provider = LocalFetchProvider(_real_cached_fetch_result())
        parser = SoccerStatsParser()
        service = DataCollectionService(
            provider=provider,
            parser=parser,
            repository=repository,
            persistence_scope_id=scope.scope_id,
        )
        result = service.fetch_match_results(SOURCE_URL, source_file=SOURCE_FILE_LABEL)

        assert result.status is DataStatus.OK
        assert len(result.records) == 380
        assert Counter(match.status for match in result.records) == {
            SoccerStatsMatchStatus.PLAYED: 69,
            SoccerStatsMatchStatus.FUTURE: 311,
        }

        stored = repository.list_matches(scope.scope_id)
        assert len(stored) == 380

        domain_matches = repository.list_domain_matches(scope.scope_id)
        assert len(domain_matches) == 380
        assert all(isinstance(match, SoccerStatsMatch) for match in domain_matches)

        # Helper to get matches for a team (by name in home or away)
        def team_matches(team_name: str) -> list[SoccerStatsMatch]:
            return [
                m
                for m in domain_matches
                if m.home_team.value == team_name or m.away_team.value == team_name
            ]

        stats_service = BasicStatisticsService()

        # --- Valencia: check general, home, away, and exclusions ---
        valencia_matches = team_matches("Valencia")
        valencia_played = [m for m in valencia_matches if m.status == SoccerStatsMatchStatus.PLAYED]
        valencia_future = [m for m in valencia_matches if m.status == SoccerStatsMatchStatus.FUTURE]

        # GENERAL
        general = stats_service.calculate(domain_matches, "Valencia", "GENERAL")
        assert general.context == "GENERAL"
        assert general.matches_played == len(valencia_played)
        assert general.goals_for is not None
        assert general.goals_against is not None
        assert general.average_total_goals is not None
        assert general.over_1_5.denominator == general.matches_played
        assert general.over_2_5.denominator == general.matches_played
        assert general.over_3_5.denominator == general.matches_played
        assert general.btts.denominator == general.matches_played
        assert general.status is DataStatus.OK
        assert general.exclusions.total == len(valencia_future)
        assert general.exclusions.by_status.get("FUTURE", 0) == len(valencia_future)

        # HOME and AWAY (should partition the played matches)
        home = stats_service.calculate(domain_matches, "Valencia", "HOME")
        away = stats_service.calculate(domain_matches, "Valencia", "AWAY")
        assert home.matches_played + away.matches_played == general.matches_played
        assert home.context == "HOME"
        assert away.context == "AWAY"
        assert home.exclusions.total + away.exclusions.total == general.exclusions.total

        # --- Non-existent team: should yield NOT_AVAILABLE and zero exclusions ---
        nonexistent = stats_service.calculate(domain_matches, "NonExistentTeam", "GENERAL")
        assert nonexistent.matches_played == 0
        assert nonexistent.goals_for is None
        assert nonexistent.goals_against is None
        assert nonexistent.average_total_goals is None
        assert nonexistent.over_1_5.status is DataStatus.NOT_AVAILABLE
        assert nonexistent.over_2_5.status is DataStatus.NOT_AVAILABLE
        assert nonexistent.over_3_5.status is DataStatus.NOT_AVAILABLE
        assert nonexistent.btts.status is DataStatus.NOT_AVAILABLE
        assert nonexistent.status is DataStatus.NOT_AVAILABLE
        assert nonexistent.exclusions.total == 0

        # --- Order independence: reversed list must produce identical statistics ---
        reversed_matches = list(reversed(domain_matches))
        general_reversed = stats_service.calculate(reversed_matches, "Valencia", "GENERAL")
        assert general_reversed == general  # Dataclass equality works for nested FrozenInstances

        # --- Specific match round-trip: ensure mapping from domain to persistence and back is correct ---
        # Valencia vs Celta Vigo (played 0-0)
        valencia_celta = next(
            m for m in domain_matches
            if m.home_team.value == "Valencia" and m.away_team.value == "Celta Vigo"
        )
        assert valencia_celta.status is SoccerStatsMatchStatus.PLAYED
        assert valencia_celta.ft_raw.value == "0:0"
        assert valencia_celta.home_goals.value == 0
        assert valencia_celta.away_goals.value == 0
        assert valencia_celta.total_goals.value == 0
        assert valencia_celta.over_2_5.value is False
        assert valencia_celta.btts.value is False

        # Athletic Bilbao vs Sevilla FC (played 1-3)
        athletic_sevilla = next(
            m for m in domain_matches
            if m.home_team.value == "Athletic Bilbao" and m.away_team.value == "Sevilla FC"
        )
        assert athletic_sevilla.ft_raw.value == "1:3"
        assert athletic_sevilla.home_goals.value == 1
        assert athletic_sevilla.away_goals.value == 3
        assert athletic_sevilla.total_goals.value == 4
        assert athletic_sevilla.over_2_5.value is True
        assert athletic_sevilla.btts.value is True

        # Valencia vs Elche (future)
        valencia_elche = next(
            m for m in domain_matches
            if m.home_team.value == "Valencia" and m.away_team.value == "Elche"
        )
        assert valencia_elche.status is SoccerStatsMatchStatus.FUTURE
        assert valencia_elche.kickoff_time.value == "20:00"
        assert valencia_elche.home_goals.value is None
        assert valencia_elche.away_goals.value is None
        assert valencia_elche.ft_raw.value is None
        assert valencia_elche.kickoff_time.status is DataStatus.OK
    finally:
        engine.dispose()