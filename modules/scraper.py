"""
Scraper ofert pracy z portali:
  1. JustJoinIT       – publiczne API v1 (zwraca wszystkie oferty)
  2. NoFluffJobs       – scraping HTML + __NEXT_DATA__
  3. LinkedIn          – guest API (nie wymaga logowania)
  4. Pracuj.pl         – session + __NEXT_DATA__ JSON w HTML
  5. Bulldogjob        – __NEXT_DATA__ JSON w HTML
  6. TheProtocol       – __NEXT_DATA__ JSON w HTML
  7. RemoteOK          – publiczne JSON API (oferty zdalne, wynagrodzenie USD)
  8. WeWorkRemotely    – RSS feed (oferty zdalne, wynagrodzenie USD)
  9. Himalayas         – __NEXT_DATA__ / DOM (oferty zdalne, wynagrodzenie USD)
"""

import re
import time
import logging
import requests
import json as _json
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Konfiguracja targetów ──────────────────────────────────────────────────────

ROLES = [
    "Product Owner",
    "Technical Product Owner",
    "Chapter Lead",
    "IT Manager",
    "Engineering Manager",
    "Product Manager",
    "Project Manager",
    "IT Operations Manager",
    "Service Delivery Manager",
    "NOC Manager",
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

SALARY_MIN_UOP = 23000
SALARY_MIN_B2B = 25000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    # Celowo bez "br" – requests nie obsługuje brotli bez pakietu brotlipy
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

# ── Pomocnicze ─────────────────────────────────────────────────────────────────

def _matches_role(title: str) -> bool:
    t = title.lower()
    return any(role.lower() in t for role in ROLES)


def _matches_location(location: str) -> bool:
    """
    Akceptuje ofertę jeśli:
    - remote (dowolny kraj/miasto), LUB
    - hybrid w Łódź / Warszawa / Gdańsk, LUB
    - brak trybu (portal już filtrował remote – dajemy benefit of doubt).
    Odrzuca: jawnie stacjonarne, hybrid poza docelowymi miastami.
    """
    if not location:
        return True
    loc = location.lower()
    # Jawnie stacjonarne – odrzuć niezależnie od miasta
    if any(kw in loc for kw in {"stacjonar", "on-site", "onsite", "in-office", "on site"}):
        return False
    # Remote – zawsze OK (dowolny kraj)
    if any(kw in loc for kw in _REMOTE_KW):
        return True
    # Hybrid – tylko Łódź / Warszawa / Gdańsk
    if any(kw in loc for kw in _HYBRID_KW):
        return any(city in loc for city in _TARGET_CITIES)
    # Samo miasto / kraj bez trybu – akceptuj (portal już filtrował remote)
    return True


def _salary_in_range(salary_from: int, contract_type: str = "") -> bool:
    """Zwraca True jeśli widełki mieszczą się w wymaganiach (lub brak danych)."""
    if salary_from <= 0:
        return True  # brak widełek – nie odrzucamy; salary prediction zadba o resztę
    b2b = "b2b" in contract_type.lower()
    if b2b:
        return salary_from >= SALARY_MIN_B2B - 2000
    return salary_from >= SALARY_MIN_UOP - 2000


def _get(url: str, params: dict = None, timeout: int = 15,
         session: requests.Session = None) -> requests.Response | None:
    try:
        requester = session or requests
        r = requester.get(url, params=params, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:
        logger.warning(f"GET {url} – błąd: {e}")
        return None


def _make_session(base_url: str) -> requests.Session:
    """Tworzy sesję z cookies przez odwiedzenie strony głównej (omija część 403)."""
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        session.get(base_url, timeout=10)
    except Exception:
        pass
    return session


# ── 1. JustJoinIT ──────────────────────────────────────────────────────────────
# Oba publiczne API (v1 /api/offers, v2 api.justjoin.it) zwracają 404.
# Scraping strony Next.js – parsujemy __NEXT_DATA__ lub karty HTML.

JJIT_BASE = "https://justjoin.it"
JJIT_SEARCH_ROLES = [
    "product-owner",
    "project-manager",
    "it-manager",
    "engineering-manager",
]


def _jjit_salary_str(emp_types: list) -> tuple[str, int, str]:
    if not emp_types:
        return "", 0, ""
    parts, best_from, best_type = [], 0, ""
    for e in emp_types:
        s = e.get("salary") or e.get("salaryRange") or {}
        if not s:
            continue
        lo = s.get("from") or s.get("min") or 0
        hi = s.get("to") or s.get("max") or 0
        cur = s.get("currency", "PLN")
        typ = e.get("type", "") or e.get("employmentType", "")
        parts.append(f"{lo:,}–{hi:,} {cur} ({typ})")
        if lo > best_from:
            best_from, best_type = lo, typ
    return ", ".join(parts), best_from, best_type


def _jjit_parse_next_data(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script:
        return []
    try:
        data = _json.loads(script.string)
        pp = data.get("props", {}).get("pageProps", {})
        # Różne klucze w zależności od wersji strony
        return (
            pp.get("dehydratedState", {})
              .get("queries", [{}])[0]
              .get("state", {}).get("data", {}).get("data", [])
            or pp.get("offers", [])
            or pp.get("jobOffers", [])
            or pp.get("data", {}).get("offers", [])
        )
    except Exception as e:
        logger.debug(f"[JustJoinIT] __NEXT_DATA__ błąd: {e}")
        return []


def _jjit_parse_html_cards(html: str) -> list[dict]:
    """Fallback – parsuje karty ofert z HTML gdy brak __NEXT_DATA__."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    # JustJoinIT używa data-testid lub klas z "offer"
    for card in soup.select("[data-testid*='offer-list-item'], article, a[href*='/job-offer/']"):
        title_el = card.find(["h2", "h3", "h4"])
        link_el = card if card.name == "a" else card.find("a", href=re.compile(r"/job-offer/|/offers/"))
        if not title_el or not link_el:
            continue
        title = title_el.get_text(strip=True)
        href = link_el.get("href", "")
        if href and not href.startswith("http"):
            href = f"{JJIT_BASE}{href}"
        company_el = card.find(attrs={"data-testid": re.compile("company|employer", re.I)})
        results.append({
            "title": title,
            "company": company_el.get_text(strip=True) if company_el else "",
            "url": href,
        })
    return results


def scrape_justjoinit() -> list[dict]:
    session = _make_session(JJIT_BASE)
    results = []
    seen = set()

    search_urls = [
        f"{JJIT_BASE}/job-offers?keyword={role}&remote=true" for role in JJIT_SEARCH_ROLES
    ] + [
        f"{JJIT_BASE}/job-offers?keyword={role}&city=lodz" for role in JJIT_SEARCH_ROLES
    ]

    for url in search_urls:
        r = _get(url, session=session)
        if not r:
            time.sleep(0.5)
            continue

        raw_offers = _jjit_parse_next_data(r.text)

        if raw_offers:
            for o in raw_offers:
                title = o.get("title", "") or o.get("position", "")
                if not _matches_role(title):
                    continue
                city = o.get("city", "") or o.get("location", {}).get("city", "") or ""
                workplace = (o.get("workplaceType", "")
                             or o.get("workplace_type", "")
                             or o.get("remoteInterview", ""))
                loc_str = f"{city} {workplace}".strip()
                if not _matches_location(loc_str):
                    continue
                emp_types = (o.get("employmentTypes", [])
                             or o.get("employment_types", [])
                             or o.get("salaryRanges", []))
                salary_str, salary_from, salary_type = _jjit_salary_str(emp_types)
                if salary_from and not _salary_in_range(salary_from, salary_type):
                    continue
                slug = o.get("slug", "") or o.get("id", "")
                offer_url = o.get("url", "") or f"{JJIT_BASE}/job-offer/{slug}"
                if offer_url in seen:
                    continue
                seen.add(offer_url)
                results.append({
                    "source": "JustJoinIT",
                    "title": title,
                    "company": o.get("companyName", "") or o.get("company_name", "") or (o.get("company") or {}).get("name", ""),
                    "location": loc_str or "remote",
                    "salary": salary_str,
                    "salary_from": salary_from,
                    "url": offer_url,
                    "skills": [s.get("name", "") for s in (o.get("requiredSkills", []) or o.get("skills", []))],
                    "description": o.get("body", "") or o.get("description", "") or "",
                })
        else:
            # HTML fallback
            for card in _jjit_parse_html_cards(r.text):
                title = card.get("title", "")
                if not _matches_role(title) or card.get("url") in seen:
                    continue
                seen.add(card["url"])
                results.append({
                    "source": "JustJoinIT",
                    "title": title,
                    "company": card.get("company", ""),
                    "location": "remote",
                    "salary": "",
                    "salary_from": 0,
                    "url": card["url"],
                    "skills": [],
                    "description": "",
                })
        time.sleep(1)

    logger.info(f"[JustJoinIT] {len(results)} ofert")
    return results


# ── 2. NoFluffJobs ─────────────────────────────────────────────────────────────
# API /api/posting zwraca 405 – używamy scraping HTML z __NEXT_DATA__.
# URL: https://nofluffjobs.com/pl?criteria=requirement%3D{role}+remote

NFJ_SEARCH = "https://nofluffjobs.com/pl"


def _nfj_salary_str(p: dict) -> tuple[str, int, str]:
    s = p.get("salary") or {}
    if not s:
        return "", 0, ""
    lo = s.get("from") or 0
    hi = s.get("to") or 0
    cur = s.get("currency", "PLN")
    typ = s.get("type", "")
    period = s.get("period", "")
    return f"{lo:,}–{hi:,} {cur}/{period} ({typ})", lo, typ


def _nfj_parse_next_data(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script:
        return []
    try:
        data = _json.loads(script.string)
        # Struktura zależy od wersji NFJ – szukamy postings w różnych miejscach
        page_props = data.get("props", {}).get("pageProps", {})
        return (
            page_props.get("dehydratedState", {})
                      .get("queries", [{}])[0]
                      .get("state", {})
                      .get("data", {})
                      .get("postings", [])
            or page_props.get("postings", [])
            or page_props.get("jobs", [])
        )
    except Exception as e:
        logger.warning(f"[NoFluffJobs] błąd __NEXT_DATA__: {e}")
        return []


def scrape_nofluffjobs() -> list[dict]:
    results = []
    session = _make_session("https://nofluffjobs.com")

    # Próbujemy kilka formatów URL – NFJ zmieniało strukturę
    search_urls = [
        "https://nofluffjobs.com/pl/praca-zdalna/product-owner",
        "https://nofluffjobs.com/pl/praca-zdalna/project-manager",
        "https://nofluffjobs.com/pl/praca-zdalna/it-manager",
        "https://nofluffjobs.com/pl/praca-zdalna/engineering-manager",
        "https://nofluffjobs.com/pl/praca-zdalna/chapter-lead",
        # fallback z criteria query
        "https://nofluffjobs.com/pl?criteria=category%3Dproduct-owner+remote",
        "https://nofluffjobs.com/pl?criteria=category%3Dit-manager+remote",
    ]

    seen = set()
    for url in search_urls:
        r = _get(url, session=session)
        if not r:
            time.sleep(0.8)
            continue

        postings = _nfj_parse_next_data(r.text)
        if not postings:
            logger.debug(f"[NoFluffJobs] brak __NEXT_DATA__ dla {url}")
            time.sleep(0.8)
            continue

        for p in postings:
            title = p.get("title", "") or p.get("position", "")
            if not _matches_role(title):
                continue
            slug = p.get("url", "") or p.get("slug", "") or p.get("id", "")
            offer_url = f"https://nofluffjobs.com/pl/job/{slug}"
            if offer_url in seen:
                continue
            seen.add(offer_url)

            location = p.get("location") or {}
            places = location.get("places", []) if isinstance(location, dict) else []
            city = places[0].get("city", "") if places else ""
            remote = p.get("remote", False) or p.get("fullyRemote", False)
            loc_str = f"{city} {'remote' if remote else ''}".strip()

            salary_str, salary_from, salary_type = _nfj_salary_str(p)
            if salary_from and not _salary_in_range(salary_from, salary_type):
                continue

            results.append({
                "source": "NoFluffJobs",
                "title": title,
                "company": p.get("name", "") or (p.get("company") or {}).get("name", ""),
                "location": loc_str or "remote",
                "salary": salary_str,
                "salary_from": salary_from,
                "url": offer_url,
                "skills": p.get("technology", []) or p.get("skills", []),
                "description": (p.get("requirements") or {}).get("description", "") if isinstance(p.get("requirements"), dict) else "",
            })
        time.sleep(1)

    logger.info(f"[NoFluffJobs] {len(results)} ofert")
    return results


# ── 3. LinkedIn (guest API) ────────────────────────────────────────────────────

LI_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"


def scrape_linkedin(max_pages: int = 3) -> list[dict]:
    results = []
    search_terms = [
        "Chapter Lead IT Poland",
        "Engineering Manager IT Poland",
        "IT Manager Poland",
        "Technical Product Owner Poland",
        "Product Owner IT Poland",
        "IT Operations Manager Poland",
    ]
    for term in search_terms:
        for start in range(0, max_pages * 25, 25):
            r = _get(LI_URL, {
                "keywords": term,
                "location": "Poland",
                "start": start,
                "f_WT": "2",        # remote
                "f_TPR": "r604800", # ostatni tydzień
            })
            if not r or not r.text.strip():
                break
            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.find_all("li")
            if not cards:
                break
            found_any = False
            for card in cards:
                title_el = card.find("h3", class_=re.compile("base-search-card__title|job-card.*title"))
                company_el = card.find("h4", class_=re.compile("base-search-card__subtitle|company"))
                loc_el = card.find("span", class_=re.compile("job-search-card__location|location"))
                link_el = card.find("a", href=re.compile(r"/jobs/view/"))
                if not title_el or not link_el:
                    continue
                title = title_el.get_text(strip=True)
                company = company_el.get_text(strip=True) if company_el else ""
                location = loc_el.get_text(strip=True) if loc_el else "Poland"
                href = link_el.get("href", "").split("?")[0]
                if not _matches_role(title):
                    continue
                results.append({
                    "source": "LinkedIn",
                    "title": title,
                    "company": company,
                    "location": location,
                    "salary": "",
                    "salary_from": 0,
                    "url": href,
                    "skills": [],
                    "description": "",
                })
                found_any = True
            if not found_any:
                break
            time.sleep(1.5)  # LinkedIn jest czuły na szybkie requesty
    logger.info(f"[LinkedIn] {len(results)} ofert")
    return results


# ── 4. Pracuj.pl ───────────────────────────────────────────────────────────────
# Pracuj.pl blokuje proste requesty (403). Używamy sesji która najpierw pobiera
# stronę główną (dostaje cookies/CF clearance), potem dopiero szuka ofert.

PRACUJ_BASE = "https://www.pracuj.pl"
PRACUJ_SEARCH = "https://www.pracuj.pl/praca/{query};kw"


def _parse_pracuj_next_data(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script:
        return []
    try:
        data = _json.loads(script.string)
        offers_raw = (
            data.get("props", {})
                .get("pageProps", {})
                .get("data", {})
                .get("jobOffers", {})
                .get("groupedOffers", [])
        )
        return offers_raw
    except Exception as e:
        logger.warning(f"[Pracuj.pl] błąd parsowania __NEXT_DATA__: {e}")
        return []


def _pracuj_salary(offer_raw: dict) -> tuple[str, int]:
    sal = offer_raw.get("salaryDisplayText", "") or ""
    nums = re.findall(r"\d[\d\s]*\d", sal.replace("\xa0", " ").replace(" ", ""))
    salary_from = 0
    if nums:
        try:
            salary_from = int(nums[0].replace(" ", ""))
        except ValueError:
            pass
    return sal, salary_from


def scrape_pracuj() -> list[dict]:
    session = _make_session(PRACUJ_BASE)
    # Dodaj nagłówki imitujące przeglądarkę – Pracuj.pl wymaga Referer
    session.headers.update({
        "Referer": "https://www.pracuj.pl/",
        "Origin": "https://www.pracuj.pl",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })
    time.sleep(1)  # chwila po załadowaniu strony głównej

    results = []
    search_queries = [
        "product owner",
        "chapter lead",
        "it manager",
        "engineering manager",
        "product manager",
        "it operations manager",
        "service delivery manager",
    ]
    for query in search_queries:
        url = PRACUJ_SEARCH.format(query=requests.utils.quote(query))
        # wp=Łódź + praca zdalna (wp=home_office) + ostatnie 7 dni
        params = {"rdth": "30", "its": "big-cities,remote"}
        r = _get(url, params=params, session=session)
        if not r:
            time.sleep(1)
            continue

        offers_raw = _parse_pracuj_next_data(r.text)
        for o in offers_raw:
            sub = o.get("offers", [o])
            for offer in sub:
                title = offer.get("jobTitle", "") or o.get("jobTitle", "")
                if not _matches_role(title):
                    continue
                company = offer.get("companyName", "") or o.get("companyName", "")
                city = offer.get("jobCity", "") or o.get("jobCity", "")
                offer_url = offer.get("offerAbsoluteUri", "") or o.get("offerAbsoluteUri", "")
                sal_str, sal_from = _pracuj_salary(offer)
                if sal_from and not _salary_in_range(sal_from):
                    continue
                results.append({
                    "source": "Pracuj.pl",
                    "title": title,
                    "company": company,
                    "location": city,
                    "salary": sal_str,
                    "salary_from": sal_from,
                    "url": offer_url,
                    "skills": [],
                    "description": "",
                })
        time.sleep(1.5)
    logger.info(f"[Pracuj.pl] {len(results)} ofert")
    return results


# ── 5. Bulldogjob ──────────────────────────────────────────────────────────────
# Działa: /companies/jobs zwraca __NEXT_DATA__ z 50 najnowszymi ofertami.
# Paginacja SSR niedostępna – scraper pobiera 50 ofert i filtruje po tytule.

def _bdj_salary(j: dict) -> tuple[str, int, str]:
    sal = j.get("denominatedSalaryLong") or {}
    money_str = sal.get("money") or ""
    currency = sal.get("currency") or "PLN"
    hidden = sal.get("hidden", True)
    if not money_str or hidden:
        return "", 0, ""
    nums = re.findall(r"\d+", money_str.replace("\xa0", "").replace(" ", ""))
    nums_int = [int(n) for n in nums if int(n) > 100]
    if not nums_int:
        return "", 0, ""
    sal_from = nums_int[0]
    sal_str = f"{nums_int[0]:,}–{nums_int[1]:,} {currency}" if len(nums_int) >= 2 else f"{nums_int[0]:,} {currency}"
    contract = "B2B" if j.get("contractB2b") else ("UoP" if j.get("contractEmployment") else "")
    if contract:
        sal_str += f" ({contract})"
    return sal_str, sal_from, contract


def scrape_bulldogjob() -> list[dict]:
    results = []
    session = _make_session("https://bulldogjob.pl")
    r = _get("https://bulldogjob.pl/companies/jobs", session=session)
    if not r:
        logger.info("[Bulldogjob] 0 ofert")
        return results

    soup = BeautifulSoup(r.text, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script:
        logger.warning("[Bulldogjob] brak __NEXT_DATA__")
        logger.info("[Bulldogjob] 0 ofert")
        return results

    try:
        data = _json.loads(script.string)
        jobs = data["props"]["pageProps"].get("jobs", [])
    except Exception as e:
        logger.warning(f"[Bulldogjob] błąd parsowania: {e}")
        logger.info("[Bulldogjob] 0 ofert")
        return results

    seen = set()
    for j in jobs:
        title = j.get("position", "")
        if not _matches_role(title):
            continue

        job_id = j.get("id", "")
        offer_url = f"https://bulldogjob.pl/companies/jobs/{job_id}"
        if offer_url in seen:
            continue
        seen.add(offer_url)

        company = (j.get("company") or {}).get("name", "")
        city = j.get("city", "")
        remote = j.get("remote", False)
        loc_str = city if city else ""
        if remote:
            loc_str = f"{loc_str} / remote".strip(" /") if loc_str else "remote"

        sal_str, sal_from, contract = _bdj_salary(j)
        if sal_from and not _salary_in_range(sal_from, contract):
            continue

        tags = j.get("technologyTags") or []

        results.append({
            "source": "Bulldogjob",
            "title": title,
            "company": company,
            "location": loc_str or "Polska",
            "salary": sal_str,
            "salary_from": sal_from,
            "url": offer_url,
            "skills": [t if isinstance(t, str) else t.get("name", "") for t in tags],
            "description": "",
        })

    logger.info(f"[Bulldogjob] {len(results)} ofert")
    return results


# ── 6. TheProtocol.it ──────────────────────────────────────────────────────────
# Struktura __NEXT_DATA__ (zweryfikowana):
#   offersResponse.offers[] = [{title, employer (string!), workplace [{city}],
#                                salary {to, currency}, typesOfContracts [{salary.from}],
#                                workModes [], technologies [], offerUrlName}]
# Filtr kategorii (/t/...) jest zepsuty – scrapujemy zdalnie + paginacja, filtr po tytule.

PROTOCOL_BASE = "https://theprotocol.it"
PROTOCOL_REMOTE_URL = f"{PROTOCOL_BASE}/filtry/oferty;tryb-pracy/zdalnie"


def _protocol_salary(o: dict) -> tuple[str, int]:
    """Pobiera najlepszą kwotę z typesOfContracts (ma 'from')."""
    contracts = o.get("typesOfContracts") or []
    best_from, best_to, best_cur = 0, 0, "zł"
    for c in contracts:
        s = c.get("salary") or {}
        if not s:
            continue
        lo = s.get("from") or 0
        hi = s.get("to") or 0
        cur = s.get("currencySymbol", "zł")
        # tylko wynagrodzenia miesięczne (timeUnitId=0)
        if s.get("timeUnitId", 0) != 0:
            continue
        if lo > best_from:
            best_from, best_to, best_cur = lo, hi, cur
    if best_from:
        return f"{best_from:,}–{best_to:,} {best_cur}/mies.", best_from
    # fallback: tylko pole salary.to
    sal = o.get("salary") or {}
    if isinstance(sal, dict) and sal.get("to"):
        unit = (sal.get("timeUnit") or {}).get("shortForm", "")
        if "mth" in unit or "mies" in unit:
            return f"do {sal['to']:,} {sal.get('currency','zł')}/mies.", 0
    return "", 0


def scrape_theprotocol(max_pages: int = 30) -> list[dict]:
    results = []
    seen = set()
    session = _make_session(PROTOCOL_BASE)

    for page in range(1, max_pages + 1):
        url = PROTOCOL_REMOTE_URL if page == 1 else f"{PROTOCOL_REMOTE_URL};strona/{page}"
        r = _get(url, session=session)
        if not r:
            break

        soup = BeautifulSoup(r.text, "html.parser")
        script = soup.find("script", id="__NEXT_DATA__")
        if not script:
            break
        try:
            data = _json.loads(script.string)
            oresp = data["props"]["pageProps"].get("offersResponse", {})
            offers_raw = oresp.get("offers", [])
        except Exception as e:
            logger.warning(f"[TheProtocol] p.{page} __NEXT_DATA__ błąd: {e}")
            break

        if not offers_raw:
            break

        found_this_page = 0
        for o in offers_raw:
            if not isinstance(o, dict):
                continue
            title = o.get("title", "")
            if not _matches_role(title):
                continue

            offer_url_name = o.get("offerUrlName", "")
            offer_url = f"{PROTOCOL_BASE}/szczegoly/{offer_url_name}" if offer_url_name else ""
            if offer_url in seen:
                continue
            seen.add(offer_url)

            # employer to bezpośrednio string
            company = o.get("employer", "") or ""

            # workplace – lista dict z polem city (lub string w nowszych wersjach)
            workplaces = o.get("workplace") or []
            cities = []
            for w in workplaces:
                if isinstance(w, dict):
                    c = w.get("city", "")
                    if c:
                        cities.append(c)
                elif isinstance(w, str) and w:
                    cities.append(w)
            loc_str = ", ".join(cities) if cities else "remote"

            work_modes = o.get("workModes") or []
            if any(m in ("remote", "zdalna", "zdalnie") for m in work_modes):
                loc_str = (loc_str + " / remote").strip(" /")

            sal_str, sal_from = _protocol_salary(o)
            if sal_from and not _salary_in_range(sal_from):
                continue

            skills_raw = o.get("technologies") or []
            skills = []
            for t in skills_raw:
                if isinstance(t, dict):
                    n = t.get("name", "")
                    if n:
                        skills.append(n)
                elif isinstance(t, str) and t:
                    skills.append(t)

            results.append({
                "source": "TheProtocol",
                "title": title,
                "company": company,
                "location": loc_str,
                "salary": sal_str,
                "salary_from": sal_from,
                "url": offer_url,
                "skills": skills,
                "description": o.get("aboutProject", "") or "",
            })
            found_this_page += 1

        logger.debug(f"[TheProtocol] p.{page}: {found_this_page} dopasowań")
        time.sleep(0.8)

    logger.info(f"[TheProtocol] {len(results)} ofert")
    return results


# ── 7. RemoteOK ────────────────────────────────────────────────────────────────
# Publiczne JSON API – tag-based search zwraca do 100 ofert na tag.
# Wynagrodzenia w USD rocznie – salary_from=0 żeby pominąć filtr PLN.

REMOTEOK_API = "https://remoteok.com/api"
_REMOTEOK_TAGS = ["product", "manager", "exec"]


def scrape_remoteok() -> list[dict]:
    _headers = {**HEADERS, "Accept": "application/json"}
    results = []
    seen: set[str] = set()

    for tag in _REMOTEOK_TAGS:
        try:
            r = requests.get(
                REMOTEOK_API,
                params={"tag": tag},
                headers=_headers,
                timeout=25,
            )
            r.raise_for_status()
            r.encoding = "utf-8"
            data = r.json()
        except Exception as e:
            logger.warning(f"[RemoteOK] błąd dla tag={tag}: {e}")
            continue

        for job in data:
            if not isinstance(job, dict) or "id" not in job:
                continue
            title = job.get("position", "") or ""
            if not _matches_role(title):
                continue
            url = job.get("url", "")
            if url in seen:
                continue
            seen.add(url)
            company = job.get("company", "")
            location = job.get("location", "") or "remote"
            tags_list = job.get("tags") or []
            sal_min = job.get("salary_min") or 0
            sal_max = job.get("salary_max") or 0
            sal_str = ""
            if sal_min or sal_max:
                sal_str = (
                    f"{sal_min:,}–{sal_max:,} USD/yr"
                    if sal_max
                    else f"od {sal_min:,} USD/yr"
                )
            results.append({
                "source": "RemoteOK",
                "title": title,
                "company": company,
                "location": location,
                "salary": sal_str,
                "salary_from": 0,
                "url": url,
                "skills": [t for t in tags_list if isinstance(t, str)],
                "description": "",
            })
        time.sleep(1)

    logger.info(f"[RemoteOK] {len(results)} ofert")
    return results


# ── 8. We Work Remotely ────────────────────────────────────────────────────────
# RSS feeds dla kategorii Management i Product.
# Format tytułu: "Kategoria: Stanowisko at Firma"

_WWR_REGION_NS = "https://weworkremotely.com/namespaces/rss/1.0"

WWR_FEEDS = [
    "https://weworkremotely.com/categories/remote-management-and-finance-jobs.rss",
    "https://weworkremotely.com/categories/remote-product-jobs.rss",
]


def scrape_weworkremotely() -> list[dict]:
    results = []
    seen: set[str] = set()

    for feed_url in WWR_FEEDS:
        r = _get(feed_url, timeout=20)
        if not r:
            continue
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError as e:
            logger.warning(f"[WeWorkRemotely] błąd XML ({feed_url}): {e}")
            continue

        channel = root.find("channel")
        if channel is None:
            continue

        for item in channel.findall("item"):
            raw_title = item.findtext("title") or ""
            # Format: "Firma: Stanowisko" (company before colon, title after)
            if ": " in raw_title:
                company, title = raw_title.split(": ", 1)
            else:
                title, company = raw_title, ""
            title = title.strip()
            company = company.strip()

            if not _matches_role(title):
                continue

            url = item.findtext("link") or ""
            if url in seen:
                continue
            seen.add(url)

            region = (
                item.findtext(f"{{{_WWR_REGION_NS}}}region")
                or item.findtext("region")
                or "Worldwide"
            )

            results.append({
                "source": "WeWorkRemotely",
                "title": title,
                "company": company,
                "location": region,
                "salary": "",
                "salary_from": 0,
                "url": url,
                "skills": [],
                "description": "",
            })

    logger.info(f"[WeWorkRemotely] {len(results)} ofert")
    return results


# ── 9. Himalayas ───────────────────────────────────────────────────────────────
# himalayas.app – Next.js, oferty zdalne globalnie.
# Parsuje __NEXT_DATA__ (props.pageProps.jobs), fallback DOM.

HIMALAYAS_BASE = "https://himalayas.app"
HIMALAYAS_TERMS = [
    "product owner",
    "engineering manager",
    "it manager",
    "product manager",
    "chapter lead",
]
_HIMALAYAS_API = f"{HIMALAYAS_BASE}/jobs/api"


def scrape_himalayas(max_per_term: int = 60) -> list[dict]:
    """Używa publicznego JSON API himalayas.app/jobs/api z paginacją (max 60/term)."""
    results = []
    seen: set[str] = set()

    for term in HIMALAYAS_TERMS:
        offset = 0
        limit = 20
        fetched = 0
        while fetched < max_per_term:
            try:
                r = requests.get(
                    _HIMALAYAS_API,
                    params={"q": term, "limit": limit, "offset": offset},
                    headers=HEADERS,
                    timeout=20,
                )
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                logger.warning(f"[Himalayas] błąd dla '{term}' offset={offset}: {e}")
                break

            jobs = data.get("jobs", [])
            if not jobs:
                break

            for job in jobs:
                title = job.get("title", "")
                if not _matches_role(title):
                    continue
                company = job.get("companyName", "")
                guid = job.get("guid", "")
                app_link = job.get("applicationLink", "")
                job_url = guid if (guid or "").startswith("http") else app_link
                if not job_url or job_url in seen:
                    continue
                seen.add(job_url)
                restrictions = job.get("locationRestrictions") or []
                location = ", ".join(restrictions) if restrictions else "remote"
                sal_min = job.get("minSalary") or 0
                sal_max = job.get("maxSalary") or 0
                cur = job.get("currency", "USD")
                sal_str = ""
                if sal_min or sal_max:
                    sal_str = (
                        f"{sal_min:,}–{sal_max:,} {cur}/yr"
                        if sal_max
                        else f"od {sal_min:,} {cur}/yr"
                    )
                results.append({
                    "source": "Himalayas",
                    "title": title,
                    "company": company,
                    "location": location,
                    "salary": sal_str,
                    "salary_from": 0,
                    "url": job_url,
                    "skills": [],
                    "description": (job.get("excerpt", "") or "")[:500],
                })

            fetched += len(jobs)
            offset += limit
            time.sleep(0.8)

        time.sleep(1)

    logger.info(f"[Himalayas] {len(results)} ofert")
    return results


# ── Deduplikacja ───────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Normalizuje tekst do porównań – małe litery, bez znaków specjalnych."""
    text = text.lower().strip()
    # polskie znaki → ascii
    pl_map = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
    text = text.translate(pl_map)
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def deduplicate(offers: list[dict]) -> list[dict]:
    """
    Usuwa duplikaty. Ta sama oferta = ten sam (company_normalized, title_normalized).
    Gdy duplikat, zostawiamy tę z bogatszymi danymi (salary, skills).
    """
    seen: dict[tuple, dict] = {}
    for offer in offers:
        key = (
            _normalize(offer.get("company", "")),
            _normalize(offer.get("title", "")),
        )
        if key not in seen:
            seen[key] = offer
        else:
            existing = seen[key]
            # Preferuj ofertę z widełkami lub więcej skillsów
            if not existing.get("salary") and offer.get("salary"):
                seen[key] = offer
            elif len(offer.get("skills", [])) > len(existing.get("skills", [])):
                seen[key] = offer
    result = list(seen.values())
    logger.info(f"[DEDUP] {len(offers)} → {len(result)} ofert po deduplikacji")
    return result


# ── Główny punkt wejścia ───────────────────────────────────────────────────────

# Aliasy nazw portali → klucz kanoniczny
_PORTAL_ALIASES: dict[str, str] = {
    "jjit":           "justjoinit",
    "justjoinit":     "justjoinit",
    "nofluff":        "nofluffjobs",
    "nofluffjobs":    "nofluffjobs",
    "pracuj":         "pracuj",
    "linkedin":       "linkedin",
    "theprotocol":    "theprotocol",
    "bulldogjob":     "bulldogjob",
    "remoteok":       "remoteok",
    "weworkremotely": "weworkremotely",
    "himalayas":      "himalayas",
}


def _playwright_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except ImportError:
        return False


def scrape_all(portals: list[str] | None = None) -> list[dict]:
    """
    Uruchamia scrapery. portals=None → wszystkie; portals=["jjit"] → tylko JJIT.
    Obsługuje aliasy: jjit/justjoinit, nofluff/nofluffjobs, pracuj, linkedin,
    theprotocol, bulldogjob, remoteok, weworkremotely, himalayas.
    """
    # Normalizuj aliasy do zestawu kluczy kanonicznych
    if portals:
        want = {_PORTAL_ALIASES.get(p.lower(), p.lower()) for p in portals}
        logger.info(f"[SCRAPER] Filtr portali: {sorted(want)}")
    else:
        want = None  # wszystkie

    def _wanted(key: str) -> bool:
        return want is None or key in want

    all_offers: list[dict] = []

    # Scrapery działające bez przeglądarki
    scrapers = [
        ("linkedin",        scrape_linkedin),
        ("theprotocol",     scrape_theprotocol),
        ("bulldogjob",      scrape_bulldogjob),
        ("remoteok",        scrape_remoteok),
        ("weworkremotely",  scrape_weworkremotely),
        ("himalayas",       scrape_himalayas),
    ]

    for key, fn in scrapers:
        if not _wanted(key):
            continue
        try:
            results = fn()
            all_offers.extend(results)
        except Exception as e:
            logger.error(f"[{key}] krytyczny błąd scrapera: {e}")

    # Scrapery Playwright (SPA / Cloudflare)
    pw_keys = {"justjoinit", "nofluffjobs", "pracuj"}
    if any(_wanted(k) for k in pw_keys):
        if _playwright_available():
            from modules.scraper_pw import (
                scrape_justjoinit,
                scrape_nofluffjobs,
                scrape_pracuj,
            )
            pw_scrapers = [
                ("justjoinit", scrape_justjoinit),
                ("nofluffjobs", scrape_nofluffjobs),
                ("pracuj",      scrape_pracuj),
            ]
            for key, fn in pw_scrapers:
                if not _wanted(key):
                    continue
                try:
                    results = fn()
                    all_offers.extend(results)
                except Exception as e:
                    logger.error(f"[{key}] krytyczny błąd scrapera Playwright: {e}")
        else:
            logger.warning("[SCRAPER] Playwright niedostępny – pomijam JustJoinIT, NoFluffJobs, Pracuj.pl")

    all_offers = deduplicate(all_offers)
    logger.info(f"[SCRAPER] łącznie {len(all_offers)} unikalnych ofert")
    return all_offers
