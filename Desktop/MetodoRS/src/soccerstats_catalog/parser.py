"""Parser del catálogo de ligas de SoccerSTATS.

Extrae la tabla principal de https://www.soccerstats.com/leagues.asp
con código, nombre, URL canónica y estadísticas de la tabla.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .models import (
    BLOCKED,
    EMPTY,
    EXTRACTED,
    PARSE_ERROR,
    CatalogExtraction,
    LeagueEntry,
    empty_extraction,
)

BASE_URL = "https://www.soccerstats.com"


def _detect_blocked(html: str) -> bool:
    """Detecta si la respuesta es un challenge de Cloudflare o página de bloqueo."""
    low = html.lower()
    signals = (
        "just a moment",
        "un momento",
        "cf-browser-verification",
        "attention required",
        "cloudflare",
        "challenges.cloudflare.com",
        "cf-chl-widget",
        "enable javascript and cookies",
        "verificación de seguridad",
        "checking your browser",
        "403 forbidden",
    )
    # Si contiene señales de Cloudflare y NO contiene el enlace típico de liga, está bloqueado.
    # El límite de longitud de 5000 era insuficiente (el desafío Turnstile pesa ~29KB).
    is_blocked = any(s in low for s in signals)
    has_content = "latest.asp?league=" in low
    return is_blocked and not has_content


def _parse_stats_cell(text: str) -> str | None:
    """Limpia y normaliza una celda de estadísticas."""
    if not text:
        return None
    t = text.strip()
    # Remove % from percentages for cleaner storage
    if t.endswith("%"):
        t = t[:-1]
    return t or None


def _clean_league_name(raw_name: str) -> str:
    """Limpia el nombre de la liga extraído del HTML."""
    # Replace NBSP and normalize whitespace
    name = raw_name.replace("\xa0", " ").strip()
    # Collapse multiple spaces
    name = re.sub(r"\s+", " ", name)
    # Fix "A - League" -> "A-League" and similar
    name = re.sub(r"\s*-\s*", " - ", name)
    return name


def parse_league_catalog(html: str, source_url: str = BASE_URL + "/leagues.asp") -> CatalogExtraction:
    """Parsea el HTML del catálogo de ligas y devuelve una CatalogExtraction.

    Args:
        html: HTML de la página leagues.asp
        source_url: URL de origen para trazabilidad

    Returns:
        CatalogExtraction con las ligas parseadas
    """
    # Detectar bloqueo
    if _detect_blocked(html):
        return CatalogExtraction(
            source_url=source_url,
            status=BLOCKED,
            leagues=(),
            error="Cloudflare challenge or blocked page detected",
        )

    soup = BeautifulSoup(html, "html.parser")

    # Buscar la tabla principal de ligas
    tables = soup.find_all("table")
    league_table = None

    for table in tables:
        # La tabla tiene headers específicos
        headers = table.find_all("th")
        header_texts = [h.get_text(strip=True) for h in headers]
        if "Leagues" in header_texts and "GP" in header_texts and "Home W." in header_texts:
            league_table = table
            break

    if not league_table:
        # Fallback: buscar tabla con muchos enlaces a latest.asp?league=
        for table in tables:
            links = table.find_all("a", href=re.compile(r"latest\.asp\?league="))
            if len(links) > 50:
                league_table = table
                break

    if not league_table:
        return CatalogExtraction(
            source_url=source_url,
            status=PARSE_ERROR,
            leagues=(),
            error="No league table found in HTML",
        )

    leagues: list[LeagueEntry] = []

    for row in league_table.find_all("tr"):
        # Use recursive=False to get only direct td/th children, not nested table cells
        cells = row.find_all(["td", "th"], recursive=False)
        # Skip header row (contains "Leagues", "GP", etc.)
        if not cells or len(cells) < 14:
            continue

        # Check if this is the header row
        first_cell_text = cells[0].get_text(strip=True)
        if first_cell_text == "Leagues" or first_cell_text == "":
            continue

        # Cell 0: League name (with flag image, etc.)
        # Cell 1: "stats" link with league code
        # Cell 2: empty
        # Cells 3-13: 11 stats columns

        # Extract league code from stats link in cell 1
        league_code = None
        stats_link = cells[1].find("a", href=True) if len(cells) > 1 else None
        if stats_link:
            href = stats_link.get("href", "")
            m = re.search(r"latest\.asp\?league=([a-z0-9_]+)", href, re.I)
            if m:
                league_code = m.group(1)

        if not league_code:
            # Try finding in any cell
            for cell in cells:
                for link in cell.find_all("a", href=True):
                    href = link.get("href", "")
                    if "latest.asp?league=" in href:
                        m = re.search(r"latest\.asp\?league=([a-z0-9_]+)", href, re.I)
                        if m:
                            league_code = m.group(1)
                            break
                if league_code:
                    break

        if not league_code:
            continue

        # Extract league name from cell 0
        raw_name = cells[0].get_text(" ", strip=True)
        name = _clean_league_name(raw_name)

        # Extract stats from cells 3-13 (indices 3 to 13 inclusive = 11 cells)
        stats = []
        for i in range(3, min(14, len(cells))):
            stats.append(cells[i].get_text(strip=True))

        # Parse stats
        gp = hw = d = aw = avg_goals = hg = ag = o15 = o25 = o35 = btts = None
        if len(stats) >= 11:
            gp = _parse_stats_cell(stats[0])
            hw = _parse_stats_cell(stats[1])
            d = _parse_stats_cell(stats[2])
            aw = _parse_stats_cell(stats[3])
            avg_goals = _parse_stats_cell(stats[4])
            hg = _parse_stats_cell(stats[5])
            ag = _parse_stats_cell(stats[6])
            o15 = _parse_stats_cell(stats[7])
            o25 = _parse_stats_cell(stats[8])
            o35 = _parse_stats_cell(stats[9])
            btts = _parse_stats_cell(stats[10])

        # Build canonical URL
        canonical_url = f"{BASE_URL}/latest.asp?league={league_code}"
        relative_url = f"latest.asp?league={league_code}"

        league = LeagueEntry(
            name=name,
            canonical_url=canonical_url,
            relative_url=relative_url,
            gp=gp,
            hw=hw,
            d=d,
            aw=aw,
            avg_goals=avg_goals,
            hg=hg,
            ag=ag,
            o15=o15,
            o25=o25,
            o35=o35,
            btts=btts,
            source_section="main_table",
            extraction_status=EXTRACTED,
        )
        leagues.append(league)

    if not leagues:
        return empty_extraction(source_url, status=EMPTY, error="No leagues extracted from table")

    return CatalogExtraction(
        source_url=source_url,
        status=EXTRACTED,
        leagues=tuple(leagues),
        source_section="main_table",
    )


# Alias for backwards compatibility
extract_leagues = parse_league_catalog