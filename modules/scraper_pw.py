"""
Playwright-based scrapers dla portali SPA:
  - Pracuj.pl (dehydratedState + __NEXT_DATA__)

JustJoinIT i NoFluffJobs przeniesione do scraper.py (REST API, bez przeglądarki).
"""

import json
import logging
import re
import time

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Browser, BrowserContext

# Wspólne helpery – importowane z scraper.py (jedno źródło prawdy)
from modules.scraper import _matches_role, _matches_location, _salary_in_range

logger = logging.getLogger(__name__)


def _make_browser(playwright) -> tuple[Browser, BrowserContext]:
    browser = playwright.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    )
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="pl-PL",
        viewport={"width": 1280, "height": 900},
        java_script_enabled=True,
    )
    # Ukryj flagę webdriver (anti-bot)
    ctx.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return browser, ctx

# ── Pracuj.pl ──────────────────────────────────────────────────────────────────

PRACUJ_QUERIES = [
    "product owner",
    "chapter lead",
    "it manager",
    "engineering manager",
    "product manager",
    "it operations manager",
    "service delivery manager",
]


def _pracuj_dehydrated(pp: dict) -> list[dict]:
    """Wyciąga groupedOffers z dehydratedState (React Query)."""
    queries = pp.get("dehydratedState", {}).get("queries", [])
    for q in queries:
        data = q.get("state", {}).get("data", {})
        if isinstance(data, dict) and "groupedOffers" in data:
            return data["groupedOffers"]
    return []


def _pracuj_parse_salary(sal_text: str) -> tuple[str, int]:
    if not sal_text:
        return "", 0
    nums = re.findall(r"\d[\d\s]*\d|\d+", sal_text.replace("\xa0", " ").replace(" ", ""))
    sal_from = 0
    if nums:
        try:
            sal_from = int(nums[0].replace(" ", ""))
        except ValueError:
            pass
    return sal_text, sal_from


_PRACUJ_WORK_REMOTE = {"praca zdalna", "remote", "fully remote"}
_PRACUJ_WORK_HYBRID = {"praca hybrydowa", "hybrid"}
_PRACUJ_WORK_ONSITE = {"praca stacjonarna", "on-site", "stacjonar"}


def _pracuj_location(group: dict) -> str:
    """
    Buduje string lokalizacji z workModes + displayWorkplace sub-ofert.
    Przykłady: 'Warszawa (zdalna)', 'Kraków, Łódź (hybrydowa)', 'zdalna'
    """
    work_modes = [m.lower() for m in (group.get("workModes") or [])]
    sub_offers  = group.get("offers") or []

    is_remote = any(any(kw in m for kw in _PRACUJ_WORK_REMOTE) for m in work_modes)
    is_hybrid = any(any(kw in m for kw in _PRACUJ_WORK_HYBRID) for m in work_modes)
    is_whole_pl = any(o.get("isWholePoland") for o in sub_offers)

    # Zbierz unikalne miasta z displayWorkplace
    cities: list[str] = []
    seen_c: set[str] = set()
    for o in sub_offers:
        wp = o.get("displayWorkplace") or ""
        city = wp.split(",")[0].strip() if wp else ""
        if city and city.lower() not in seen_c:
            seen_c.add(city.lower())
            cities.append(city)

    city_str = ", ".join(cities[:3]) if cities else ""

    if is_remote and not cities:
        return "zdalna"
    if is_remote:
        return f"{city_str} (zdalna)"
    if is_hybrid:
        return f"{city_str} (hybrydowa)" if city_str else "hybrydowa"
    if is_whole_pl:
        return "Polska (zdalna)"
    return city_str or "Polska"


def _pracuj_location_ok(group: dict) -> bool:
    """Czy oferta pasuje do preferencji lokalizacyjnych kandydata?"""
    work_modes = [m.lower() for m in (group.get("workModes") or [])]
    is_remote   = any(any(kw in m for kw in _PRACUJ_WORK_REMOTE) for m in work_modes)
    is_hybrid   = any(any(kw in m for kw in _PRACUJ_WORK_HYBRID) for m in work_modes)
    is_onsite   = any(any(kw in m for kw in _PRACUJ_WORK_ONSITE) for m in work_modes)

    if is_remote:
        return True
    if is_onsite and not is_hybrid:
        return False

    # Hybrid lub brak info – sprawdź czy miasto jest w target cities
    loc_str = _pracuj_location(group)
    return _matches_location(loc_str)


def scrape_pracuj() -> list[dict]:
    from modules.scraper import _is_recent, _clean_html_description

    all_results: list[dict] = []
    seen_urls:   set[str]   = set()

    with sync_playwright() as pw:
        browser, ctx = _make_browser(pw)
        page = ctx.new_page()

        for query in PRACUJ_QUERIES:
            query_enc = query.replace(" ", "%20")
            url = f"https://www.pracuj.pl/praca/{query_enc};kw?its=big-cities,remote&rdth=7"
            try:
                page.goto(url, timeout=40_000, wait_until="domcontentloaded")
                page.wait_for_timeout(3_000)
            except Exception as e:
                logger.warning(f"[Pracuj.pl] goto błąd dla '{query}': {e}")
                continue

            html = page.content()
            soup = BeautifulSoup(html, "html.parser")
            script = soup.find("script", id="__NEXT_DATA__")
            if not script:
                continue

            try:
                data = json.loads(script.string)
                pp   = data.get("props", {}).get("pageProps", {})
            except Exception:
                continue

            grouped = _pracuj_dehydrated(pp)
            logger.debug(f"[Pracuj.pl] '{query}': {len(grouped)} grup")

            for group in grouped:
                title = group.get("jobTitle", "")
                if not title or not _matches_role(title):
                    continue

                # Filtr dat
                if not _is_recent(group.get("lastPublicated")):
                    continue

                # Filtr lokalizacji
                if not _pracuj_location_ok(group):
                    continue

                company   = group.get("companyName", "")
                location  = _pracuj_location(group)

                # URL z pierwszej sub-oferty
                sub_offers = group.get("offers") or []
                offer_url  = ""
                if sub_offers:
                    offer_url = (sub_offers[0].get("offerAbsoluteUri") or
                                 sub_offers[0].get("offerUrl") or "")
                if not offer_url:
                    offer_url = group.get("offerAbsoluteUri") or group.get("offerUrl") or ""
                if not offer_url or offer_url in seen_urls:
                    continue

                # Salary (rzadkie na Pracuj.pl – brak = normalne)
                sal_raw = group.get("salaryDisplayText") or ""
                sal_str, sal_from = _pracuj_parse_salary(sal_raw)
                if sal_from and not _salary_in_range(sal_from):
                    continue

                # Opis (skrócony preview z listy)
                desc_html = group.get("jobDescription") or ""
                desc = _clean_html_description(desc_html) if desc_html else ""

                seen_urls.add(offer_url)
                all_results.append({
                    "source":          "Pracuj.pl",
                    "title":           title,
                    "company":         company,
                    "location":        location,
                    "salary":          sal_str,
                    "salary_from":     sal_from,
                    "salary_contract": (group.get("typesOfContract") or [""])[0],
                    "url":             offer_url,
                    "skills":          [],
                    "description":     desc,
                })

            time.sleep(1.5)

        browser.close()

    logger.info(f"[Pracuj.pl] {len(all_results)} ofert")
    return all_results
