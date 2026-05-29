import json
import os
import logging
import sqlalchemy
from sqlalchemy import text

logger = logging.getLogger(__name__)
_engine = None


def get_engine():
    global _engine
    if _engine is None:
        host = os.environ["DB_HOST"]
        name = os.environ["DB_NAME"]
        user = os.environ["DB_USER"]
        password = os.environ["DB_PASSWORD"]
        url = f"postgresql+pg8000://{user}:{password}@{host}/{name}"
        _engine = sqlalchemy.create_engine(url, pool_pre_ping=True)
    return _engine


def init_db():
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS offers (
                id           SERIAL PRIMARY KEY,
                url          TEXT UNIQUE NOT NULL,
                title        TEXT,
                company      TEXT,
                location     TEXT,
                salary       TEXT,
                source       TEXT,
                score        SMALLINT,
                verdict      VARCHAR(10),
                match_reason TEXT,
                scraped_at   TIMESTAMPTZ DEFAULT NOW(),
                notified_at  TIMESTAMPTZ,
                missed_count SMALLINT DEFAULT 0,
                expired_at   TIMESTAMPTZ
            )
        """))
        # migracje istniejących tabel
        conn.execute(text("ALTER TABLE offers ADD COLUMN IF NOT EXISTS missed_count SMALLINT DEFAULT 0"))
        conn.execute(text("ALTER TABLE offers ADD COLUMN IF NOT EXISTS expired_at TIMESTAMPTZ"))
        conn.execute(text("ALTER TABLE offers ADD COLUMN IF NOT EXISTS dimensions JSONB"))
        conn.execute(text("ALTER TABLE offers ADD COLUMN IF NOT EXISTS final_score NUMERIC(4,2)"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS feedback (
                id         SERIAL PRIMARY KEY,
                offer_id   INTEGER REFERENCES offers(id),
                thumb      SMALLINT,
                reason     TEXT,
                outcome    VARCHAR(20),
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
    logger.info("[DB] Tabela offers i feedback gotowe")


def get_known_urls() -> set[str]:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT url FROM offers")).fetchall()
    return {row[0] for row in rows}


def insert_offers(offers: list[dict]) -> None:
    if not offers:
        return
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO offers (url, title, company, location, salary, source, score, verdict, match_reason, dimensions, final_score)
                VALUES (:url, :title, :company, :location, :salary, :source, :score, :verdict, :match_reason, :dimensions, :final_score)
                ON CONFLICT (url) DO NOTHING
            """),
            [
                {
                    "url":          o.get("url", ""),
                    "title":        o.get("title", ""),
                    "company":      o.get("company", ""),
                    "location":     o.get("location", ""),
                    "salary":       o.get("salary", ""),
                    "source":       o.get("source", ""),
                    "score":        o.get("score"),
                    "verdict":      o.get("verdict", ""),
                    "match_reason": o.get("match_reason", ""),
                    "dimensions":   json.dumps(o["dimensions"]) if o.get("dimensions") else None,
                    "final_score":  o.get("final_score"),
                }
                for o in offers
            ],
        )
    logger.info(f"[DB] Zapisano {len(offers)} ofert")


def update_offer_status(active_urls: set[str]) -> None:
    if not active_urls:
        return
    urls = list(active_urls)
    engine = get_engine()
    with engine.begin() as conn:
        # oferty które wróciły – resetuj licznik
        conn.execute(
            text("UPDATE offers SET missed_count = 0 WHERE url = ANY(:urls) AND missed_count > 0"),
            {"urls": urls},
        )
        # oferty nieobecne w tym scrapingu – zwiększ licznik
        conn.execute(
            text("UPDATE offers SET missed_count = missed_count + 1 WHERE url != ALL(:urls) AND expired_at IS NULL"),
            {"urls": urls},
        )
        # oznacz jako wygasłe po 2 nieobecnościach
        result = conn.execute(
            text("UPDATE offers SET expired_at = NOW() WHERE missed_count >= 2 AND expired_at IS NULL RETURNING url"),
        )
        expired_count = result.rowcount
    if expired_count:
        logger.info(f"[DB] Oznaczono {expired_count} ofert jako wygasłe")


def mark_notified(offers: list[dict]) -> None:
    if not offers:
        return
    urls = [o.get("url") for o in offers if o.get("url")]
    if not urls:
        return
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE offers SET notified_at = NOW() WHERE url = ANY(:urls)"),
            {"urls": urls},
        )
    logger.info(f"[DB] Oznaczono {len(urls)} ofert jako notified")


def add_feedback(url_or_offer_id, thumb: int, reason: str = None, outcome: str = None) -> None:
    """Zapisuje ocenę kandydata dla oferty. url_or_offer_id może być URL (str) lub offer.id (int)."""
    engine = get_engine()
    with engine.begin() as conn:
        if isinstance(url_or_offer_id, str):
            row = conn.execute(
                text("SELECT id FROM offers WHERE url = :url"),
                {"url": url_or_offer_id},
            ).fetchone()
            if not row:
                logger.warning(f"[DB] add_feedback: nie znaleziono oferty dla url={url_or_offer_id!r}")
                return
            offer_id = row[0]
        else:
            offer_id = url_or_offer_id
        conn.execute(
            text("""
                INSERT INTO feedback (offer_id, thumb, reason, outcome)
                VALUES (:offer_id, :thumb, :reason, :outcome)
            """),
            {"offer_id": offer_id, "thumb": thumb, "reason": reason, "outcome": outcome},
        )
    logger.info(f"[DB] Feedback zapisany dla offer_id={offer_id} thumb={thumb}")
