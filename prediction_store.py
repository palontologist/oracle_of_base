"""
prediction_store.py
--------------------
PostgreSQL-backed store for all predictions issued by the Oracle.
Uses DATABASE_URL env var injected automatically by Railway.
"""

import os
import uuid
import json
import logging
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager

log = logging.getLogger("prediction_store")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable not set.")

# Railway injects postgres:// but psycopg2 needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)


@contextmanager
def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS predictions (
                    id              TEXT PRIMARY KEY,
                    agent_id        TEXT NOT NULL,
                    prediction_type TEXT NOT NULL,
                    subject         TEXT NOT NULL,
                    verdict         TEXT NOT NULL,
                    score           INTEGER NOT NULL,
                    raw_data        JSONB,
                    attestation_uid TEXT,
                    status          TEXT DEFAULT 'PENDING',
                    created_at      TIMESTAMPTZ NOT NULL,
                    resolve_after   TIMESTAMPTZ NOT NULL,
                    resolved_at     TIMESTAMPTZ,
                    resolution_uid  TEXT
                );

                CREATE TABLE IF NOT EXISTS resolutions (
                    id              TEXT PRIMARY KEY,
                    prediction_id   TEXT NOT NULL REFERENCES predictions(id),
                    outcome         TEXT NOT NULL,
                    accuracy        REAL NOT NULL,
                    actual_data     JSONB,
                    attestation_uid TEXT,
                    created_at      TIMESTAMPTZ NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_predictions_status
                    ON predictions(status);
                CREATE INDEX IF NOT EXISTS idx_predictions_resolve_after
                    ON predictions(resolve_after);
                CREATE INDEX IF NOT EXISTS idx_predictions_agent
                    ON predictions(agent_id);
            """)
    log.info("Database tables ready.")


def save_prediction(
    agent_id: str,
    prediction_type: str,
    subject: str,
    verdict: str,
    score: int,
    raw_data: dict,
    attestation_uid: str,
    resolve_after_hours: int = 24,
) -> str:
    """Persist a new prediction. Returns the prediction UUID."""
    prediction_id = str(uuid.uuid4())
    now           = datetime.now(timezone.utc)
    resolve_after = now + timedelta(hours=resolve_after_hours)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO predictions
                    (id, agent_id, prediction_type, subject, verdict, score,
                     raw_data, attestation_uid, status, created_at, resolve_after)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'PENDING', %s, %s)
                """,
                (
                    prediction_id, agent_id, prediction_type, subject,
                    verdict, score,
                    psycopg2.extras.Json(raw_data),
                    attestation_uid, now, resolve_after,
                ),
            )
    return prediction_id


def get_pending_for_resolution() -> list[dict]:
    """Return all PENDING predictions whose resolve_after time has passed."""
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM predictions
                WHERE status = 'PENDING' AND resolve_after <= %s
                ORDER BY resolve_after ASC
                """,
                (now,),
            )
            return [dict(r) for r in cur.fetchall()]


def save_resolution(
    prediction_id: str,
    outcome: str,
    accuracy: float,
    actual_data: dict,
    attestation_uid: str,
) -> str:
    """Persist a resolution and mark prediction RESOLVED."""
    resolution_id = str(uuid.uuid4())
    now           = datetime.now(timezone.utc)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO resolutions
                    (id, prediction_id, outcome, accuracy,
                     actual_data, attestation_uid, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    resolution_id, prediction_id, outcome, accuracy,
                    psycopg2.extras.Json(actual_data),
                    attestation_uid, now,
                ),
            )
            cur.execute(
                """
                UPDATE predictions
                SET status = 'RESOLVED',
                    resolved_at = %s,
                    resolution_uid = %s
                WHERE id = %s
                """,
                (now, attestation_uid, prediction_id),
            )
    return resolution_id


def get_reputation_stats(agent_id: str) -> dict:
    """Compute aggregate trust stats for an agent."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            cur.execute(
                """
                SELECT
                    COUNT(*)                                            AS total,
                    SUM(CASE WHEN r.outcome = 'TRUE'    THEN 1 ELSE 0 END) AS correct,
                    SUM(CASE WHEN r.outcome = 'FALSE'   THEN 1 ELSE 0 END) AS wrong,
                    SUM(CASE WHEN r.outcome = 'PARTIAL' THEN 1 ELSE 0 END) AS partial,
                    AVG(r.accuracy)                                     AS avg_accuracy
                FROM predictions p
                JOIN resolutions r ON r.prediction_id = p.id
                WHERE p.agent_id = %s
                """,
                (agent_id,),
            )
            totals = dict(cur.fetchone())

            cur.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM predictions
                WHERE agent_id = %s AND status = 'PENDING'
                """,
                (agent_id,),
            )
            pending = cur.fetchone()["cnt"]

            cur.execute(
                """
                SELECT p.prediction_type,
                       COUNT(*)        AS total,
                       AVG(r.accuracy) AS avg_accuracy
                FROM predictions p
                JOIN resolutions r ON r.prediction_id = p.id
                WHERE p.agent_id = %s
                GROUP BY p.prediction_type
                """,
                (agent_id,),
            )
            by_type = [dict(r) for r in cur.fetchall()]

    total   = int(totals["total"] or 0)
    correct = int(totals["correct"] or 0)
    trust_score = round(correct / total * 100, 2) if total > 0 else None

    return {
        "agent_id":       agent_id,
        "total_resolved": total,
        "correct":        correct,
        "wrong":          int(totals["wrong"] or 0),
        "partial":        int(totals["partial"] or 0),
        "pending":        int(pending),
        "avg_accuracy":   round(float(totals["avg_accuracy"] or 0), 4),
        "trust_score":    trust_score,
        "by_type":        by_type,
    }


# Initialise DB on import
init_db()
