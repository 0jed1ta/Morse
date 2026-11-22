from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased, sessionmaker

from src.domain.enums import DataOrigin, DataStatus, SoccerStatsMatchStatus, ValueOrigin
from src.domain.models import FieldValue, Provenance, SoccerStatsMatch
from src.persistence.orm_models import (
    LeagueRow,
    MatchIngestionIssueRow,
    MatchRow,
    SourceScopeRow,
    TeamRow,
)


class PersistenceOriginError(ValueError):
    """Raised when non-REAL data is sent to a production-configured repository."""


class PersistenceIdentityError(ValueError):
    """Raised when a record cannot be keyed safely for persistence."""


@dataclass(frozen=True)
class PersistenceOutcome:
    action: str
    match: MatchRow | None = None
    issue: MatchIngestionIssueRow | None = None


class SoccerStatsMatchRepository:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        allow_non_real_origins: bool = False,
    ) -> None:
        bind = sessions.kw.get("bind")
        if allow_non_real_origins and (bind is None or bind.dialect.name != "sqlite"):
            raise PersistenceOriginError("Non-REAL origins may only be enabled for an isolated SQLite test database.")
        self.sessions = sessions
        self.allow_non_real_origins = allow_non_real_origins

    def ensure_league(
        self,
        country: str,
        name: str,
        *,
        source_url: str | None = None,
        canonical_url: str | None = None,
    ) -> LeagueRow:
        with self.sessions.begin() as session:
            row = session.scalar(
                select(LeagueRow).where(LeagueRow.country == country, LeagueRow.name == name)
            )
            if row is None:
                row = LeagueRow(country=country, name=name, source_url=source_url, canonical_url=canonical_url)
                session.add(row)
                session.flush()
            else:
                if source_url is not None:
                    row.source_url = source_url
                if canonical_url is not None:
                    row.canonical_url = canonical_url
            return row

    def ensure_team(self, league_id: int, name: str, *, source_url: str | None = None) -> TeamRow:
        with self.sessions.begin() as session:
            row = session.scalar(
                select(TeamRow).where(TeamRow.league_id == league_id, TeamRow.name == name)
            )
            if row is None:
                row = TeamRow(league_id=league_id, name=name, source_url=source_url)
                session.add(row)
                session.flush()
            elif source_url is not None:
                row.source_url = source_url
            return row

    def ensure_source_scope(
        self,
        league_id: int,
        scope_key: str,
        *,
        description: str | None = None,
        canonical_source_url: str | None = None,
    ) -> SourceScopeRow:
        if not scope_key.strip():
            raise ValueError("scope_key must be a stable non-empty operational key, not a season guess.")
        with self.sessions.begin() as session:
            row = session.scalar(
                select(SourceScopeRow).where(
                    SourceScopeRow.league_id == league_id,
                    SourceScopeRow.scope_key == scope_key,
                )
            )
            if row is None:
                row = SourceScopeRow(
                    league_id=league_id,
                    scope_key=scope_key,
                    description=description,
                    canonical_source_url=canonical_source_url,
                )
                session.add(row)
                session.flush()
            else:
                if description is not None:
                    row.description = description
                if canonical_source_url is not None:
                    row.canonical_source_url = canonical_source_url
            return row

    def save_match(self, scope_id: int, match: SoccerStatsMatch) -> PersistenceOutcome:
        return self.save_matches(scope_id, [match])[0]

    def save_matches(self, scope_id: int, matches: Iterable[SoccerStatsMatch]) -> list[PersistenceOutcome]:
        records = list(matches)
        outcomes: list[PersistenceOutcome] = []
        with self.sessions.begin() as session:
            scope = session.get(SourceScopeRow, scope_id)
            if scope is None:
                raise PersistenceIdentityError(f"Unknown source_scope_id: {scope_id}")
            for match in records:
                self._validate_origin(match)
                if match.status in {SoccerStatsMatchStatus.MISSING_DATA, SoccerStatsMatchStatus.INVALID_FORMAT}:
                    outcomes.append(self._save_issue(session, scope, match))
                    continue
                if match.status not in {SoccerStatsMatchStatus.PLAYED, SoccerStatsMatchStatus.FUTURE}:
                    raise ValueError(f"Unsupported SoccerStatsMatchStatus: {match.status}")
                outcomes.append(self._upsert_match(session, scope, match))
            session.flush()
        return outcomes

    def update_match(self, match_id: int, scope_id: int, match: SoccerStatsMatch) -> PersistenceOutcome:
        existing = self.get_by_id(match_id)
        if existing is None:
            raise PersistenceIdentityError(f"Unknown match_id: {match_id}")
        if existing.scope_id != scope_id:
            raise PersistenceIdentityError("A match cannot be moved between source scopes during update.")
        identity = match.logical_identity
        if identity is None:
            raise PersistenceIdentityError("Updated match has no complete logical identity.")
        identified = self.get_by_identity(scope_id, *identity)
        if identified is None or identified.match_id != match_id:
            raise PersistenceIdentityError("Update identity does not match the persisted logical identity.")
        return self.save_match(scope_id, match)

    def get_by_id(self, match_id: int) -> MatchRow | None:
        with self.sessions() as session:
            return session.get(MatchRow, match_id)

    def get_by_identity(
        self,
        scope_id: int,
        date_raw: str,
        home_team: str,
        away_team: str,
    ) -> MatchRow | None:
        home_row = aliased(TeamRow)
        away_row = aliased(TeamRow)
        with self.sessions() as session:
            return session.scalar(
                select(MatchRow)
                .join(
                    home_row,
                    (home_row.team_id == MatchRow.home_team_id) & (home_row.league_id == MatchRow.league_id),
                )
                .join(
                    away_row,
                    (away_row.team_id == MatchRow.away_team_id) & (away_row.league_id == MatchRow.league_id),
                )
                .where(
                    MatchRow.scope_id == scope_id,
                    MatchRow.date_raw == date_raw,
                    home_row.name == home_team,
                    away_row.name == away_team,
                )
            )

    def list_matches(self, scope_id: int) -> list[MatchRow]:
        with self.sessions() as session:
            return list(
                session.scalars(
                    select(MatchRow).where(MatchRow.scope_id == scope_id).order_by(MatchRow.match_id)
                ).all()
            )

    def list_domain_matches(self, scope_id: int) -> list[SoccerStatsMatch]:
        home_row = aliased(TeamRow)
        away_row = aliased(TeamRow)
        with self.sessions() as session:
            stmt = (
                select(MatchRow, home_row.name, away_row.name)
                .join(
                    home_row,
                    (home_row.team_id == MatchRow.home_team_id) & (home_row.league_id == MatchRow.league_id),
                )
                .join(
                    away_row,
                    (away_row.team_id == MatchRow.away_team_id) & (away_row.league_id == MatchRow.league_id),
                )
                .where(MatchRow.scope_id == scope_id)
                .order_by(MatchRow.match_id)
            )
            rows = session.execute(stmt).all()
            return [self._row_to_domain_match(match_row, home_name, away_name) for match_row, home_name, away_name in rows]

    def list_issues(self, scope_id: int) -> list[MatchIngestionIssueRow]:
        with self.sessions() as session:
            return list(
                session.scalars(
                    select(MatchIngestionIssueRow)
                    .where(MatchIngestionIssueRow.scope_id == scope_id)
                    .order_by(MatchIngestionIssueRow.issue_id)
                ).all()
            )

    def _validate_origin(self, match: SoccerStatsMatch) -> None:
        if match.provenance.origin is not DataOrigin.REAL and not self.allow_non_real_origins:
            raise PersistenceOriginError(
                f"Production persistence accepts REAL only; received {match.provenance.origin.value}."
            )

    @staticmethod
    def _field_statuses(match: SoccerStatsMatch) -> dict[str, str]:
        return {
            field_name: getattr(match, field_name).status.value
            for field_name in (
                "date_raw", "home_team", "away_team", "kickoff_time", "ft_raw", "home_goals",
                "away_goals", "ht_raw", "halftime_home_goals", "halftime_away_goals", "total_goals",
                "over_2_5_raw", "over_2_5", "bts_raw", "btts", "h2h_url",
            )
        }

    @staticmethod
    def _raw_values(match: SoccerStatsMatch) -> dict:
        values = {
            "date_raw": match.date_raw.raw_value,
            "home_team": match.home_team.raw_value,
            "away_team": match.away_team.raw_value,
            "kickoff_time": match.kickoff_time.raw_value,
            "ft_raw": match.ft_raw.raw_value,
            "ht_raw": match.ht_raw.raw_value,
            "total_goals_raw": match.total_goals.raw_value,
            "over_2_5_raw": match.over_2_5_raw.raw_value,
            "bts_raw": match.bts_raw.raw_value,
            "h2h_url": match.h2h_url.raw_value,
        }
        values["field_statuses"] = SoccerStatsMatchRepository._field_statuses(match)
        values["field_origins"] = {
            field_name: getattr(match, field_name).origin.value
            if getattr(match, field_name).origin is not None
            else None
            for field_name in (
                "date_raw", "home_team", "away_team", "kickoff_time", "ft_raw", "home_goals",
                "away_goals", "ht_raw", "halftime_home_goals", "halftime_away_goals", "total_goals",
                "over_2_5_raw", "over_2_5", "bts_raw", "btts", "h2h_url",
            )
        }
        return values

    @staticmethod
    def _ensure_team(session: Session, league_id: int, name: str) -> TeamRow:
        team = session.scalar(
            select(TeamRow).where(TeamRow.league_id == league_id, TeamRow.name == name)
        )
        if team is None:
            team = TeamRow(league_id=league_id, name=name)
            session.add(team)
            session.flush()
        return team

    def _upsert_match(
        self,
        session: Session,
        scope: SourceScopeRow,
        match: SoccerStatsMatch,
    ) -> PersistenceOutcome:
        identity = match.logical_identity
        if identity is None:
            return self._save_issue(session, scope, match, issue="Match has no complete logical identity.")
        date_raw, home_name, away_name = identity
        if match.status is SoccerStatsMatchStatus.PLAYED:
            required = (
                match.ft_raw.value,
                match.home_goals.value,
                match.away_goals.value,
                match.ht_raw.value,
                match.total_goals.value,
                match.halftime_home_goals.value,
                match.halftime_away_goals.value,
                match.over_2_5_raw.value,
                match.over_2_5.value,
                match.bts_raw.value,
                match.btts.value,
            )
            if any(value is None for value in required):
                return self._save_issue(
                    session,
                    scope,
                    match,
                    issue="PLAYED match is missing required raw or normalized result fields.",
                    issue_status=SoccerStatsMatchStatus.MISSING_DATA.value,
                )
            home_goals = match.home_goals.value
            away_goals = match.away_goals.value
            total_goals = match.total_goals.value
            halftime_home = match.halftime_home_goals.value
            halftime_away = match.halftime_away_goals.value
            inconsistencies = []
            if min(home_goals, away_goals, total_goals, halftime_home, halftime_away) < 0:
                inconsistencies.append("goal values must be nonnegative")
            if total_goals != home_goals + away_goals:
                inconsistencies.append(
                    f"TG={total_goals} contradicts FT={match.ft_raw.value} (expected {home_goals + away_goals})"
                )
            if halftime_home > home_goals or halftime_away > away_goals:
                inconsistencies.append(f"HT={match.ht_raw.value} exceeds FT={match.ft_raw.value} for at least one team")
            if match.over_2_5.value != (total_goals > 2):
                inconsistencies.append(f"Over 2.5 raw='{match.over_2_5_raw.value}' contradicts TG={total_goals}")
            if match.btts.value != (home_goals > 0 and away_goals > 0):
                inconsistencies.append(f"BTS raw='{match.bts_raw.value}' contradicts FT={match.ft_raw.value}")
            if inconsistencies:
                return self._save_issue(
                    session,
                    scope,
                    match,
                    issue="; ".join(inconsistencies),
                    issue_type="RESULT_CONFLICT",
                    match_id=None,
                )
        if match.status is SoccerStatsMatchStatus.FUTURE and not match.kickoff_time.value:
            return self._save_issue(session, scope, match, issue="FUTURE match is missing kickoff_time.")

        home_team = self._ensure_team(session, scope.league_id, home_name)
        away_team = self._ensure_team(session, scope.league_id, away_name)
        existing = session.scalar(
            select(MatchRow).where(
                MatchRow.scope_id == scope.scope_id,
                MatchRow.date_raw == date_raw,
                MatchRow.home_team_id == home_team.team_id,
                MatchRow.away_team_id == away_team.team_id,
            )
        )
        if existing is not None and existing.status == SoccerStatsMatchStatus.PLAYED.value:
            if match.status is SoccerStatsMatchStatus.FUTURE:
                return PersistenceOutcome("IGNORED_STALE_FUTURE", existing)
            if self._played_values(existing) != self._played_values_from_domain(match):
                issue = self._save_issue(
                    session,
                    scope,
                    match,
                    issue="Conflicting PLAYED result for an existing logical match; canonical row left unchanged.",
                    issue_type="RESULT_CONFLICT",
                    match_id=existing.match_id,
                )
                return PersistenceOutcome("CONFLICT", existing, issue.issue)

        values = self._mapped_values(scope, home_team, away_team, match)
        if existing is None:
            existing = MatchRow(**values)
            session.add(existing)
            session.flush()
            return PersistenceOutcome("INSERTED", existing)

        if values["season"] is None and existing.season is not None:
            values["season"] = existing.season

        if all(getattr(existing, name) == value for name, value in values.items()):
            return PersistenceOutcome("UNCHANGED", existing)

        for name, value in values.items():
            setattr(existing, name, value)
        session.flush()
        return PersistenceOutcome("UPDATED", existing)

    @staticmethod
    def _played_values(row: MatchRow) -> tuple:
        return (
            row.home_goals, row.away_goals, row.total_goals,
            row.halftime_home_goals, row.halftime_away_goals, row.over_2_5, row.btts,
        )

    @staticmethod
    def _played_values_from_domain(match: SoccerStatsMatch) -> tuple:
        return (
            match.home_goals.value, match.away_goals.value, match.total_goals.value,
            match.halftime_home_goals.value, match.halftime_away_goals.value,
            match.over_2_5.value, match.btts.value,
        )

    @staticmethod
    def _mapped_values(
        scope: SourceScopeRow,
        home_team: TeamRow,
        away_team: TeamRow,
        match: SoccerStatsMatch,
    ) -> dict:
        provenance = match.provenance
        return {
            "scope_id": scope.scope_id,
            "league_id": scope.league_id,
            "home_team_id": home_team.team_id,
            "away_team_id": away_team.team_id,
            "season": provenance.season,
            "date_raw": match.date_raw.value,
            "match_date": None,
            "kickoff_time": time.fromisoformat(match.kickoff_time.value) if match.kickoff_time.value else None,
            "kickoff_time_raw": match.kickoff_time.raw_value,
            "status": match.status.value,
            "data_origin": provenance.origin.value,
            "home_goals": match.home_goals.value,
            "away_goals": match.away_goals.value,
            "total_goals": match.total_goals.value,
            "halftime_home_goals": match.halftime_home_goals.value,
            "halftime_away_goals": match.halftime_away_goals.value,
            "over_2_5": match.over_2_5.value,
            "btts": match.btts.value,
            "ft_raw": match.ft_raw.raw_value,
            "ht_raw": match.ht_raw.raw_value,
            "total_goals_raw": match.total_goals.raw_value,
            "over_2_5_raw": match.over_2_5_raw.raw_value,
            "bts_raw": match.bts_raw.raw_value,
            "h2h_url": match.h2h_url.raw_value,
            "source": provenance.source or "SoccerSTATS",
            "source_url": provenance.source_url or "https://www.soccerstats.com/results.asp?league=spain",
            "canonical_url": provenance.canonical_url,
            "source_file": provenance.source_file,
            "row_identity": provenance.row_identity or " | ".join(match.logical_identity or ()),
            "extraction_method": provenance.extraction_method,
            "extracted_at": provenance.extracted_at,
            "quality": provenance.quality,
            "field_statuses": SoccerStatsMatchRepository._field_statuses(match),
        }

    def _save_issue(
        self,
        session: Session,
        scope: SourceScopeRow,
        match: SoccerStatsMatch,
        *,
        issue: str | None = None,
        issue_type: str = "PARSE",
        match_id: int | None = None,
        issue_status: str | None = None,
    ) -> PersistenceOutcome:
        identity = match.logical_identity
        status = issue_status or (
            match.status.value
            if match.status in {SoccerStatsMatchStatus.MISSING_DATA, SoccerStatsMatchStatus.INVALID_FORMAT}
            else None
        )
        issue_message = issue or match.issue or f"SoccerStats row status {status}."
        row_identity = match.provenance.row_identity or " | ".join(identity or ())
        if match_id is None and identity is not None:
            date_raw, home_name, away_name = identity
            home_team_id = session.scalar(
                select(TeamRow.team_id).where(
                    TeamRow.league_id == scope.league_id,
                    TeamRow.name == home_name,
                )
            )
            away_team_id = session.scalar(
                select(TeamRow.team_id).where(
                    TeamRow.league_id == scope.league_id,
                    TeamRow.name == away_name,
                )
            )
            if home_team_id is not None and away_team_id is not None:
                existing_match = session.scalar(
                    select(MatchRow).where(
                        MatchRow.scope_id == scope.scope_id,
                        MatchRow.date_raw == date_raw,
                        MatchRow.home_team_id == home_team_id,
                        MatchRow.away_team_id == away_team_id,
                    )
                )
                if existing_match is not None:
                    match_id = existing_match.match_id
        existing = None
        if row_identity:
            existing = session.scalar(
                select(MatchIngestionIssueRow).where(
                    MatchIngestionIssueRow.scope_id == scope.scope_id,
                    MatchIngestionIssueRow.row_identity == row_identity,
                    MatchIngestionIssueRow.status == status,
                    MatchIngestionIssueRow.issue_type == issue_type,
                )
            )
        values = {
            "match_id": match_id,
            "scope_id": scope.scope_id,
            "league_id": scope.league_id,
            "status": status,
            "issue_type": issue_type,
            "issue": issue_message,
            "date_raw": match.date_raw.raw_value,
            "home_team_raw": match.home_team.raw_value,
            "away_team_raw": match.away_team.raw_value,
            "raw_data": self._raw_values(match),
            "source": match.provenance.source or "SoccerSTATS",
            "source_url": match.provenance.source_url or "https://www.soccerstats.com/results.asp?league=spain",
            "canonical_url": match.provenance.canonical_url,
            "source_file": match.provenance.source_file,
            "row_identity": row_identity,
            "extraction_method": match.provenance.extraction_method,
            "extracted_at": match.provenance.extracted_at,
            "data_origin": match.provenance.origin.value,
        }
        if existing is None:
            existing = MatchIngestionIssueRow(**values)
            session.add(existing)
            session.flush()
            return PersistenceOutcome("ISSUE_RECORDED", issue=existing)
        for name, value in values.items():
            setattr(existing, name, value)
        session.flush()
        return PersistenceOutcome("ISSUE_UPDATED", issue=existing)

    @staticmethod
    def _row_to_domain_match(
        match_row: MatchRow,
        home_name: str,
        away_name: str,
    ) -> SoccerStatsMatch:
        """Reconstruct a domain SoccerStatsMatch from a persisted MatchRow.

        Field statuses are recovered from the stored ``field_statuses`` JSON so
        that a persisted FUTURE row (no result data) is rebuilt with
        NOT_AVAILABLE statuses rather than silently claiming OK.
        """
        statuses = match_row.field_statuses or {}

        def _fv(
            field_name: str,
            value,
            *,
            derived: bool = False,
            raw_value=None,
        ) -> FieldValue:
            stored_status = statuses.get(field_name)
            if value is None:
                status = DataStatus(stored_status) if stored_status else DataStatus.NOT_AVAILABLE
                origin = ValueOrigin.NOT_AVAILABLE
            else:
                status = DataStatus(stored_status) if stored_status else DataStatus.OK
                origin = ValueOrigin.DERIVED if derived else ValueOrigin.EXTRACTED
            return FieldValue(
                value=value,
                status=status,
                raw_value=raw_value if raw_value is not None else value,
                normalized_value=value,
                origin=origin,
            )

        provenance = Provenance(
            source=match_row.source,
            source_url=match_row.source_url,
            canonical_url=match_row.canonical_url,
            source_file=match_row.source_file,
            row_identity=match_row.row_identity,
            extraction_method=match_row.extraction_method,
            extracted_at=match_row.extracted_at,
            quality=match_row.quality,
            season=match_row.season,
            origin=DataOrigin(match_row.data_origin),
        )

        kickoff_val = (
            match_row.kickoff_time_raw
            or (match_row.kickoff_time.strftime("%H:%M") if match_row.kickoff_time else None)
        )
        return SoccerStatsMatch(
            status=SoccerStatsMatchStatus(match_row.status),
            date_raw=_fv("date_raw", match_row.date_raw),
            home_team=_fv("home_team", home_name),
            away_team=_fv("away_team", away_name),
            kickoff_time=_fv(
                "kickoff_time",
                kickoff_val,
                raw_value=match_row.kickoff_time_raw,
            ),
            ft_raw=_fv("ft_raw", match_row.ft_raw),
            home_goals=_fv("home_goals", match_row.home_goals, derived=True, raw_value=match_row.ft_raw),
            away_goals=_fv("away_goals", match_row.away_goals, derived=True, raw_value=match_row.ft_raw),
            ht_raw=_fv("ht_raw", match_row.ht_raw),
            halftime_home_goals=_fv(
                "halftime_home_goals", match_row.halftime_home_goals, derived=True, raw_value=match_row.ht_raw
            ),
            halftime_away_goals=_fv(
                "halftime_away_goals", match_row.halftime_away_goals, derived=True, raw_value=match_row.ht_raw
            ),
            total_goals=_fv("total_goals", match_row.total_goals, raw_value=match_row.total_goals_raw),
            over_2_5_raw=_fv("over_2_5_raw", match_row.over_2_5_raw),
            over_2_5=_fv("over_2_5", match_row.over_2_5, derived=True, raw_value=match_row.over_2_5_raw),
            bts_raw=_fv("bts_raw", match_row.bts_raw),
            btts=_fv("btts", match_row.btts, derived=True, raw_value=match_row.bts_raw),
            h2h_url=_fv("h2h_url", match_row.h2h_url),
            provenance=provenance,
        )


SoccerStatsRepository = SoccerStatsMatchRepository
