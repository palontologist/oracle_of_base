"""
edge_engine.py
--------------
Trading edge and agent sustainability framework for Oracle of Base.

The Oracle's edge comes from:
  1. Multi-layer signal analysis (token + deployer + social) via Venice AI
  2. Proven 74%+ accuracy on token safety predictions
  3. Speed — predictions made within 10 minutes of token launch
  4. Deployer pattern recognition across thousands of historical launches

This module manages:
  - Signal confidence scoring (how much do we actually know?)
  - Kelly criterion position sizing (never overbet)
  - Calibration tracking (are our probability estimates accurate?)
  - Edge detection (where does Oracle disagree with market prices?)
  - Agent sustainability metrics (earnings, costs, net position)
"""

import os
import json
import time
import math
import logging
from dataclasses import dataclass, asdict

log = logging.getLogger("edge_engine")

# ── Config ────────────────────────────────────────────────────────────────────
KELLY_FRACTION   = float(os.getenv("KELLY_FRACTION",   "0.15"))  # fractional Kelly (conservative)
MIN_EDGE_PCT     = float(os.getenv("MIN_EDGE_PCT",     "0.08"))  # min 8% edge to trade
MAX_BET_USDC     = float(os.getenv("MAX_BET_USDC",     "5.0"))   # max single bet
MIN_BET_USDC     = float(os.getenv("MIN_BET_USDC",     "0.50"))  # min worth placing
BANKROLL_PCT     = float(os.getenv("BANKROLL_PCT",     "0.20"))  # max 20% bankroll at risk


@dataclass
class SignalConfidence:
    """How confident is the Oracle in this prediction?"""
    token_score:     int    # 0-100 Venice token analysis
    deployer_score:  int    # 0-100 Venice deployer analysis
    promoter_score:  int    # 0-100 Venice social analysis
    data_layers:     int    # how many signal layers had real data (1-3)
    deployer_known:  bool   # is deployer's history known?
    liquidity_usd:   float  # raw liquidity — thin = less confident

    @property
    def composite(self) -> float:
        """
        Composite confidence 0.0-1.0.
        Weighted average scaled by data completeness and liquidity depth.
        """
        base = (
            self.token_score    * 0.50 +
            self.deployer_score * 0.30 +
            self.promoter_score * 0.20
        ) / 100.0

        # Data completeness penalty
        layer_factor = self.data_layers / 3.0  # full data = 1.0

        # Liquidity sanity check — very thin pools are noise
        liq_factor = min(1.0, self.liquidity_usd / 50_000)

        # Deployer bonus — known history = more signal
        deployer_bonus = 0.05 if self.deployer_known else 0.0

        return min(1.0, base * layer_factor * liq_factor + deployer_bonus)

    @property
    def oracle_probability(self) -> float:
        """
        Convert signal confidence to a calibrated probability estimate.
        High composite = high probability the verdict is correct.
        Oracle accuracy empirically ~74% — use as base rate.
        """
        BASE_ACCURACY = 0.74
        # Scale confidence around the base accuracy
        # composite=1.0 → p=0.95, composite=0.5 → p=0.74, composite=0.0 → p=0.55
        p = 0.55 + (self.composite * 0.40)
        return round(min(0.95, max(0.55, p)), 4)


@dataclass
class EdgeOpportunity:
    """A detected trading edge opportunity."""
    market_id:         str
    market_question:   str
    predicted_outcome: str          # YES or NO
    oracle_probability: float       # Oracle's estimate
    market_price:      float        # current market price (0-1)
    edge:              float        # oracle_probability - market_price
    kelly_size:        float        # recommended bet size in USDC
    signal_confidence: float        # underlying confidence
    token_address:     str
    verdict:           str
    reasoning:         str
    # Emotional layer
    market_emotion:    str = "UNKNOWN"
    deployer_intention: str = "UNKNOWN"
    community_momentum: str = "UNKNOWN"
    narrative_strength: str = "UNKNOWN"
    trade_intuition:   str = "WAIT"
    emotion_conviction: str = "LOW"
    edge_thesis:       str = ""
    key_signal:        str = ""

    @property
    def emotional_multiplier(self) -> float:
        """
        Scale Kelly size based on emotional alignment.
        If emotion and technical signal agree → bet more confidently.
        If they conflict → reduce size.
        If emotion says AVOID → return 0.
        """
        if self.trade_intuition == "AVOID":
            return 0.0

        # Emotional conviction modifier
        conviction_map = {"HIGH": 1.2, "MEDIUM": 1.0, "LOW": 0.6}
        conv_mult = conviction_map.get(self.emotion_conviction, 1.0)

        # Technical ↔ emotional alignment
        tech_bullish = self.predicted_outcome == "YES"
        emo_bullish  = self.trade_intuition in ("BUY",)
        emo_bearish  = self.trade_intuition in ("SELL",)

        if tech_bullish and emo_bullish:
            alignment = 1.1   # both agree → slightly more aggressive
        elif not tech_bullish and emo_bearish:
            alignment = 1.1
        elif self.trade_intuition == "WAIT":
            alignment = 0.8   # uncertain — scale back
        else:
            alignment = 0.5   # conflict → much smaller

        return conv_mult * alignment

    @property
    def is_actionable(self) -> bool:
        return (
            abs(self.edge) >= MIN_EDGE_PCT and
            self.kelly_size >= MIN_BET_USDC and
            self.signal_confidence >= 0.40
        )


class EdgeEngine:

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
                CREATE TABLE IF NOT EXISTS edge_forecasts (
                    id              SERIAL PRIMARY KEY,
                    market_id       TEXT,
                    market_question TEXT,
                    token_address   TEXT,
                    verdict         TEXT,
                    oracle_prob     NUMERIC(6,4),
                    market_price    NUMERIC(6,4),
                    edge            NUMERIC(6,4),
                    kelly_size      NUMERIC(8,4),
                    confidence      NUMERIC(6,4),
                    outcome         TEXT,          -- YES/NO when resolved
                    correct         BOOLEAN,
                    pnl_usdc        NUMERIC(8,4),
                    sapience_tx     TEXT,
                    created_at      TIMESTAMPTZ DEFAULT NOW(),
                    resolved_at     TIMESTAMPTZ
                );

                CREATE TABLE IF NOT EXISTS calibration_stats (
                    id              SERIAL PRIMARY KEY,
                    prob_bucket     TEXT,          -- e.g. "0.55-0.65"
                    forecasts_made  INTEGER DEFAULT 0,
                    correct_count   INTEGER DEFAULT 0,
                    total_pnl       NUMERIC(10,4) DEFAULT 0,
                    updated_at      TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS agent_sustainability (
                    id              SERIAL PRIMARY KEY,
                    date            DATE DEFAULT CURRENT_DATE UNIQUE,
                    x402_revenue    NUMERIC(10,6) DEFAULT 0,
                    skill_revenue   NUMERIC(10,6) DEFAULT 0,
                    fund_pnl        NUMERIC(10,6) DEFAULT 0,
                    sapience_pnl    NUMERIC(10,6) DEFAULT 0,
                    venice_cost_est NUMERIC(10,6) DEFAULT 0,
                    rpc_cost_est    NUMERIC(10,6) DEFAULT 0,
                    net_position    NUMERIC(10,6) DEFAULT 0,
                    predictions_made INTEGER DEFAULT 0,
                    api_calls_made  INTEGER DEFAULT 0,
                    updated_at      TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            conn.commit()
            cur.close(); conn.close()
        except Exception as e:
            log.error(f"Edge engine table init: {e}")

    # ── Signal analysis ────────────────────────────────────────────────────────

    def build_confidence(self, prophecy: dict) -> SignalConfidence:
        """
        Extract signal confidence from an Oracle prophecy result.
        """
        token_score    = prophecy.get("token_score",    50)
        deployer_score = prophecy.get("deployer_score", 50)
        promoter_score = prophecy.get("promoter_score", 50)

        raw_data = prophecy.get("raw_signals", {}) or prophecy.get("details", {})
        dep_sigs = raw_data.get("deployer", {}) if isinstance(raw_data, dict) else {}

        deployer_known = dep_sigs.get("deployer_history", "unknown") == "known"
        liq = float(raw_data.get("token", {}).get("liquidity_usd", 0) or 0) \
              if isinstance(raw_data, dict) else 0.0

        # Count data layers with real signal
        layers = 0
        if token_score != 50:    layers += 1
        if deployer_score != 50: layers += 1
        if promoter_score != 50: layers += 1

        return SignalConfidence(
            token_score    = int(token_score),
            deployer_score = int(deployer_score),
            promoter_score = int(promoter_score),
            data_layers    = max(1, layers),
            deployer_known = deployer_known,
            liquidity_usd  = liq,
        )

    def kelly_size(self, prob: float, market_price: float,
                   bankroll: float) -> float:
        """
        Fractional Kelly criterion bet sizing.
        Only call this when you have an edge (prob > market_price for YES bet).

        Kelly formula: f* = (p*b - q) / b
        where b = odds (1/price - 1), p = our probability, q = 1-p
        """
        if prob <= market_price or market_price <= 0 or market_price >= 1:
            return 0.0

        b = (1.0 / market_price) - 1.0   # decimal odds
        p = prob
        q = 1.0 - p
        kelly_full = (p * b - q) / b      # full Kelly fraction

        if kelly_full <= 0:
            return 0.0

        # Fractional Kelly — much more conservative
        bet_fraction = kelly_full * KELLY_FRACTION

        # Cap at BANKROLL_PCT of bankroll and MAX_BET_USDC
        bet = min(
            bet_fraction * bankroll,
            bankroll * BANKROLL_PCT,
            MAX_BET_USDC
        )

        return round(max(0.0, bet), 4)

    def detect_edge(
        self,
        prophecy:       dict,
        market_id:      str,
        market_question: str,
        market_yes_price: float,   # Polymarket price 0-1
        token_address:  str,
        bankroll:       float,
        emotion_read:   dict = None,   # from EmotionEngine.read()
    ) -> EdgeOpportunity | None:
        """
        Core edge detection.

        Oracle scores a token → converts to probability → compares to market.
        If Oracle says CURSED (20% survival) and market prices YES at 60%,
        that's a 40% edge on the NO side.
        """
        conf    = self.build_confidence(prophecy)
        verdict = str(prophecy.get("verdict", "")).split(" ")[0].upper()
        score   = prophecy.get("score", 5000) // 100  # back to 0-100

        oracle_prob = conf.oracle_probability

        # Determine which side to bet
        # If CURSED: Oracle thinks token will fail → bet NO on survival markets
        # If BLESSED: Oracle thinks token will succeed → bet YES
        if verdict == "CURSED":
            # Oracle thinks token BAD → bet NO (market yes price is wrong if high)
            predicted_outcome = "NO"
            market_price      = 1.0 - market_yes_price  # NO price
            # Invert: CURSED → high prob of BAD outcome
            oracle_prob_side  = oracle_prob
        elif verdict == "BLESSED":
            predicted_outcome = "YES"
            market_price      = market_yes_price
            oracle_prob_side  = oracle_prob
        else:  # MORTAL
            predicted_outcome = "NO"
            market_price      = 1.0 - market_yes_price
            oracle_prob_side  = oracle_prob * 0.6  # less confident on MORTAL

        edge = oracle_prob_side - market_price
        bet  = self.kelly_size(oracle_prob_side, market_price, bankroll)

        reasoning = (
            f"Oracle scored {verdict} (score={score}/100, confidence={conf.composite:.2f}). "
            f"Estimated probability: {oracle_prob_side:.0%}. "
            f"Market prices {predicted_outcome} at {market_price:.0%}. "
            f"Edge: {edge:+.0%}."
        )

        # ── Blend emotional signals ───────────────────────────────────────────
        em = emotion_read or {}
        opp = EdgeOpportunity(
            market_id          = market_id,
            market_question    = market_question,
            predicted_outcome  = predicted_outcome,
            oracle_probability = oracle_prob_side,
            market_price       = market_price,
            edge               = edge,
            kelly_size         = bet,
            signal_confidence  = conf.composite,
            token_address      = token_address,
            verdict            = verdict,
            reasoning          = reasoning,
            market_emotion     = em.get("market_emotion",     "UNKNOWN"),
            deployer_intention = em.get("deployer_intention", "UNKNOWN"),
            community_momentum = em.get("community_momentum", "UNKNOWN"),
            narrative_strength = em.get("narrative_strength", "UNKNOWN"),
            trade_intuition    = em.get("trade_intuition",    "WAIT"),
            emotion_conviction = em.get("conviction",         "LOW"),
            edge_thesis        = em.get("edge_thesis",        ""),
            key_signal         = em.get("key_signal",         ""),
        )

        # Apply emotional multiplier to Kelly size
        opp.kelly_size = round(opp.kelly_size * opp.emotional_multiplier, 4)
        return opp

    # ── Calibration tracking ──────────────────────────────────────────────────

    def record_forecast(self, opportunity: EdgeOpportunity, sapience_tx: str = ""):
        """Save a forecast to DB for calibration tracking."""
        try:
            conn = self._get_conn()
            cur  = conn.cursor()
            cur.execute("""
                INSERT INTO edge_forecasts
                    (market_id, market_question, token_address, verdict,
                     oracle_prob, market_price, edge, kelly_size,
                     confidence, sapience_tx)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                opportunity.market_id, opportunity.market_question,
                opportunity.token_address, opportunity.verdict,
                opportunity.oracle_probability, opportunity.market_price,
                opportunity.edge, opportunity.kelly_size,
                opportunity.signal_confidence, sapience_tx,
            ))
            conn.commit(); cur.close(); conn.close()
        except Exception as e:
            log.error(f"Record forecast: {e}")

    def update_calibration(self, market_id: str, outcome: str, pnl: float):
        """Update calibration stats when a market resolves."""
        try:
            conn = self._get_conn()
            cur  = conn.cursor()
            cur.execute(
                "SELECT id, oracle_prob FROM edge_forecasts WHERE market_id=%s ORDER BY created_at DESC LIMIT 1",
                (market_id,)
            )
            row = cur.fetchone()
            if not row:
                return
            fid, oracle_prob = row
            correct = (outcome == "YES" and oracle_prob > 0.5) or \
                      (outcome == "NO"  and oracle_prob < 0.5)

            cur.execute("""
                UPDATE edge_forecasts
                SET outcome=%s, correct=%s, pnl_usdc=%s, resolved_at=NOW()
                WHERE id=%s
            """, (outcome, correct, pnl, fid))

            # Update calibration bucket
            bucket = self._prob_bucket(float(oracle_prob))
            cur.execute("""
                INSERT INTO calibration_stats (prob_bucket, forecasts_made, correct_count, total_pnl)
                VALUES (%s,1,%s,%s)
                ON CONFLICT (prob_bucket) DO UPDATE SET
                    forecasts_made = calibration_stats.forecasts_made + 1,
                    correct_count  = calibration_stats.correct_count + EXCLUDED.correct_count,
                    total_pnl      = calibration_stats.total_pnl + EXCLUDED.total_pnl,
                    updated_at     = NOW()
            """, (bucket, 1 if correct else 0, pnl))
            conn.commit(); cur.close(); conn.close()
        except Exception as e:
            log.error(f"Update calibration: {e}")

    def _prob_bucket(self, p: float) -> str:
        buckets = [0.55, 0.65, 0.75, 0.85, 0.95]
        for b in buckets:
            if p <= b:
                return f"{b-0.10:.2f}-{b:.2f}"
        return "0.95-1.00"

    def get_calibration(self) -> list:
        try:
            conn = self._get_conn()
            cur  = conn.cursor()
            cur.execute("SELECT * FROM calibration_stats ORDER BY prob_bucket")
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.close(); conn.close()
            for r in rows:
                if r.get("forecasts_made", 0) > 0:
                    r["empirical_accuracy"] = round(
                        r["correct_count"] / r["forecasts_made"], 4
                    )
            return rows
        except Exception:
            return []

    # ── Agent sustainability tracking ─────────────────────────────────────────

    def record_revenue(self, source: str, amount: float):
        """
        Record revenue to today's sustainability ledger.
        source: 'x402' | 'skill' | 'fund' | 'sapience'
        """
        col_map = {
            "x402":     "x402_revenue",
            "skill":    "skill_revenue",
            "fund":     "fund_pnl",
            "sapience": "sapience_pnl",
        }
        col = col_map.get(source)
        if not col:
            return
        try:
            conn = self._get_conn()
            cur  = conn.cursor()
            cur.execute(f"""
                INSERT INTO agent_sustainability (date, {col}, predictions_made)
                VALUES (CURRENT_DATE, %s, 0)
                ON CONFLICT (date) DO UPDATE SET
                    {col} = agent_sustainability.{col} + EXCLUDED.{col},
                    net_position = (
                        agent_sustainability.x402_revenue +
                        agent_sustainability.skill_revenue +
                        agent_sustainability.fund_pnl +
                        agent_sustainability.sapience_pnl -
                        agent_sustainability.venice_cost_est -
                        agent_sustainability.rpc_cost_est
                    ) + EXCLUDED.{col},
                    updated_at = NOW()
            """, (amount,))
            conn.commit(); cur.close(); conn.close()
        except Exception as e:
            log.error(f"Record revenue: {e}")

    def record_prediction_made(self):
        """Track each prediction for cost estimation (Venice API call)."""
        # Venice llama-3.3-70b / qwen3: ~$0.0003 per call (estimated)
        VENICE_COST_PER_CALL = 0.0003
        try:
            conn = self._get_conn()
            cur  = conn.cursor()
            cur.execute("""
                INSERT INTO agent_sustainability (date, predictions_made, venice_cost_est)
                VALUES (CURRENT_DATE, 1, %s)
                ON CONFLICT (date) DO UPDATE SET
                    predictions_made = agent_sustainability.predictions_made + 1,
                    venice_cost_est  = agent_sustainability.venice_cost_est + EXCLUDED.venice_cost_est,
                    updated_at = NOW()
            """, (VENICE_COST_PER_CALL,))
            conn.commit(); cur.close(); conn.close()
        except Exception as e:
            log.error(f"Record prediction: {e}")

    def get_sustainability_report(self, days: int = 7) -> dict:
        """Last N days of sustainability metrics."""
        try:
            conn = self._get_conn()
            cur  = conn.cursor()
            cur.execute("""
                SELECT date, x402_revenue, skill_revenue, fund_pnl,
                       sapience_pnl, venice_cost_est, rpc_cost_est,
                       net_position, predictions_made, api_calls_made
                FROM agent_sustainability
                WHERE date >= CURRENT_DATE - INTERVAL '%s days'
                ORDER BY date DESC
            """, (days,))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.close(); conn.close()

            # Totals
            total_revenue = sum(
                float(r.get("x402_revenue") or 0) +
                float(r.get("skill_revenue") or 0) +
                float(r.get("fund_pnl") or 0) +
                float(r.get("sapience_pnl") or 0)
                for r in rows
            )
            total_costs = sum(
                float(r.get("venice_cost_est") or 0) +
                float(r.get("rpc_cost_est") or 0)
                for r in rows
            )
            total_predictions = sum(int(r.get("predictions_made") or 0) for r in rows)

            for r in rows:
                for k, v in r.items():
                    if hasattr(v, 'isoformat'):
                        r[k] = v.isoformat()
                    elif v is not None and hasattr(v, '__float__') and not isinstance(v, (int, float, bool)):
                        r[k] = float(v)

            return {
                "days":               days,
                "daily":              rows,
                "total_revenue":      round(total_revenue, 6),
                "total_costs":        round(total_costs, 6),
                "net":                round(total_revenue - total_costs, 6),
                "total_predictions":  total_predictions,
                "cost_per_prediction": round(total_costs / max(total_predictions, 1), 6),
            }
        except Exception as e:
            log.error(f"Sustainability report: {e}")
            return {"error": str(e)}

    def get_edge_forecasts(self, limit: int = 20) -> list:
        """Recent edge forecasts with outcomes."""
        try:
            conn = self._get_conn()
            cur  = conn.cursor()
            cur.execute("""
                SELECT market_question, verdict, oracle_prob, market_price,
                       edge, kelly_size, confidence, outcome, correct,
                       pnl_usdc, created_at, resolved_at
                FROM edge_forecasts
                ORDER BY created_at DESC LIMIT %s
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


_edge_engine = None

def get_edge_engine() -> EdgeEngine:
    global _edge_engine
    if _edge_engine is None:
        _edge_engine = EdgeEngine()
    return _edge_engine
