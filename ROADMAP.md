# Job Hunter – Roadmap

## Aktywne źródła danych

| Portal | Metoda | Opis | Status |
|--------|--------|------|--------|
| JustJoinIT | REST API | Pełne dane: salary, skills, opisy, date filter | ✅ Produkcja |
| NoFluffJobs | REST API | Pełne dane: salary, skills, opisy, date filter | ✅ Produkcja |
| Pracuj.pl | Playwright + `__NEXT_DATA__` | Lokalizacja, opis preview, date filter | ✅ Produkcja |

---

## Planowane rozszerzenia

### LinkedIn via Apify
**Priorytet:** Wysoki  
**Uzasadnienie:** LinkedIn to kluczowe źródło ofert zarządczych (Chapter Lead, Engineering Manager, IT Manager) w dużych korporacjach i bankach. Bezpośrednie scrapowanie przez guest API nie dostarcza opisów, skills ani salary.

**Plan implementacji:**
- Użyć aktora Apify [`bebity/linkedin-jobs-scraper`](https://apify.com/bebity/linkedin-jobs-scraper) lub podobnego
- Integracja przez Apify API (klucz w Secret Manager / `.env`)
- Filtrowanie po słowach kluczowych i lokalizacji po stronie Apify
- Mapowanie wyników na wspólny schemat oferty (`title`, `company`, `location`, `salary`, `description`, `skills`, `url`)
- Dodanie do `scrape_all()` w `scraper.py` jako kolejny scraper

**Do zrobienia:**
- [ ] Założyć konto Apify i ustawić klucz API w `.env` jako `APIFY_API_KEY`
- [ ] Przetestować aktora na próbce (10–20 ofert) przez `debug_linkedin_apify.py`
- [ ] Zaimplementować `scrape_linkedin()` w `scraper.py`
- [ ] Dodać alias `linkedin` w `_PORTAL_ALIASES`

---

### Potencjalne kolejne portale

| Portal | Uwagi |
|--------|-------|
| **Indeed PL** | Wymaga rejestracji Publisher API (bezpłatna) |
| **Rocket Jobs** | White-label JJIT – prawdopodobnie duplikaty JJIT |
| **Praca.pl** | Do zbadania: `__NEXT_DATA__` lub REST API |
