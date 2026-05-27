"""
Wysyłka e-mail z raportem Job Hunter.
Wymaga w .env:
  GMAIL_USER         – adres nadawcy (konto Gmail)
  GMAIL_APP_PASSWORD – hasło aplikacji Gmail (nie hasło konta!)
  RECIPIENT_EMAIL    – adres odbiorcy
"""

import os
import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _email_available() -> bool:
    user = os.environ.get("GMAIL_USER", "")
    pwd  = os.environ.get("GMAIL_APP_PASSWORD", "")
    return bool(user and pwd and not pwd.startswith("xxxx"))


def _score_emoji(score) -> str:
    if not isinstance(score, int):
        return "⬜"
    if score >= 8:
        return "🟢"
    if score >= 6:
        return "🟡"
    return "🔴"


def _build_html(offers: list[dict], total_scraped: int, run_date: str) -> str:
    apply_offers = [o for o in offers if o.get("verdict") == "APPLY"]
    skip_offers  = [o for o in offers if o.get("verdict") != "APPLY"]

    def _row(o: dict, dimmed: bool = False) -> str:
        score    = o.get("score", "?")
        emoji    = _score_emoji(score)
        salary   = o.get("salary") or "—"
        if o.get("salary_predicted"):
            salary += " <small><i>(est.)</i></small>"
        reason   = o.get("match_reason") or ""
        emphasis = o.get("cv_emphasis") or "—"
        url      = o.get("url", "")
        style    = ' style="opacity:.55"' if dimmed else ""
        return f"""
        <tr{style}>
          <td style="text-align:center;font-size:1.1em">{emoji} <strong>{score}</strong></td>
          <td><a href="{url}" style="color:#1a73e8;text-decoration:none">
                <strong>{o.get('title','')}</strong></a><br>
              <small style="color:#555">{o.get('source','')}</small></td>
          <td>{o.get('company','—')}</td>
          <td>{o.get('location','—')}</td>
          <td>{salary}</td>
          <td style="color:#555;font-size:.85em">{reason}</td>
          <td style="text-align:center;font-size:.85em">{emphasis}</td>
        </tr>"""

    rows = "".join(_row(o) for o in apply_offers)
    if skip_offers:
        rows += f"""
        <tr>
          <td colspan="7" style="background:#f0f0f0;color:#888;font-size:.8em;padding:6px 8px">
            ▼ SKIP – score &lt; 6 ({len(skip_offers)} ofert spełniło warunki lokalizacji i wynagrodzenia, ale poniżej progu)
          </td>
        </tr>"""
        rows += "".join(_row(o, dimmed=True) for o in skip_offers)

    apply_count = len(apply_offers)

    return f"""<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="UTF-8">
  <style>
    body {{ font-family: Arial, sans-serif; color: #333; max-width: 1100px; margin: 0 auto; padding: 20px; }}
    h1   {{ color: #1a73e8; border-bottom: 2px solid #1a73e8; padding-bottom: 8px; }}
    .summary {{ background: #f8f9fa; border-left: 4px solid #1a73e8; padding: 12px 16px;
                margin: 16px 0; border-radius: 4px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 20px; font-size: .9em; }}
    th    {{ background: #1a73e8; color: white; padding: 10px 8px; text-align: left; }}
    tr:nth-child(even) {{ background: #f8f9fa; }}
    td    {{ padding: 8px; border-bottom: 1px solid #e0e0e0; vertical-align: top; }}
    .footer {{ margin-top: 24px; font-size: .8em; color: #999; }}
  </style>
</head>
<body>
  <h1>🔍 Job Hunter – Raport dzienny</h1>
  <div class="summary">
    <strong>Data:</strong> {run_date}<br>
    <strong>Przeszukano portali:</strong> LinkedIn, TheProtocol, Bulldogjob, JustJoinIT, NoFluffJobs, Pracuj.pl, RemoteOK, WeWorkRemotely, Himalayas<br>
    <strong>Unikalnych ofert znalezionych:</strong> {total_scraped}<br>
    <strong>APPLY (score ≥ 6):</strong> {apply_count} &nbsp;|&nbsp; <strong>SKIP:</strong> {len(offers) - apply_count} &nbsp;|&nbsp; <strong>Łącznie:</strong> {len(offers)}
  </div>

  <table>
    <thead>
      <tr>
        <th>Ocena</th>
        <th>Stanowisko</th>
        <th>Firma</th>
        <th>Lokalizacja</th>
        <th>Wynagrodzenie PLN/mies.</th>
        <th>Powód dopasowania</th>
        <th>Akcent CV</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>

  <div class="footer">
    Wygenerowano automatycznie przez Job Hunter · Pełne dane w załączonym pliku XLSX
  </div>
</body>
</html>"""


def send_report(
    offers: list[dict],
    csv_path: Path,
    total_scraped: int = 0,
) -> bool:
    """
    Wysyła e-mail z raportem HTML + CSV jako załącznik.
    Zwraca True jeśli wysyłka się powiodła.
    """
    if not _email_available():
        logger.warning("[EMAIL] Brak konfiguracji GMAIL – pomijam wysyłkę.")
        logger.warning("[EMAIL] Ustaw GMAIL_USER i GMAIL_APP_PASSWORD w .env")
        return False

    sender    = os.environ["GMAIL_USER"]
    password  = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ.get("RECIPIENT_EMAIL", sender)
    run_date  = datetime.now().strftime("%d.%m.%Y %H:%M")

    apply_count = sum(1 for o in offers if o.get("verdict") == "APPLY")
    subject = (
        f"Job Hunter {datetime.now().strftime('%d.%m.%Y')} – "
        f"{apply_count} ofert APPLY / {len(offers)} analizowanych"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Job Hunter <{sender}>"
    msg["To"]      = recipient

    # Część tekstowa (fallback)
    text_lines = [f"Job Hunter – Raport {run_date}", f"Znaleziono: {total_scraped} ofert\n"]
    for i, o in enumerate(offers, 1):
        text_lines.append(
            f"#{i:02d} {o.get('score','?')}/10  {o.get('title','')} @ {o.get('company','')}  "
            f"{o.get('salary','brak')}  {o.get('url','')}"
        )
    msg.attach(MIMEText("\n".join(text_lines), "plain", "utf-8"))

    # Część HTML
    html_body = _build_html(offers, total_scraped, run_date)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # Załącznik CSV
    if csv_path and csv_path.exists():
        with open(csv_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{csv_path.name}"',
        )
        msg.attach(part)

    # Wysyłka
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())
        logger.info(f"[EMAIL] Raport wysłany na {recipient}")
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error(
            "[EMAIL] Błąd autoryzacji Gmail. "
            "Sprawdź GMAIL_APP_PASSWORD w .env – musi być hasłem aplikacji, nie hasłem konta."
        )
    except Exception as e:
        logger.error(f"[EMAIL] Błąd wysyłki: {e}")
    return False
