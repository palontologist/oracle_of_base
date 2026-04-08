"""
agents/pheromone.py — Stigmergic Pheromone Memory
--------------------------------------------------
Inspired by ant colony stigmergy: agents leave chemical traces that
decay over time. Strong consistent signals accumulate; flash-in-the-pan
tokens fade. No explicit memory — it emerges from decay + reinforcement.

PHEROMONE SCORE (0-10):
  - New prediction: score initialised from verdict (BLESSED=8, MORTAL=5, CURSED=2)
  - Every epoch (24h): score decays 20%
  - Correct resolution: +2.0 reinforcement
  - Incorrect resolution: −1.5 penalty
  - Deployer seen before: their avg score influences new token's prior

ANTI-GOODHART ROTATION:
  - Each epoch rotates which dimension gets extra weight
  - Prevents gaming by consistently optimising one signal
  - Rotation: token_signals → deployer_history → social_signals → all_equal

WHY THIS MATTERS:
  - DEGEN after 800 days has strong pheromone → MORTAL reads as "healthy survivor"
  - New rugger's second token inherits bad deployer pheromone
  - Flash pump-and-dump tokens decay to zero within 3 epochs
"""

import os, time, math, logging
log = logging.getLogger("pheromone")

DECAY_RATE    = float(os.getenv("PHEROMONE_DECAY",  "0.20"))  # 20% per epoch
EPOCH_HOURS   = float(os.getenv("PHEROMONE_EPOCH",  "24"))    # 24h epoch
REINFORCE_HIT = float(os.getenv("PHEROMONE_HIT",    "2.0"))
REINFORCE_MISS = float(os.getenv("PHEROMONE_MISS",  "-1.5"))

WEIGHT_DIMENSIONS = ["token", "deployer", "social", "balanced"]


class PheromoneMemory:

    AGENT_ID = "PHEROMONE"

    def __init__(self):
        self._init_tables()

    def _get_conn(self):
        from prediction_store import get_conn
        return get_conn()

    def _init_tables(self):
        try:
            conn = self._get_conn()
            cur  = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pheromone_scores (
                    id              SERIAL PRIMARY KEY,
                    address         TEXT NOT NULL,          -- token OR deployer address
                    address_type    TEXT NOT NULL,          -- 'token' | 'deployer'
                    symbol          TEXT,
                    score           NUMERIC(6,3) DEFAULT 5.0,
                    raw_score       NUMERIC(6,3) DEFAULT 5.0,
                    epoch_count     INTEGER DEFAULT 0,
                    correct_count   INTEGER DEFAULT 0,
                    prediction_count INTEGER DEFAULT 0,
                    last_verdict    TEXT,
                    last_reinforced TIMESTAMPTZ DEFAULT NOW(),
                    created_at      TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(address, address_type)
                );
                CREATE TABLE IF NOT EXISTS pheromone_epoch_log (
                    id          SERIAL PRIMARY KEY,
                    epoch_num   INTEGER,
                    weight_dim  TEXT,
                    tokens_decayed INTEGER,
                    ran_at      TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            conn.commit()
            cur.close(); conn.close()
        except Exception as e:
            log.error(f"[PHEROMONE] Table init: {e}")

    # ── Score management ──────────────────────────────────────────────────────

    def get_score(self, address: str, address_type: str = "token") -> float | None:
        """Get current pheromone score for a token or deployer. None = never seen."""
        try:
            conn = self._get_conn()
            cur  = conn.cursor()
            cur.execute(
                "SELECT score FROM pheromone_scores WHERE address=%s AND address_type=%s",
                (address.lower(), address_type)
            )
            row = cur.fetchone()
            cur.close(); conn.close()
            return float(row[0]) if row else None
        except Exception:
            return None

    def record_prediction(self, address: str, address_type: str,
                          verdict: str, symbol: str = "") -> float:
        """
        Called after each new prediction.
        Initialises or updates the pheromone score based on verdict.
        Returns the current score.
        """
        verdict_clean = verdict.split(" ")[0].upper()
        initial_score = {"BLESSED": 8.0, "MORTAL": 5.0, "CURSED": 2.0}.get(verdict_clean, 5.0)

        try:
            conn = self._get_conn()
            cur  = conn.cursor()
            cur.execute("""
                INSERT INTO pheromone_scores
                    (address, address_type, symbol, score, raw_score,
                     prediction_count, last_verdict)
                VALUES (%s, %s, %s, %s, %s, 1, %s)
                ON CONFLICT (address, address_type) DO UPDATE SET
                    prediction_count = pheromone_scores.prediction_count + 1,
                    last_verdict     = EXCLUDED.last_verdict,
                    -- Blend: 70% existing score + 30% new signal
                    score = (pheromone_scores.score * 0.7) + (EXCLUDED.score * 0.3),
                    raw_score = EXCLUDED.raw_score
                RETURNING score
            """, (address.lower(), address_type, symbol, initial_score, initial_score, verdict_clean))
            row = cur.fetchone()
            conn.commit(); cur.close(); conn.close()
            return float(row[0]) if row else initial_score
        except Exception as e:
            log.error(f"[PHEROMONE] record_prediction: {e}")
            return initial_score

    def reinforce(self, address: str, address_type: str, correct: bool):
        """
        Called when a prediction resolves. Reinforces or penalises the score.
        """
        delta = REINFORCE_HIT if correct else REINFORCE_MISS
        try:
            conn = self._get_conn()
            cur  = conn.cursor()
            cur.execute("""
                UPDATE pheromone_scores SET
                    score = GREATEST(0, LEAST(10, score + %s)),
                    correct_count = correct_count + CASE WHEN %s THEN 1 ELSE 0 END,
                    last_reinforced = NOW()
                WHERE address = %s AND address_type = %s
            """, (delta, correct, address.lower(), address_type))
            conn.commit(); cur.close(); conn.close()
            log.info(f"[PHEROMONE] Reinforce {address[:10]} | correct={correct} | delta={delta:+.1f}")
        except Exception as e:
            log.error(f"[PHEROMONE] reinforce: {e}")

    def get_deployer_prior(self, deployer_address: str) -> dict:
        """
        Get the deployer's accumulated pheromone as a prior for new token scoring.
        A deployer with score 8+ gets a trust boost; score 2- gets a penalty.
        """
        score = self.get_score(deployer_address, "deployer")
        if score is None:
            return {"deployer_pheromone": None, "prior": "unknown", "adjustment": 0}

        return {
            "deployer_pheromone": round(score, 2),
            "prior": (
                "strong_trust"  if score >= 7.5
                else "moderate" if score >= 4.5
                else "caution"  if score >= 2.5
                else "danger"
            ),
            "adjustment": round((score - 5.0) * 0.3, 2),  # -1.5 to +1.5 score adjustment
        }

    # ── Decay engine ──────────────────────────────────────────────────────────

    def run_decay_epoch(self) -> dict:
        """
        Decay all pheromone scores by DECAY_RATE.
        Also rotates the weight dimension (anti-Goodhart).
        Run every 24h from the resolution scheduler.
        """
        try:
            conn = self._get_conn()
            cur  = conn.cursor()

            # Get current epoch number
            cur.execute("SELECT COALESCE(MAX(epoch_num), 0) FROM pheromone_epoch_log")
            epoch_num = cur.fetchone()[0] + 1

            # Rotate weight dimension
            weight_dim = WEIGHT_DIMENSIONS[(epoch_num - 1) % len(WEIGHT_DIMENSIONS)]

            # Decay all scores
            cur.execute("""
                UPDATE pheromone_scores SET
                    score = GREATEST(0.1, score * %s),
                    epoch_count = epoch_count + 1
                WHERE last_reinforced < NOW() - INTERVAL '24 hours'
                RETURNING id
            """, (1.0 - DECAY_RATE,))
            decayed = len(cur.fetchall())

            cur.execute("""
                INSERT INTO pheromone_epoch_log (epoch_num, weight_dim, tokens_decayed)
                VALUES (%s, %s, %s)
            """, (epoch_num, weight_dim, decayed))

            conn.commit(); cur.close(); conn.close()
            log.info(f"[PHEROMONE] Epoch {epoch_num} | dim={weight_dim} | decayed={decayed} scores")
            return {"epoch": epoch_num, "weight_dim": weight_dim, "decayed": decayed}
        except Exception as e:
            log.error(f"[PHEROMONE] decay epoch: {e}")
            return {"error": str(e)}

    def get_current_weight_dim(self) -> str:
        """Which dimension gets extra weight this epoch?"""
        try:
            conn = self._get_conn()
            cur  = conn.cursor()
            cur.execute("SELECT weight_dim FROM pheromone_epoch_log ORDER BY epoch_num DESC LIMIT 1")
            row = cur.fetchone()
            cur.close(); conn.close()
            return row[0] if row else "balanced"
        except Exception:
            return "balanced"

    def top_tokens(self, limit: int = 10) -> list:
        """Highest pheromone tokens — the Oracle's most trusted signals."""
        try:
            conn = self._get_conn()
            cur  = conn.cursor()
            cur.execute("""
                SELECT address, symbol, score, prediction_count,
                       correct_count, last_verdict, last_reinforced
                FROM pheromone_scores
                WHERE address_type = 'token'
                ORDER BY score DESC LIMIT %s
            """, (limit,))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.close(); conn.close()
            for r in rows:
                for k, v in r.items():
                    if hasattr(v, 'isoformat'):
                        r[k] = v.isoformat()
                    elif v is not None and hasattr(v, '__float__') and not isinstance(v, (int, float, bool)):
                        r[k] = float(v)
            return rows
        except Exception:
            return []


_pheromone = None

def get_pheromone() -> PheromoneMemory:
    global _pheromone
    if _pheromone is None:
        _pheromone = PheromoneMemory()
    return _pheromone