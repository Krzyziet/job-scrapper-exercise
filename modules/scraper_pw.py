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


def scrape_pracuj() -> list[dict]:
    all_results: list[dict] = []
    seen_urls: set[str] = set()

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
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            script = soup.find("script", id="__NEXT_DATA__")

            if not script:
                continue

            try:
                data = json.loads(script.string)
                pp = data.get("props", {}).get("pageProps", {})
            except Exception:
                continue

            # Pobierz groupedOffers z dehydratedState
            grouped = _pracuj_dehydrated(pp)
            logger.debug(f"[Pracuj.pl] '{query}': {len(grouped)} grup")

            for group in grouped:
                title = group.get("jobTitle", "")
                if not title or not _matches_role(title):
                    continue

                company = group.get("companyName", "")

                # URL: z sub-ofert lub z DOM
                sub_offers = group.get("offers", [])
                offer_url = ""
                city = ""
                sal_str = ""
                sal_from = 0

                if sub_offers:
                    first = sub_offers[0]
                    offer_url = (first.get("offerAbsoluteUri") or
                                 first.get("offerUrl") or "")
                    city = (first.get("jobCity") or
                            first.get("city") or
                            group.get("jobCity") or "")
                    sal_raw = (first.get("salaryDisplayText") or
                               first.get("salary") or "")
                    sal_str, sal_from = _pracuj_parse_salary(sal_raw)
                else:
                    # Dane bezpośrednio w grupie
                    offer_url = group.get("offerAbsoluteUri") or group.get("offerUrl") or ""
                    city = group.get("jobCity") or group.get("city") or ""
                    sal_raw = group.get("salaryDisplayText") or ""
                    sal_str, sal_from = _pracuj_parse_salary(sal_raw)

                if not offer_url:
                    continue
                if offer_url in seen_urls:
                    continue

                # Filtr lokalizacji – Pracuj.pl używa polskich nazw miast
                city_check = city.lower()
                location_ok = (
                    not city_check  # brak miasta = pewnie zdalnie
                    or _matches_location(city_check)
                    or "remote" in city_check
                    or "zdaln" in city_check
                )
                if not location_ok:
                    continue

                if sal_from and not _salary_in_range(sal_from):
                    continue

                seen_urls.add(offer_url)
                all_results.append({
                    "source": "Pracuj.pl",
                    "title": title,
                    "company": company,
                    "location": city or "Polska",
                    "salary": sal_str,
                    "salary_from": sal_from,
                    "url": offer_url,
                    "skills": [],
                    "description": "",
                })

            time.sleep(1.5)

        browser.close()

    logger.info(f"[Pracuj.pl] {len(all_results)} ofert")
    return all_results
