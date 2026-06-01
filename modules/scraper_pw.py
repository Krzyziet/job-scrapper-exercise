"""
Playwright-based scrapers dla portali SPA:
  - JustJoinIT  (REST API + detail page dla opisu)
  - NoFluffJobs (DOM fallback)
  - Pracuj.pl   (dehydratedState + [data-test='default-offer'] DOM)
"""

import json
import logging
import re
import time

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Browser, BrowserContext

logger = logging.getLogger(__name__)

DESC_MAX_CHARS   = 2_500
_JJIT_API_BASE   = "https://justjoin.it/api/candidate-api/offers"

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


# ── JustJoinIT ─────────────────────────────────────────────────────────────────

JJIT_SEARCH_TERMS = [
    "product owner",
    "chapter lead",
    "it manager",
    "engineering manager",
    "product manager",
]


def _jjit_salary_from_api(et_list: list) -> tuple[str, int, str]:
    """
    Wyciąga (salary_str, salary_from_pln_month, contract_type) z employmentTypes.

    Struktura JJIT API:
      - from/to       = miesięczna suma PLN (zawsze, niezależnie od unit)
      - fromPerUnit   = stawka godzinowa (gdy unit='Hour')
      - unit='Hour'   → kontrakt rozliczany godzinowo, ale from/to już są /mies.

    Bierze tylko currencySource='original'; priorytety: b2b > permanent > reszta.
    Zwraca ("", 0, "") gdy brak widełek.
    """
    if not et_list:
        return "", 0, ""

    original = [et for et in et_list if et.get("currencySource") == "original"] or et_list
    _PRIO = {"b2b": 0, "b2b_contract": 0, "permanent": 1,
             "contract_of_employment": 1, "mandate_contract": 2}
    ordered = sorted(original, key=lambda x: _PRIO.get(x.get("type", ""), 9))

    for et in ordered:
        sf = et.get("from") or 0
        st = et.get("to")   or 0
        if not sf:
            continue
        cur   = et.get("currency", "PLN") or "PLN"
        ctype = et.get("type", "") or ""
        unit  = (et.get("unit") or "month").lower()

        # Dla kontraktów godzinowych pokaż stawkę /h w nawiasie
        if unit in ("hour", "hourly", "h"):
            sf_h = et.get("fromPerUnit") or 0
            st_h = et.get("toPerUnit")   or 0
            hourly_note = (f" [{int(sf_h)}–{int(st_h)} {cur}/h]"
                           if sf_h and st_h else
                           f" [{int(sf_h)} {cur}/h]" if sf_h else "")
        else:
            hourly_note = ""

        sf_int = int(sf)
        st_int = int(st) if st else 0
        sal_str = (f"{sf_int:,}–{st_int:,} {cur}/mies. ({ctype}){hourly_note}"
                   if st_int else
                   f"od {sf_int:,} {cur}/mies. ({ctype}){hourly_note}")
        return sal_str, sf_int, ctype

    # Brak widełek – zwróć przynajmniej typ kontraktu
    return "", 0, (ordered[0].get("type", "") if ordered else "")


# ── Inteligentne czyszczenie opisu JJIT ──────────────────────────────────────

_CLEAN_MAX = 1_500   # limit oczyszczonego opisu (zamiast surowych 2500)

_DROP_KW = frozenset({
    # English
    "about us", "who we are", "about the company", "about company",
    "we offer", "what we offer", "benefits", "perks", "why join",
    "equal opportunity", "diversity", "what we provide", "our offer",
    "what do we offer", "what you'll get", "what you get",
    "what we give",
    # Polish
    "o nas", "o firmie", "oferujemy", "co oferujemy", "benefity",
    "co zyskujesz", "dlaczego my", "oferta zawiera", "co ci oferujemy",
    "nasze benefity", "dla ciebie", "co ci dajemy", "dlaczego warto",
})

_KEEP_KW = frozenset({
    # English
    "requirements", "qualifications", "must have", "nice to have",
    "what we're looking for", "what we are looking for",
    "responsibilities", "your role", "what you'll do", "what you will do",
    "your responsibilities", "key responsibilities", "about the role",
    "the role", "your mission", "role summary", "the job",
    "people leadership", "technical leadership", "delivery", "execution",
    "collaboration", "architecture", "engineering", "leadership",
    "growth", "mentorship", "management",
    # Polish
    "wymagania", "obowiązki", "oczekujemy", "szukamy", "twoja rola",
    "zakres obowiązków", "mile widziane", "czego oczekujemy",
    "twoje zadania", "co będziesz robić", "wymagane", "oczekiwania",
    "co robisz", "twoje obowiązki", "twoja misja", "zakres",
})


def _heading_label(text: str) -> str:
    """Klasyfikuje nagłówek sekcji: 'drop' | 'keep' | 'unknown'."""
    t = text.lower().strip().rstrip(":")
    for kw in _DROP_KW:
        if kw in t:
            return "drop"
    for kw in _KEEP_KW:
        if kw in t:
            return "keep"
    return "unknown"


def _is_section_header(el) -> bool:
    """
    True dla h1-h6, <p><strong>...</strong></p> (samodzielny bold)
    oraz krótkich <p> z czystym tekstem bez interpunkcji zdaniowej
    (np. <p>Requirements</p>, <p>Key Responsibilities</p>).
    """
    if el.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        return True
    if el.name == "p":
        kids = [c for c in el.children if str(c).strip()]
        # Samodzielny <strong> lub <b>
        if (len(kids) == 1
                and hasattr(kids[0], "name")
                and kids[0].name in ("strong", "b")):
            return True
        # Krótki czysty tekst bez zdaniowej interpunkcji → działa jak nagłówek
        text = el.get_text(strip=True)
        if (len(text) <= 60
                and not any(c in text for c in ".?!")
                and not text.endswith(",")
                and len(kids) <= 1):
            return True
    return False


def _lines_from_elems(elems: list) -> list[str]:
    """Wyciąga listę linii tekstu z listy elementów BeautifulSoup."""
    lines: list[str] = []
    for el in elems:
        if not hasattr(el, "name") or not el.name:
            continue
        if el.name in ("ul", "ol"):
            for li in el.find_all("li"):
                t = re.sub(r"\s+", " ", li.get_text(separator=" ")).strip()
                if t:
                    lines.append(f"• {t}")
        elif el.name == "p":
            t = re.sub(r"\s+", " ", el.get_text(separator=" ")).strip()
            if len(t) > 10:
                lines.append(t)
        elif el.name in ("div", "section", "article", "span"):
            # Zejdź rekurencyjnie w kontenery
            sub = [c for c in el.children if hasattr(c, "name") and c.name]
            lines.extend(_lines_from_elems(sub))
        else:
            t = re.sub(r"\s+", " ", el.get_text(separator=" ")).strip()
            if len(t) > 10:
                lines.append(t)
    return lines


def _split_sections(container) -> list[tuple[str, list]]:
    """
    Dzieli bezpośrednie dzieci kontenera na sekcje wg nagłówków.
    Zwraca listę (label, [elementy]).
    """
    sections: list[tuple[str, list]] = []
    cur_label = "unknown"
    cur_elems: list = []

    for el in container.children:
        if not hasattr(el, "name") or not el.name:
            continue
        if _is_section_header(el):
            if cur_elems:
                sections.append((cur_label, cur_elems))
            cur_label = _heading_label(el.get_text(strip=True))
            cur_elems = []
        else:
            cur_elems.append(el)

    if cur_elems:
        sections.append((cur_label, cur_elems))
    return sections


def _trim_to_limit(text: str, limit: int = _CLEAN_MAX) -> str:
    """Przycina tekst do ~limit znaków na granicy linii lub słowa."""
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    if cut < int(limit * 0.75):
        cut = text.rfind(" ", 0, limit)
    if cut < int(limit * 0.75):
        cut = limit
    return text[:cut].rstrip()


def _clean_jjit_description(html: str) -> str:
    """
    Filtruje HTML opisu JJIT: wycina marketing (about us, benefits),
    zostawia wymagania i obowiązki. Zwraca czysty tekst ≤ ~1500 znaków.

    Fallback 1: wszystkie <li> z sekcji non-drop.
    Fallback 2: surowy get_text().
    """
    if not html:
        return ""

    soup = BeautifulSoup(html, "html.parser")
    container = soup.find("body") or soup

    sections = _split_sections(container)

    # Jeśli kontener ma tylko jeden wrapper-div, zejdź poziom niżej
    if len(sections) <= 1:
        wrapper = container.find("div")
        if wrapper:
            deeper = _split_sections(wrapper)
            if len(deeper) > 1:
                sections = deeper

    # ── Zbierz linie z sekcji non-drop ───────────────────────────────────────
    lines: list[str] = []
    for label, elems in sections:
        if label == "drop":
            continue
        lines.extend(_lines_from_elems(elems))

    result = "\n".join(lines).strip()

    # ── Fallback 1: wszystkie <li> z non-drop ────────────────────────────────
    if len(result) < 100:
        li_lines: list[str] = []
        for label, elems in sections:
            if label == "drop":
                continue
            for el in elems:
                if hasattr(el, "find_all"):
                    for li in el.find_all("li"):
                        t = re.sub(r"\s+", " ", li.get_text(separator=" ")).strip()
                        if t:
                            li_lines.append(f"• {t}")
        if li_lines:
            result = "\n".join(li_lines).strip()

    # ── Fallback 2: surowy tekst ──────────────────────────────────────────────
    if len(result) < 100:
        result = re.sub(r"\s+", " ", soup.get_text(separator=" ")).strip()

    return _trim_to_limit(result)


def _jjit_fetch_list_page(page, term: str, cursor: int | None) -> dict | None:
    """Pobiera jedną stronę listy ofert z REST API."""
    url = (
        f"{_JJIT_API_BASE}"
        f"?keywords={term.replace(' ', '+')}&keywordType=any&pageSize=20"
        + (f"&from={cursor}" if cursor is not None else "")
    )
    try:
        resp = page.goto(url, timeout=20_000, wait_until="domcontentloaded")
        if resp and resp.status == 200:
            return resp.json()
        logger.warning(f"[JJIT] lista HTTP {resp.status if resp else '?'}  {url}")
    except Exception as e:
        logger.warning(f"[JJIT] lista błąd '{term}': {e}")
    return None


def _jjit_fetch_description(page, slug: str, offer_url: str) -> str:
    """
    Pobiera i czyści opis oferty (wymagania/obowiązki, max ~1500 znaków).
    Próba 1: GET /api/candidate-api/offers/{slug} – pole body (HTML).
    Próba 2: DOM strony oferty.
    """
    # Próba 1: detail endpoint API (body = surowy HTML)
    try:
        resp = page.goto(
            f"{_JJIT_API_BASE}/{slug}",
            timeout=15_000, wait_until="domcontentloaded",
        )
        if resp and resp.status == 200:
            data = resp.json()
            body = data.get("body") or data.get("description") or ""
            if body:
                return _clean_jjit_description(body)
    except Exception as e:
        logger.debug(f"[JJIT] detail API błąd ({slug}): {e}")

    # Próba 2: DOM strony oferty
    try:
        page.goto(offer_url, timeout=20_000, wait_until="domcontentloaded")
        page.wait_for_timeout(2_000)
        soup = BeautifulSoup(page.content(), "html.parser")
        el = (
            soup.find(attrs={"data-testid": re.compile(r"job.desc|description", re.I)})
            or soup.find("div", class_=re.compile(r"JobDescription|job-desc|offer-desc", re.I))
            or soup.find("section", class_=re.compile(r"description|content", re.I))
        )
        if el:
            return _clean_jjit_description(str(el))
        main = soup.find("main") or soup.find("article")
        if main:
            return _clean_jjit_description(str(main))
    except Exception as e:
        logger.debug(f"[JJIT] DOM detail błąd ({offer_url}): {e}")

    return ""


def _jjit_parse_item(texts: list[str], href: str) -> dict | None:
    """Parsuje listę tekstów z li[data-index] → oferta lub None."""
    # Znajdź tytuł przez dopasowanie roli
    title = ""
    title_idx = -1
    for i, t in enumerate(texts):
        if _matches_role(t):
            title = t
            title_idx = i
            break
    if not title:
        return None

    # Przed tytułem: firma i miasto (po odfiltrowaniu szumu)
    pre = [
        t for t in texts[:title_idx]
        if t.lower() not in _JJIT_NOISE
        and not re.match(r"^[,\s]*\+\d+$", t)
        and len(t.strip()) > 1
    ]
    company = pre[0] if pre else ""
    city = pre[1] if len(pre) >= 2 else ""

    # Lokalizacja – zawsze Poland/remote na JJIT dla naszych zapytań
    loc_str = city if city else "remote"

    url = f"https://justjoin.it{href}" if href.startswith("/") else href

    return {
        "source": "JustJoinIT",
        "title": title,
        "company": company,
        "location": loc_str,
        "salary": "",
        "salary_from": 0,
        "url": url,
        "skills": [],
        "description": "",
    }


def scrape_justjoinit() -> list[dict]:
    all_results: list[dict] = []
    seen_urls:   set[str]   = set()

    with sync_playwright() as pw:
        browser, ctx = _make_browser(pw)
        page = ctx.new_page()

        # ── Faza 1: lista przez REST API ─────────────────────────────────────
        candidates: list[dict] = []
        for term in JJIT_SEARCH_TERMS:
            cursor = None
            while True:
                raw = _jjit_fetch_list_page(page, term, cursor)
                if not raw:
                    break
                items     = raw.get("data", [])
                meta      = raw.get("meta", {})
                total_int = meta.get("totalItems") or 0

                new_this_page = 0
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    slug = item.get("slug") or item.get("guid") or ""
                    if not slug:
                        continue
                    offer_url = f"https://justjoin.it/job-offer/{slug}"
                    if offer_url in seen_urls:
                        continue
                    seen_urls.add(offer_url)
                    new_this_page += 1

                    title    = item.get("title") or ""
                    city     = item.get("city") or ""
                    wp       = item.get("workplaceType") or ""
                    location = f"{city} ({wp})" if city and wp else (city or wp)

                    # Skills dostępne w liście – bez dodatkowych requestów
                    req  = [s.get("name") if isinstance(s, dict) else s
                            for s in (item.get("requiredSkills") or [])]
                    nice = [s.get("name") if isinstance(s, dict) else s
                            for s in (item.get("niceToHaveSkills") or [])]
                    skills = [str(s) for s in req + nice if s]

                    # Salary z API – ustawia salary/salary_from, pomija predykcję
                    et_list = item.get("employmentTypes") or []
                    sal_str, sal_from, sal_contract = _jjit_salary_from_api(et_list)

                    candidates.append({
                        "_slug":           slug,
                        "source":          "JustJoinIT",
                        "title":           title,
                        "company":         item.get("companyName") or "",
                        "location":        location,
                        "salary":          sal_str,
                        "salary_from":     sal_from,
                        "salary_contract": sal_contract,
                        "url":             offer_url,
                        "skills":          skills,
                        "description":     "",
                    })

                cursor = (meta.get("next") or {}).get("cursor")
                if (new_this_page == 0
                        or cursor is None
                        or (total_int and cursor >= total_int)):
                    break
                time.sleep(0.3)

        logger.info(f"[JustJoinIT] {len(candidates)} ofert z listy API")

        # ── Faza 2: opis tylko dla ofert po filtrze tytułu + lokalizacji ─────
        to_enrich = [
            o for o in candidates
            if _matches_role(o["title"]) and _matches_location(o["location"])
        ]
        logger.info(f"[JustJoinIT] pobieranie opisów dla {len(to_enrich)} "
                    f"(po filtrze tytułu+lokalizacji)")

        for i, offer in enumerate(to_enrich, 1):
            slug = offer.pop("_slug")
            try:
                desc = _jjit_fetch_description(page, slug, offer["url"])
                offer["description"] = desc
                logger.debug(
                    f"[JJIT desc] {i}/{len(to_enrich)}  "
                    f"{'OK ' + str(len(desc)) + 'zn.' if desc else 'brak'}  "
                    f"– {offer['title']} @ {offer['company']}"
                )
            except Exception as e:
                logger.debug(f"[JJIT desc] błąd {offer['url']}: {e}")
            time.sleep(0.5)

        # Usuń _slug z ofert, które nie były enrichowane
        for o in candidates:
            o.pop("_slug", None)

        # Zwróć oferty które przeszły filtr tytułu (opis może być "")
        all_results = [o for o in candidates if _matches_role(o["title"])]

        browser.close()

    logger.info(f"[JustJoinIT] {len(all_results)} ofert łącznie")
    return all_results


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
