CANDIDATE_PROFILE = """
Krzysztof Zieliński – Technical Product Owner @ ING Hubs Poland
Background: Network Engineer → Product Owner (5+ lat doświadczenia)
Lokalizacja: Łódź, Polska

Certyfikaty: PSPO I, CCNP Enterprise, CCNA

Mocne strony:
- Zarządzanie 18-osobowym zespołem Network Operations (ING) – Director's Award
- Product ownership: roadmap planning, backlog management, sprint reviews
- Stakeholder management, ITSM, Incident & Change Management
- Technologie: Cisco, Palo Alto, F5, VMware ESXi
- Narzędzia: Jira, Confluence, HP Service Manager
- Angielski B2
- Branże: bankowość (ING), fintech, telco

Oczekiwania płacowe: ~23 000 zł brutto UoP / ~25 000 zł netto B2B
Preferencja lokalizacji (od najlepszej):
1. Łódź hybrydowo / stacjonarnie – priorytet absolutny
2. W pełni zdalnie (full remote)
3. Warszawa hybrydowo
4. Gdańsk / Gdynia / Sopot hybrydowo

Preferencja branży (od najlepszej):
1. Bankowość (ING, Commerzbank, Santander, mBank, PKO, HSBC, BNP Paribas i inne banki)
2. Fintech (szeroko: PayU, Revolut, Stripe, payments, neobank, crypto)
2. Healthcare (zdrowie, medtech, pharma, biotech)
3. Inne branże

Preferencja kontraktu: B2B+UoP (oba dostępne) lub samo B2B preferowane nad samym UoP
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
