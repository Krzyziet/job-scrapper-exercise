import os
import re
import json
import logging

from modules.profile import (
    CANDIDATE_PROFILE,
    WEIGHTS,
    BOOSTERS,
    STRETCH_PENALTIES,
    VERDICT_THRESHOLD,
    compute_final_score,
)

logger = logging.getLogger(__name__)


def claude_available() -> bool:
    key = os.environ.get("CLAUDE_API_KEY", "")
    return bool(key and key.startswith("sk-ant-"))

SALARY_MIN_UOP = 23000
SALARY_MIN_B2B = 25000
SALARY_TOLERANCE = 2000  # akceptujemy oferty do 2k poniżej minimum

# ── Kursy walut (PLN) – orientacyjne, aktualizuj raz na kwartał ───────────────
_USD_TO_PLN = 4.05
_EUR_TO_PLN = 4.28
_GBP_TO_PLN = 5.15

# ── Reguły rynku IT PL (fallback bez Claude) ──────────────────────────────────

# Mediana widełek UoP brutto dla rynku PL 2024-2025
_SALARY_RULES: list[tuple[str, int, int]] = [
    ("chapter lead",          26_000, 36_000),
    ("engineering manager",   24_000, 34_000),
    ("it manager",            22_000, 32_000),
    ("noc manager",           20_000, 30_000),
    ("it operations manager", 20_000, 30_000),
    ("service delivery",      20_000, 30_000),
    ("technical product owner", 20_000, 28_000),
    ("product owner",         18_000, 27_000),
    ("product manager",       18_000, 26_000),
]

_COMPANY_PREMIUM = {
    "ing": 1.1, "santander": 1.1, "mbank": 1.1, "pko": 1.05,
    "hsbc": 1.15, "bnp": 1.1, "commerzbank": 1.1,
    "accenture": 1.05, "capgemini": 1.0, "ibm": 1.1,
    "orange": 1.05, "t-mobile": 1.05,
}

# Słowa kluczowe podpowiadające emphasis CV
_EMPHASIS_KEYWORDS = {
    "network":    ["network", "cisco", "palo alto", "noc", "soc", "routing", "switching", "firewall"],
    "devops":     ["devops", "kubernetes", "docker", "ci/cd", "aws", "gcp", "azure", "terraform", "cloud"],
    "product":    ["product owner", "scrum", "agile", "backlog", "roadmap", "sprint", "kanban"],
    "management": ["manager", "chapter lead", "team lead", "leadership", "people management", "hiring"],
}


def _rule_salary(offer: dict) -> tuple[int, int]:
    title = offer.get("title", "").lower()
    company = offer.get("company", "").lower()
    base_from, base_to = 18_000, 26_000
    for keyword, sal_from, sal_to in _SALARY_RULES:
        if keyword in title:
            base_from, base_to = sal_from, sal_to
            break
    for company_key, multiplier in _COMPANY_PREMIUM.items():
        if company_key in company:
            base_from = int(base_from * multiplier)
            base_to = int(base_to * multiplier)
            break
    return base_from, base_to


def _rule_emphasis(offer: dict) -> str:
    text = (offer.get("title", "") + " " + " ".join(offer.get("skills", []))).lower()
    scores = {k: sum(1 for kw in kws if kw in text) for k, kws in _EMPHASIS_KEYWORDS.items()}
    return max(scores, key=scores.get)


_LOC_LODZ       = {"łódź", "lodz", "lódź"}
_LOC_WARSAW     = {"warszawa", "warsaw"}
_LOC_TROJMIASTO = {"gdańsk", "gdansk", "gdynia", "sopot", "trojmiasto", "trójmiasto"}
_LOC_REMOTE     = {"remote", "zdal", "worldwide", "anywhere", "fully remote", "praca zdalna"}

_BANKING_KW = {
    "ing", "bnp", "commerzbank", "santander", "mbank", "pko", "hsbc", "citi",
    "alior", "millennium", "raiffeisen", "deutsche bank", "credit suisse",
    "jpmorgan", "barclays", "goldman", "morgan stanley", "wells fargo",
    "bank", "bancorp", "banca",
}
_FINTECH_KW = {
    "fintech", "paytech", "payu", "revolut", "stripe", "klarna", "wise",
    "adyen", "checkout", "paypal", "monzo", "n26", "starling", "payments",
    "crypto", "blockchain", "neobank",
}
_HEALTHCARE_KW = {
    "health", "medical", "hospital", "clinic", "pharma", "biotech",
    "medtech", "medicare", "meditrina", "patient", "clinical",
}


def _location_score(location: str) -> int:
    """
    Punkty za lokalizację (priorytet kandydata):
      +3  hybrydowo / stacjonarnie Łódź
      +2  full remote
      +1  hybrydowo Warszawa
       0  hybrydowo Gdańsk / Gdynia / Sopot
      -1  inne
    """
    loc = location.lower()
    if any(k in loc for k in _LOC_LODZ):
        return 3
    if any(k in loc for k in _LOC_REMOTE) and not any(k in loc for k in _LOC_WARSAW | _LOC_TROJMIASTO):
        return 2
    if any(k in loc for k in _LOC_WARSAW):
        return 1
    if any(k in loc for k in _LOC_TROJMIASTO):
        return 0
    return -1


def _sector_score(company: str, description: str = "") -> int:
    text = (company + " " + description).lower()
    if any(k in text for k in _BANKING_KW | _FINTECH_KW | _HEALTHCARE_KW):
        return 1
    return 0


def _contract_score(offer: dict) -> int:
    salary = (offer.get("salary", "") or "").lower()
    contract = (offer.get("salary_contract", "") or "").lower()
    if "b2b" in salary or "b2b" in contract:
        return 1
    return 0


def _rule_score(offer: dict) -> dict:
    """
    Fallback scoring bez Claude.
    Produkuje dimensions (aproksymowane) i przechodzi przez compute_final_score,
    tak by struktura danych była identyczna z trybem Claude.
    """
    title    = offer.get("title", "").lower()
    company  = offer.get("company", "")
    location = offer.get("location", "")
    desc     = offer.get("description", "") or ""
    text_all = (title + " " + desc).lower()

    # people_leadership_fit – dopasowanie stanowiska przywódczego
    if "chapter lead" in title:
        plf = 9
    elif any(k in title for k in [
        "engineering manager", "it manager", "it operations manager",
        "service delivery manager", "noc manager",
    ]):
        plf = 7
    elif any(k in title for k in ["product owner", "product manager", "technical product owner"]):
        plf = 5
    else:
        plf = 3

    # role_seniority_fit
    rsf = 7 if "senior" in title else 5

    # product_agile_fit
    product_kw = ["product", "agile", "scrum", "backlog", "roadmap", "sprint", "kanban"]
    paf = 7 if any(k in text_all for k in product_kw) else 4

    # technical_credibility_fit – neutralne bez pełnego opisu
    tcf = 5

    # growth_learning_fit – neutralne
    glf = 5

    # conditions_fit – z lokalizacji i kontraktu
    loc_map = {3: 8, 2: 7, 1: 6, 0: 5, -1: 3}
    cf = loc_map.get(_location_score(location), 5)
    if _contract_score(offer):
        cf = min(cf + 1, 10)

    dimensions = {
        "people_leadership_fit":    plf,
        "role_seniority_fit":       rsf,
        "product_agile_fit":        paf,
        "technical_credibility_fit": tcf,
        "growth_learning_fit":      glf,
        "conditions_fit":           cf,
    }

    result = {
        "dimensions":                  dimensions,
        "stretch_flag_line_management": False,
        "stretch_flag_english_c1":      False,
        "booster_domain":               _sector_score(company, desc) > 0,
        "booster_ai":                   any(k in text_all for k in [
                                            "artificial intelligence", " ai ", "machine learning",
                                            "generative", "llm",
                                        ]),
        "key_gaps":                     [],
        "match_reason":                 "Dopasowanie regułowe (brak klucza Claude API)",
        "cv_emphasis":                  _rule_emphasis(offer),
    }
    return compute_final_score(result)


# ── Prompty Claude ─────────────────────────────────────────────────────────────

SALARY_PREDICT_PROMPT = """Jesteś ekspertem rynku IT w Polsce.

Na podstawie informacji o ofercie pracy oszacuj widełki wynagrodzenia.

Oferta:
- Tytuł: {title}
- Firma: {company}
- Lokalizacja: {location}
- Wymagane umiejętności: {skills}
- Opis: {description}

Odpowiedz WYŁĄCZNIE w formacie JSON (bez markdown):
{{
  "salary_from": <liczba całkowita PLN brutto miesięcznie>,
  "salary_to": <liczba całkowita PLN brutto miesięcznie>,
  "contract_type": "<UoP|B2B|unknown>",
  "confidence": "<low|medium|high>"
}}

Bazuj na aktualnych realiach rynku IT w Polsce (2024-2025).
Dla Chapter Lead / IT Manager / Engineering Manager senior poziom to zazwyczaj 18 000–35 000 PLN UoP.
"""

ANALYSIS_PROMPT = """Jesteś doświadczonym rekruterem IT w Polsce. Oceń dopasowanie kandydata do oferty z perspektywy PRACODAWCY – co oferta faktycznie wymaga, a co kandydat udokumentował (nie deklarowana otwartość).

Zasady oceniania:
- Rozróżniaj nieformalny wpływ na zespół (agile lead, PO, stakeholder mgmt) od formalnego line managementu (zatrudnianie/zwalnianie, oceny pracownicze, bezpośrednia odpowiedzialność HR). Jeśli oferta wymaga formalnego line managementu, ustaw stretch_flag_line_management=true.
- Jeśli oferta wymaga angielskiego C1/C2, a kandydat ma B2, ustaw stretch_flag_english_c1=true.
- Brak dopasowania domenowego NIE dyskwalifikuje – ustaw booster_domain=false, ale nie karać innych wymiarów tylko za domenę.
- booster_domain=true gdy firma/rola jest blisko domeny networking/infrastruktury kandydata.
- booster_ai=true gdy oferta zawiera istotny komponent AI/ML/GenAI w zakresie obowiązków.

=== PROFIL KANDYDATA ===
{profile}

=== OFERTA ===
Tytuł: {title}
Firma: {company}
Lokalizacja: {location}
Wynagrodzenie: {salary}
Umiejętności: {skills}
Opis: {description}

Odpowiedz WYŁĄCZNIE tym JSON (bez markdown, bez komentarzy):
{{
  "dimensions": {{
    "people_leadership_fit": <0-10>,
    "role_seniority_fit": <0-10>,
    "product_agile_fit": <0-10>,
    "technical_credibility_fit": <0-10>,
    "growth_learning_fit": <0-10>,
    "conditions_fit": <0-10>
  }},
  "stretch_flag_line_management": <true|false>,
  "stretch_flag_english_c1": <true|false>,
  "booster_domain": <true|false>,
  "booster_ai": <true|false>,
  "key_gaps": ["<luka 1>", "<luka 2>"],
  "match_reason": "<max 2 zdania>",
  "cv_emphasis": "<network|management|product|devops>"
}}

Wskazówki do wymiarów (skala 0-10, oceniaj z perspektywy wymagań oferty vs udokumentowanych osiągnięć kandydata):
- people_leadership_fit: dopasowanie doświadczenia przywódczego do wymaganego przez ofertę
- role_seniority_fit: dopasowanie poziomu seniorności i zakresu odpowiedzialności
- product_agile_fit: dopasowanie doświadczenia product/agile do wymagań oferty
- technical_credibility_fit: wiarygodność techniczna wymagana przez ofertę vs posiadana
- growth_learning_fit: potencjał wzrostu i uczenia się w kontekście tej roli
- conditions_fit: lokalizacja, forma zatrudnienia, wynagrodzenie
"""


def _call_claude(prompt: str, max_tokens: int = 300) -> dict | None:
    import anthropic
    try:
        client = anthropic.Anthropic(api_key=os.environ["CLAUDE_API_KEY"])
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip() if msg.content else ""
        if not raw:
            logger.warning(f"[ANALYZER] pusta odpowiedź Claude (stop_reason={msg.stop_reason})")
            return None
        # Usuń ewentualne bloki markdown ```json ... ```
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        return json.loads(raw.strip())
    except json.JSONDecodeError as e:
        logger.warning(f"[ANALYZER] błąd JSON ({e}) – raw: {raw[:200]!r}")
        return None
    except Exception as e:
        logger.error(f"[ANALYZER] błąd Claude API: {e}")
        return None


def predict_salary(offer: dict) -> dict:
    """Przewiduje wynagrodzenie – Claude jeśli dostępny, reguły jako fallback."""
    if claude_available():
        prompt = SALARY_PREDICT_PROMPT.format(
            title=offer.get("title", ""),
            company=offer.get("company", ""),
            location=offer.get("location", ""),
            skills=", ".join(offer.get("skills", [])) or "brak danych",
            description=(offer.get("description", "") or "")[:1000],
        )
        result = _call_claude(prompt, max_tokens=150)
        if result:
            sal_from = result.get("salary_from", 0) or 0
            sal_to = result.get("salary_to", 0) or 0
            contract = result.get("contract_type", "unknown")
            confidence = result.get("confidence", "low")
            logger.info(f"[SALARY] {offer.get('title')} @ {offer.get('company')}: {sal_from:,}–{sal_to:,} PLN ({contract}) [{confidence}]")
            offer["salary"] = f"~{sal_from:,}–{sal_to:,} PLN ({contract}) [est.AI {confidence}]"
            offer["salary_from"] = sal_from
            offer["salary_predicted"] = True
            offer["salary_contract"] = contract
            return offer
        logger.warning(f"[SALARY] brak wyniku Claude dla: {offer.get('title')}")

    # Fallback – reguły rynkowe
    sal_from, sal_to = _rule_salary(offer)
    offer["salary"] = f"~{sal_from:,}–{sal_to:,} PLN UoP [est.reguły]"
    offer["salary_from"] = sal_from
    offer["salary_predicted"] = True
    offer["salary_contract"] = "UoP"
    return offer


def _salary_passes_filter(offer: dict) -> bool:
    sal_from = offer.get("salary_from", 0) or 0
    if sal_from <= 0:
        return True  # brak danych – przepuść

    contract = (offer.get("salary_contract", "") or "").lower()
    b2b = "b2b" in contract
    min_expected = SALARY_MIN_B2B - SALARY_TOLERANCE if b2b else SALARY_MIN_UOP - SALARY_TOLERANCE

    # Dla przewidywanych widełek używamy środka zakresu (są szersze i mniej pewne)
    if offer.get("salary_predicted"):
        sal_str = offer.get("salary", "")
        # Wyciągnij górną granicę z "~X–Y PLN"
        nums = re.findall(r"[\d\s,]+", sal_str.replace(",", "").replace(" ", ""))
        nums_int = []
        for n in nums:
            try:
                v = int(n.strip())
                if v > 1000:
                    nums_int.append(v)
            except ValueError:
                pass
        if len(nums_int) >= 2:
            midpoint = (nums_int[0] + nums_int[1]) // 2
            return midpoint >= min_expected
        # fallback: sam from
        return sal_from >= min_expected - 3000  # dodatkowy luz dla predykcji

    return sal_from >= min_expected


def analyze_offer(offer: dict) -> dict:
    """Analizuje dopasowanie – Claude jeśli dostępny, reguły jako fallback."""
    if claude_available():
        salary_display = offer.get("salary") or "brak danych"
        if offer.get("salary_predicted"):
            salary_display += " (przewidywane)"
        prompt = ANALYSIS_PROMPT.format(
            profile=CANDIDATE_PROFILE,
            title=offer.get("title", ""),
            company=offer.get("company", ""),
            location=offer.get("location", ""),
            salary=salary_display,
            skills=", ".join(offer.get("skills", [])) or "brak danych",
            description=(offer.get("description", "") or "")[:2000],
        )
        result = _call_claude(prompt, max_tokens=700)
        if result and "dimensions" in result:
            scored = compute_final_score(result)
            return {**offer, **scored}
        logger.warning(f"[ANALYZER] brak dimensions w odpowiedzi Claude dla: {offer.get('title')}")

    # Fallback – scoring regułowy
    scored = _rule_score(offer)
    return {**offer, **scored}


def _extract_salary_nums(text: str) -> list[int]:
    """Wyciąga liczby > 100 z tekstu wynagrodzenia."""
    cleaned = text.replace(",", "").replace("\xa0", "").replace(" ", "")
    nums = re.findall(r"\d{4,}", cleaned)
    result = []
    for n in nums:
        try:
            v = int(n)
            if v > 100:
                result.append(v)
        except ValueError:
            pass
    return result


def _normalize_salary_pln(offer: dict) -> dict:
    """
    Przelicza wynagrodzenie do PLN/mies. gdy wykryje obcą walutę lub okres roczny.
    Aktualizuje pola 'salary' (string) i 'salary_from' (int PLN/mies.).
    """
    salary_str = offer.get("salary", "") or ""
    if not salary_str:
        return offer

    sl = salary_str.lower()

    # Wykryj walutę
    if "usd" in sl or "$/yr" in sl:
        rate, cur_label = _USD_TO_PLN, "USD"
    elif "eur" in sl or "€" in sl:
        rate, cur_label = _EUR_TO_PLN, "EUR"
    elif "gbp" in sl or "£" in sl:
        rate, cur_label = _GBP_TO_PLN, "GBP"
    else:
        # PLN – sprawdź czy przypadkiem roczne (rzadkie)
        if "/rok" in sl or "/year" in sl:
            nums = _extract_salary_nums(salary_str)
            if nums:
                new_from = nums[0] // 12
                new_to   = nums[1] // 12 if len(nums) > 1 else 0
                new_str  = (f"{new_from:,}–{new_to:,} PLN/mies."
                            if new_to else f"od {new_from:,} PLN/mies.")
                return {**offer, "salary": new_str, "salary_from": new_from}
        return offer  # PLN/mies. – nic nie rób

    # Wykryj okres
    is_annual = "/yr" in sl or "/year" in sl or "annual" in sl
    divisor   = 12 if is_annual else 1

    nums = _extract_salary_nums(salary_str)
    if not nums:
        return offer

    from_pln = int(nums[0] * rate / divisor)
    to_pln   = int(nums[1] * rate / divisor) if len(nums) > 1 else 0

    if to_pln:
        new_str = f"~{from_pln:,}-{to_pln:,} PLN/mies. ({cur_label} x{rate})"
    else:
        new_str = f"~{from_pln:,}+ PLN/mies. ({cur_label} x{rate})"

    return {**offer, "salary": new_str, "salary_from": from_pln}


def analyze_all(offers: list[dict]) -> list[dict]:
    """
    Pipeline:
    0. Normalizacja walut → PLN/mies.
    1. Predykcja wynagrodzenia dla ofert bez widełek (Claude lub reguły)
    2. Filtr widełek (±2k tolerancji)
    3. Scoring wszystkich ofert (Claude lub reguły)
    4. Zwraca WSZYSTKIE oferty po filtrze widełek, posortowane score desc.
       Pole 'verdict' = APPLY (score≥6) lub SKIP.
    """
    using_claude = claude_available()
    mode = "Claude AI" if using_claude else "reguły lokalne (brak CLAUDE_API_KEY)"
    logger.info(f"[ANALYZER] Start – {len(offers)} ofert, tryb: {mode}")

    # Krok 0 – normalizacja walut do PLN/mies.
    offers = [_normalize_salary_pln(o) for o in offers]

    # Krok 1 – salary prediction (tylko oferty bez znanych widełek)
    needs_prediction = [o for o in offers if not o.get("salary") or o.get("salary_from", 0) == 0]
    has_salary       = [o for o in offers if o.get("salary") and o.get("salary_from", 0) > 0]
    logger.info(f"[ANALYZER] Predykcja wynagrodzenia dla {len(needs_prediction)} ofert ({mode})...")
    predicted  = [predict_salary(o) for o in needs_prediction]
    all_offers = has_salary + predicted

    # Krok 2 – filtr widełek (twarde minimum)
    filtered = [o for o in all_offers if _salary_passes_filter(o)]
    rejected = len(all_offers) - len(filtered)
    logger.info(f"[ANALYZER] Filtr widełek: {len(filtered)} przeszło, {rejected} odrzucono")

    # Krok 3 – scoring wszystkich ofert (APPLY i SKIP)
    scored = []
    for i, offer in enumerate(filtered, 1):
        logger.info(f"[ANALYZER] {i}/{len(filtered)} – {offer.get('title')} @ {offer.get('company')}")
        scored.append(analyze_offer(offer))

    scored.sort(key=lambda x: x.get("score", 0), reverse=True)
    apply_n = sum(1 for o in scored if o.get("verdict") == "APPLY")
    logger.info(f"[ANALYZER] {apply_n} APPLY  {len(scored) - apply_n} SKIP  łącznie: {len(scored)}")
    return scored
