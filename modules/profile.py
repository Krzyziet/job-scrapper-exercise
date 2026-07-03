CANDIDATE_PROFILE = """
Krzysztof Zielinski – Technical Product Owner @ ING Hubs Poland
Background: Inżynier sieciowy z ponad 5-letnim doświadczeniem, który przeszedł naturalną ścieżkę kariery w kierunku Product Ownera. Od 2023 roku odpowiedzialny za product ownership, roadmap i rozwój operacyjny 18-osobowego zespołu drugiej linii Network Operations wspierającego globalną infrastrukturę bankową. Wcześniej Network Service Engineer w Atende S.A. / NASK S.A. Wykształcenie techniczne (Computer Engineering, praca dyplomowa z SD-WAN). Pracuje na co dzień po angielsku (spotkania, prezentacje, emaile, vendorzy, klienci).
Lokalizacja: Łódź, Polska

Certyfikaty: PSPO I (2026), CCNP Enterprise (2022), Cisco Certified Specialist – Enterprise Advanced Infrastructure Implementation (2022), CCNA (2021)

Mocne strony:
- Wprowadzenie funkcji Tech Leada w zespole – wzrost zaangażowania, autonomii i sprawczości; zespół podejmuje bardziej złożone zadania i przejął znaczną część obowiązków zespołów L3; osiągnięcie nagrodzone Director's Award
- Redukcja podatności (vulnerability patching) z ponad 1000 otwartych pozycji do zera poprzez przebudowę procesów wewnętrznych zespołu
- Wzrost satysfakcji klienta dzięki regularnym spotkaniom, transparentności i poprawie komunikacji stakeholderskiej
- Udział we wdrożeniu klienta na platformę SD-WAN (end-to-end) oraz operacyjnych onboardingach klientów
- Translacja potrzeb biznesowych, bezpieczeństwa i operacyjnych na inicjatywy techniczne i backlog items
- Technologie: Cisco (CCNP), Palo Alto, F5, VMware ESXi, Zabbix, Nagios, SolarWinds, Arbor
- Narzędzia: Jira, Confluence, ServiceNow, Azure DevOps (Pipelines + Backlog); w trakcie kursu GCP
- Angielski: codzienne użycie zawodowe (spotkania, prezentacje, dokumentacja, klienci)

Oczekiwania płacowe: ~23 000 zł brutto UoP / ~25 000 zł netto B2B
Preferencja lokalizacji (od najlepszej):
1. Łódź hybrydowo – priorytet absolutny
2. W pełni zdalnie (full remote)
3. Warszawa hybrydowo – ostateczność

Preferencja branży (od najlepszej):
1. Sektor finansowy / bankowość (ING, Santander, PKO, mBank, Commerzbank, HSBC, BNP Paribas)
2. Sektor medyczny / healthcare (medtech, pharma, biotech)
3. Inne branże technologiczne
4. Wykluczone: firmy kontraktujące (Fujitsu, Accenture, Capgemini, Infosys itp.)

Docelowe stanowiska (od najbardziej preferowanych):
1. Product Owner (szczególnie zespoły Infra / DevOps / Platform)
2. Product Manager (obszar techniczny/infrastrukturalny)
3. Chapter Lead (zespoły inżynieryjne)
4. IT Manager / Engineering Manager
5. Process Owner (mniej priorytetowe, ale akceptowane)

Preferencja kontraktu: Preferowany UoP; B2B akceptowane jako alternatywa
"""

# ── Wagi wymiarów (suma = 1.0) ─────────────────────────────────────────────────
WEIGHTS: dict[str, float] = {
    "people_leadership_fit":    0.30,
    "role_seniority_fit":       0.20,
    "product_agile_fit":        0.15,
    "technical_credibility_fit": 0.15,
    "growth_learning_fit":      0.10,
    "conditions_fit":           0.10,
}

# Boostery – wartość * 10 = bonus punktowy (np. 0.10 * 10 = +1.0 pkt)
BOOSTERS: dict[str, float] = {
    "domain_match_networking_infra": 0.10,
    "ai_component":                  0.05,
}

# Mnożniki za stretch (nie dyskwalifikują, tylko obniżają)
STRETCH_PENALTIES: dict[str, float] = {
    "requires_formal_line_management": 0.85,
    "requires_english_c1":             0.92,
}

VERDICT_THRESHOLD: float = 6.0


def compute_final_score(result: dict) -> dict:
    """
    Liczy final_score na podstawie wymiarów i flag.
    Uzupełnia result o: final_score (float), score (int, wsteczna zgodność), verdict.
    """
    dims = result.get("dimensions", {})
    base = sum(dims.get(k, 0) * w for k, w in WEIGHTS.items())

    if result.get("stretch_flag_line_management"):
        base *= STRETCH_PENALTIES["requires_formal_line_management"]
    if result.get("stretch_flag_english_c1"):
        base *= STRETCH_PENALTIES["requires_english_c1"]

    bonus = 0.0
    if result.get("booster_domain"):
        bonus += BOOSTERS["domain_match_networking_infra"] * 10
    if result.get("booster_ai"):
        bonus += BOOSTERS["ai_component"] * 10

    final = round(min(base + bonus, 10.0), 2)
    return {
        **result,
        "final_score": final,
        "score": int(round(final)),
        "verdict": "APPLY" if final >= VERDICT_THRESHOLD else "SKIP",
    }
