import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from datetime import date

logger = logging.getLogger(__name__)

SCORE_COLORS = {
    range(9, 11): "#1a7a1a",  # zielony – świetne dopasowanie
    range(7, 9):  "#e67e00",  # pomarańczowy – dobre
    range(0, 7):  "#555555",  # szary – przeciętne
}


def _score_color(score: int) -> str:
    for r, color in SCORE_COLORS.items():
        if score in r:
            return color
    return "#555555"


def _emphasis_badge(emphasis: str) -> str:
    badges = {
        "network":    ("🔌 Network",    "#e3f0ff"),
        "management": ("👥 Management", "#fff3cd"),
        "product":    ("📦 Product",    "#e8f5e9"),
        "devops":     ("⚙️ DevOps",     "#fce8ff"),
    }
    label, bg = badges.get(emphasis, ("—", "#f0f0f0"))
    return f'<span style="background:{bg};padding:2px 8px;border-radius:4px;font-size:11px">{label}</span>'


def _build_html(offers: list[dict]) -> str:
    today = date.today().strftime("%d.%m.%Y")
    rows = ""
    for o in offers:
        score = o.get("score", 0)
        color = _score_color(score)
        badge = _emphasis_badge(o.get("cv_emphasis", "management"))
        cv_filename = Path(o.get("cv_path", "")).name or "—"
        rows += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #eee">
            <strong>{o.get('company', '')}</strong>
          </td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee">
            <a href="{o.get('url', '#')}" style="color:#1a56db;text-decoration:none">
              {o.get('title', '')}
            </a>
          </td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;font-size:12px">
            {o.get('salary', '—')}
          </td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;font-size:12px">
            {o.get('location', '—')}
          </td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center">
            <strong style="color:{color}">{score}/10</strong>
          </td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee">{badge}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;font-size:11px;color:#555">
            {o.get('match_reason', '')}
          </td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;font-size:11px">
            {cv_filename}
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;color:#222;max-width:1100px;margin:auto;padding:20px">
  <h2 style="color:#1a56db">🎯 Job Hunter – Raport dzienny ({today})</h2>
  <p>Znaleziono <strong>{len(offers)}</strong> dopasowanych ofert.</p>
  <table style="width:100%;border-collapse:collapse;font-size:13px">
    <thead>
      <tr style="background:#f4f7ff">
        <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #1a56db">Firma</th>
        <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #1a56db">Rola</th>
        <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #1a56db">Wynagrodzenie</th>
        <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #1a56db">Lokalizacja</th>
        <th style="padding:10px 12px;text-align:center;border-bottom:2px solid #1a56db">Score</th>
        <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #1a56db">Tryb CV</th>
        <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #1a56db">Powód</th>
        <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #1a56db">Plik CV</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  <p style="font-size:11px;color:#999;margin-top:30px">
    Wygenerowano automatycznie przez Job Hunter · CV w załącznikach
  </p>
</body>
</html>"""


def send_report(offers: list[dict]) -> bool:
    """Wysyła email z raportem HTML i załączonymi plikami CV."""
    gmail_user = os.environ["GMAIL_USER"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ["RECIPIENT_EMAIL"]
    today = date.today().strftime("%d.%m.%Y")

    msg = MIMEMultipart("mixed")
    msg["From"] = gmail_user
    msg["To"] = recipient
    msg["Subject"] = f"[Job Hunter] {len(offers)} ofert – {today}"

    html_body = _build_html(offers)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # Załącz pliki CV
    attached = 0
    for offer in offers:
        cv_path = Path(offer.get("cv_path", ""))
        if cv_path.exists():
            with open(cv_path, "rb") as f:
                part = MIMEBase("application", "vnd.openxmlformats-officedocument.wordprocessingml.document")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=cv_path.name)
            msg.attach(part)
            attached += 1

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, recipient, msg.as_string())
        logger.info(f"[MAILER] Email wysłany ({len(offers)} ofert, {attached} załączników CV)")
        return True
    except Exception as e:
        logger.error(f"[MAILER] Błąd wysyłki: {e}")
        return False
