"""
Playwright-based scrapers dla portali SPA:
  - NoFluffJobs (DOM + przechwyt API)
  - Pracuj.pl   (dehydratedState + __NEXT_DATA__)

JustJoinIT przeniesiony do scraper.py (czysty REST API, bez przeglądarki).
"""

import json
import logging
import re
import time

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Browser, BrowserContext

logger = logging.getLogger(__name__)

ROLES = [
    "Product Owner", "Technical Product Owner", "Chapter Lead",
    "IT Manager", "Engineering Manager", "Product Manager",
    "Project Manager", "IT Operations Manager",
    "Service Delivery Manager", "NOC Manager",
]

_REMOTE_KW = {
    "remote", "zdalna", "zdalnie", "zdaln",
    "cała polska", "cala polska", "poland", "fully remote", "praca zdalna",
}
_HYBRID_KW = {"hybrid", "hybrydow", "hybryd"}
_TARGET_CITIES = {
    "łódź", "lodz", "lódź",
    "warszawa", "warsaw",
    "gdańsk", "gdansk",
    "gdynia",
    "sopot",
    "trojmiasto", "trójmiasto",
}

SALARY_MIN_UOP = 21_000
SALARY_MIN_B2B = 23_000

_JJIT_NOISE = {
    "super offer", "new", "featured", "remote", "hybrid", "b2b", "uop",
    "permanent", "contract", "locations", "undisclosed salary", "apply",
}


def _matches_role(title: str) -> bool:
    t = title.lower()
    return any(r.lower() in t for r in ROLES)


def _matches_location(loc: str) -> bool:
    if not loc:
        return True
    lo = loc.lower()
    if any(kw in lo for kw in {"stacjonar", "on-site", "onsite", "in-office", "on site"}):
        return False
    if any(kw in lo for kw in _REMOTE_KW):
        return True
    if any(kw in lo for kw in _HYBRID_KW):
        return any(city in lo for city in _TARGET_CITIES)
    return True


def _salary_ok(salary_from: int, contract: str = "") -> bool:
    if salary_from <= 0:
        return True
    b2b = "b2b" in contract.lower()
    return salary_from >= (SALARY_MIN_B2B if b2b else SALARY_MIN_UOP)


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



# ── NoFluffJobs ─────────────────────────────────────────────────────────────────

NFJ_SEARCH_PAGES = [
    "https://nofluffjobs.com/pl/praca-zdalna/product-owner",
    "https://nofluffjobs.com/pl/praca-zdalna/project-manager",
    "https://nofluffjobs.com/pl/praca-zdalna/it-manager",
    "https://nofluffjobs.com/pl/praca-zdalna/engineering-manager",
    "https://nofluffjobs.com/pl/praca-zdalna/chapter-lead",
]


def scrape_nofluffjobs() -> list[dict]:
    all_results: list[dict] = []
    seen_urls: set[str] = set()

    with sync_playwright() as pw:
        browser, ctx = _make_browser(pw)
        page = ctx.new_page()

        # Przechwytuj API jeśli dostępne
        captured: list[dict] = []

        def on_response(resp):
            if resp.status == 200 and "nofluffjobs" in resp.url and "/api/search/posting" in resp.url:
                try:
                    captured.append(resp.json())
                except Exception:
                    pass

        page.on("response", on_response)

        for search_url in NFJ_SEARCH_PAGES:
            captured.clear()
            try:
                page.goto(search_url, timeout=30_000, wait_until="domcontentloaded")
                page.wait_for_timeout(4_000)
            except Exception as e:
                logger.warning(f"[NoFluffJobs] goto błąd: {e}")
                continue

            # Priorytet: przechwycone API
            api_used = False
            for resp_data in captured:
                postings = (resp_data.get("postings") or
                            resp_data.get("data") or
                            resp_data.get("items") or [])
                if not postings:
                    continue
                api_used = True
                for p in postings:
                    title = p.get("title") or p.get("position") or ""
                    if not _matches_role(title):
                        continue
                    slug = p.get("url") or p.get("slug") or p.get("id") or ""
                    offer_url = f"https://nofluffjobs.com/pl/job/{slug}"
                    if offer_url in seen_urls:
                        continue
                    seen_urls.add(offer_url)
                    location = p.get("location") or {}
                    places = location.get("places", []) if isinstance(location, dict) else []
                    city = places[0].get("city", "") if places else ""
                    remote = p.get("remote") or p.get("fullyRemote") or False
                    loc_str = f"{city} {'remote' if remote else ''}".strip() or "remote"
                    sal = p.get("salary") or {}
                    sal_from = sal.get("from") or 0
                    sal_to = sal.get("to") or 0
                    cur = sal.get("currency", "PLN")
                    period = sal.get("period", "month")
                    sal_str = f"{sal_from:,}–{sal_to:,} {cur}/{period}" if sal_from else ""
                    all_results.append({
                        "source": "NoFluffJobs",
                        "title": title,
                        "company": p.get("name") or (p.get("company") or {}).get("name") or "",
                        "location": loc_str,
                        "salary": sal_str,
                        "salary_from": sal_from,
                        "url": offer_url,
                        "skills": p.get("technology") or p.get("skills") or [],
                        "description": "",
                    })

            # Fallback DOM – parsowanie kart
            if not api_used:
                try:
                    page.wait_for_selector(
                        "a[href*='/pl/job/']", timeout=8_000
                    )
                    from bs4 import BeautifulSoup
                    html = page.content()
                    soup = BeautifulSoup(html, "html.parser")

                    for link in soup.find_all("a", href=re.compile(r"/pl/job/")):
                        href = link.get("href", "")
                        offer_url = (f"https://nofluffjobs.com{href}"
                                     if not href.startswith("http") else href)
                        if offer_url in seen_urls:
                            continue

                        # Szukamy tytułu wewnątrz karty
                        title_el = link.find(["h3", "h2", "span"],
                                             class_=re.compile(r"title|name|posting", re.I))
                        if not title_el:
                            title_el = link.find(["h3", "h2"])
                        if not title_el:
                            continue
                        title = title_el.get_text(strip=True)
                        # Odfiltruj "NOWA" suffix
                        title = re.sub(r"\s*NOWA\s*$", "", title).strip()
                        if not _matches_role(title):
                            continue

                        # Szukamy nazwy firmy w karcie (zwykle p lub span po tytule)
                        company_el = link.find(
                            ["p", "span"],
                            class_=re.compile(r"company|employer|firm", re.I),
                        )
                        company = company_el.get_text(strip=True) if company_el else ""

                        seen_urls.add(offer_url)
                        all_results.append({
                            "source": "NoFluffJobs",
                            "title": title,
                            "company": company,
                            "location": "remote",
                            "salary": "",
                            "salary_from": 0,
                            "url": offer_url,
                            "skills": [],
                            "description": "",
                        })
                except Exception as e:
                    logger.debug(f"[NoFluffJobs] DOM fallback błąd: {e}")

            time.sleep(1)

        browser.close()

    logger.info(f"[NoFluffJobs] {len(all_results)} ofert")
    return all_results


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

                if sal_from and not _salary_ok(sal_from):
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
