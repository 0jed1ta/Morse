"""Cliente HTTP para SoccerSTATS con manejo de Cloudflare y caché.
"""

from __future__ import annotations

import hashlib
import os
import pickle
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

from .models import CatalogExtraction
from .parser import parse_league_catalog

# Configuración
SOCCERSTATS_BASE = "https://www.soccerstats.com"
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}

REQUEST_DELAY = 1.5
CACHE_TTL = 86400

_default_fetcher: "SoccerStatsFetcher | None" = None

class FetchError(Exception):
    """Error al descargar una página."""

class SoccerStatsFetcher:
    def __init__(self, engine: str | None = None):
        self.engine = engine or "auto"
        self.session = requests.Session()
        self.session.headers.update(BROWSER_HEADERS)
        self._cookie_path = DATA_DIR / "cookies.pkl"
        self._warmed = False
        self._last_request = 0.0
        self._load_cookies()

    def _load_cookies(self):
        try:
            with open(self._cookie_path, "rb") as fh:
                self.session.cookies.update(pickle.load(fh))
                self._warmed = True
        except (OSError, pickle.PickleError):
            pass

    def _save_cookies(self):
        try:
            with open(self._cookie_path, "wb") as fh:
                pickle.dump(self.session.cookies, fh)
        except OSError:
            pass

    @staticmethod
    def _cache_path(url: str) -> Path:
        key = hashlib.sha256(url.encode()).hexdigest()[:24]
        return CACHE_DIR / (key + ".html")

    def _read_cache(self, url: str, ttl: int) -> str | None:
        path = self._cache_path(url)
        if not path.exists():
            return None
        if ttl >= 0 and (time.time() - path.stat().st_mtime) > ttl:
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def _write_cache(self, url: str, html: str):
        try:
            self._cache_path(url).write_text(html, encoding="utf-8")
        except OSError:
            pass

    def _throttle(self):
        elapsed = time.time() - self._last_request
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        self._last_request = time.time()

    @staticmethod
    def _looks_blocked(status: int, html: str) -> bool:
        if status in (403, 429, 503) or status >= 500:
            return True
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
        )
        # Si tiene señales de CF y no tiene el enlace de ligas, asumimos bloqueo.
        # Quitamos restricción de longitud por los ~29KB de Turnstile.
        return any(s in low for s in signals) and "latest.asp?league=" not in low

    def get(self, path: str, ttl: int | None = None, force: bool = False) -> str:
        url = path if path.startswith("http") else urljoin(SOCCERSTATS_BASE + "/", path)
        ttl = CACHE_TTL if ttl is None else ttl
        if not force:
            cached = self._read_cache(url, ttl)
            if cached is not None:
                return cached
        html = self._fetch_live(url)
        self._write_cache(url, html)
        return html

    def _fetch_live(self, url: str) -> str:
        if self.engine in ("auto", "requests"):
            try:
                return self._fetch_requests(url)
            except FetchError:
                if self.engine == "requests":
                    raise
        return self._fetch_playwright(url)

    def _fetch_requests(self, url: str) -> str:
        if not self._warmed and not url.rstrip("/").endswith("soccerstats.com"):
            self._warmup()
        self._throttle()
        try:
            resp = self.session.get(url, timeout=25)
        except requests.RequestException as exc:
            raise FetchError(f"Error de red: {exc}")
        html = resp.text
        if self._looks_blocked(resp.status_code, html):
            self._warmup(force=True)
            self._throttle()
            try:
                resp = self.session.get(url, timeout=25)
                html = resp.text
            except requests.RequestException as exc:
                raise FetchError(f"Error de red: {exc}")
            if self._looks_blocked(resp.status_code, html):
                raise FetchError(f"Bloqueado por Cloudflare (HTTP {resp.status_code})")
        self._save_cookies()
        return html

    def _warmup(self, force: bool = False):
        if self._warmed and not force:
            return
        self._throttle()
        try:
            self.session.get(SOCCERSTATS_BASE + "/", timeout=25)
            self._warmed = True
            self._save_cookies()
        except requests.RequestException:
            pass

    def _fetch_playwright(self, url: str) -> str:
        """Fetch usando Playwright con perfil persistente para evadir Cloudflare."""
        from playwright.sync_api import sync_playwright
        import time

        profile_dir = DATA_DIR / "chrome_profile"
        profile_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(profile_dir.resolve()),
                executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--window-position=-2400,-2400",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

            start = time.time()
            timeout = 15
            success = False
            while time.time() - start < timeout:
                html = page.content()
                if "latest.asp?league=" in html:
                    success = True
                    break
                time.sleep(0.5)

            if not success:
                html = page.content()
                ctx.close()
                if self._looks_blocked(200, html):
                    raise FetchError("Playwright bloqueado por Cloudflare (timeout esperando tabla).")
                return html

            html = page.content()
            ctx.close()
        return html

def get_fetcher() -> SoccerStatsFetcher:
    global _default_fetcher
    if _default_fetcher is None:
        _default_fetcher = SoccerStatsFetcher()
    return _default_fetcher

def fetch_league_catalog(source_url: str = SOCCERSTATS_BASE + "/leagues.asp", ttl: int | None = None, force: bool = False) -> CatalogExtraction:
    fetcher = get_fetcher()
    html = fetcher.get(source_url, ttl=ttl, force=force)
    return parse_league_catalog(html, source_url)
