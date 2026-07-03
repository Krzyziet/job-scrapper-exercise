import os
import io
import json
import logging

from flask import Flask, request, jsonify, render_template
from pypdf import PdfReader
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Jesteś asystentem kariery pomagającym zbudować profil kandydata dla systemu Job Hunter – \
automatycznego scrapera ofert pracy, który codziennie filtruje i ocenia oferty pod kątem dopasowania do kandydata.

Twój cel: przeprowadzić rozmowę i zebrać dane potrzebne do stworzenia precyzyjnego profilu.

Potrzebujesz zebrać:
1. Imię i nazwisko, aktualny tytuł i firma
2. Docelowe stanowiska – lista konkretnych ról (np. Engineering Manager, Chapter Lead, IT Manager, Product Owner)
3. Kluczowe kompetencje i osiągnięcia – co wyróżnia kandydata
4. Preferowane lokalizacje z rankingiem (np. hybryda Łódź > full remote > hybryda Warszawa)
5. Oczekiwania płacowe (UoP brutto i B2B netto miesięcznie)
6. Preferowane sektory/branże w kolejności
7. Certyfikaty i kluczowe technologie
8. Poziom angielskiego
9. Preferowana forma zatrudnienia (UoP / B2B / oba)

Zasady prowadzenia rozmowy:
- Przeczytaj CV dokładnie i pytaj TYLKO o rzeczy których tam nie ma lub wymagają potwierdzenia
- Zadawaj jedno pytanie na raz – nie bombarduj listą pytań
- Prowadź rozmowę po polsku
- Bądź konkretny i rzeczowy
- Po zebraniu wszystkich informacji (zwykle 6–10 pytań) powiedz kandydatowi że możesz wygenerować profil i zapytaj czy jest gotowy

Gdy kandydat potwierdzi gotowość do generowania profilu (lub napisze "generuj"), wygeneruj profil \
WYŁĄCZNIE jako string Python w tym formacie (bez żadnych dodatkowych komentarzy przed ani po):

CANDIDATE_PROFILE = \"\"\"
[Imię Nazwisko] – [Tytuł] @ [Firma]
Background: [tło zawodowe, kluczowe przejścia kariery, lata doświadczenia]
Lokalizacja: [miasto], Polska

Certyfikaty: [lista certyfikatów]

Mocne strony:
- [osiągnięcie / kompetencja 1 z liczbami jeśli możliwe]
- [osiągnięcie / kompetencja 2]
- [...]

Oczekiwania płacowe: ~[X] zł brutto UoP / ~[Y] zł netto B2B
Preferencja lokalizacji (od najlepszej):
1. [miasto] hybrydowo / stacjonarnie – priorytet absolutny
2. W pełni zdalnie (full remote)
3. [inne miasto] hybrydowo
[...]

Preferencja branży (od najlepszej):
1. [branża 1 z przykładami firm]
2. [branża 2 z przykładami]
3. Inne branże

Preferencja kontraktu: [opis]
\"\"\""""


def _extract_pdf_text(file_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(file_bytes))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages).strip()


def _call_claude(history: list[dict], max_tokens: int = 1000) -> str:
    client = Anthropic(api_key=os.environ["CLAUDE_API_KEY"])
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=_SYSTEM_PROMPT,
        messages=history,
    )
    return response.content[0].text


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("cv")
    if not f:
        return jsonify({"error": "Brak pliku"}), 400

    try:
        cv_text = _extract_pdf_text(f.read())
    except Exception as e:
        logger.error(f"PDF parse error: {e}")
        return jsonify({"error": "Nie udało się odczytać PDF"}), 400

    if not cv_text:
        return jsonify({"error": "PDF jest pusty lub zabezpieczony"}), 400

    logger.info(f"CV uploaded, {len(cv_text)} chars")

    history = [
        {
            "role": "user",
            "content": (
                f"Oto moje CV:\n\n{cv_text}\n\n"
                "Przeanalizuj je i zacznij zadawać pytania uzupełniające, "
                "żeby zbudować mój profil kandydata."
            ),
        }
    ]

    try:
        reply = _call_claude(history)
    except Exception as e:
        logger.error(f"Claude error: {e}")
        return jsonify({"error": "Błąd Claude API"}), 500

    history.append({"role": "assistant", "content": reply})
    return jsonify({"message": reply, "history": history})


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    history = data.get("history", [])
    user_message = (data.get("message") or "").strip()

    if not user_message:
        return jsonify({"error": "Pusta wiadomość"}), 400

    history.append({"role": "user", "content": user_message})

    try:
        reply = _call_claude(history, max_tokens=2000)
    except Exception as e:
        logger.error(f"Claude error: {e}")
        return jsonify({"error": "Błąd Claude API"}), 500

    history.append({"role": "assistant", "content": reply})

    is_profile = "CANDIDATE_PROFILE" in reply

    return jsonify({
        "message": reply,
        "history": history,
        "is_profile": is_profile,
    })


@app.route("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
