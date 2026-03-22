"""
emotion_engine.py
-----------------
Emotion and intention signals for Oracle of Base trading.

Markets are made of humans (and agents) who feel things.
Technical signals tell you WHAT happened. 
Emotion signals tell you WHY it happened and WHAT HAPPENS NEXT.

Four emotion layers:
  1. MARKET EMOTION    — is the market in fear, greed, conviction, or confusion?
  2. DEPLOYER INTENTION — is this team building something or farming attention?
  3. COMMUNITY MOMENTUM — is sentiment growing, peaking, or collapsing?
  4. NARRATIVE STRENGTH — is there a real story, or just noise?

Venice reads all four layers and outputs a unified emotional edge thesis.
No rigid scoring. Venice reasons like an experienced trader reading the room.
"""

import os
import json
import time
import logging
import requests

log = logging.getLogger("emotion_engine")

VENICE_API_KEY  = os.getenv("VENICE_API_KEY", "")
VENICE_MODEL    = os.getenv("VENICE_MODEL", "qwen3-5-9b")
BASE_RPC_URL    = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")


class EmotionEngine:

    def __init__(self):
        pass

    # ── Signal collectors ──────────────────────────────────────────────────────

    def _collect_market_emotion(self, token_address: str, pair_data: dict) -> dict:
        """
        Read market emotion from on-chain price action.
        Fear leaves fingerprints. Greed leaves different ones.
        """
        try:
            price_changes = pair_data.get("priceChange", {})
            txns          = pair_data.get("txns", {})
            volume        = pair_data.get("volume", {})

            h1_change  = float(price_changes.get("h1",  0) or 0)
            h6_change  = float(price_changes.get("h6",  0) or 0)
            h24_change = float(price_changes.get("h24", 0) or 0)

            buys_h1    = int((txns.get("h1",  {}) or {}).get("buys",  0))
            sells_h1   = int((txns.get("h1",  {}) or {}).get("sells", 0))
            buys_h24   = int((txns.get("h24", {}) or {}).get("buys",  0))
            sells_h24  = int((txns.get("h24", {}) or {}).get("sells", 0))

            vol_h1  = float((volume.get("h1")  or 0))
            vol_h6  = float((volume.get("h6")  or 0))
            vol_h24 = float((volume.get("h24") or 0))

            # Volume acceleration — is the fire growing or dying?
            vol_accel = (vol_h1 * 6) / max(vol_h6, 1) if vol_h6 > 0 else 1.0

            # Buy/sell pressure ratio
            total_h24 = buys_h24 + sells_h24
            buy_pressure = buys_h24 / max(total_h24, 1)

            # Price momentum divergence — short vs long term
            momentum_divergence = h1_change - (h24_change / 24)

            return {
                "price_action": {
                    "h1_pct":        round(h1_change, 2),
                    "h6_pct":        round(h6_change, 2),
                    "h24_pct":       round(h24_change, 2),
                    "momentum_div":  round(momentum_divergence, 2),
                    "trend":         "accelerating" if h1_change > h6_change / 6 else
                                     "decelerating" if h1_change < h6_change / 6 else "steady",
                },
                "flow_signals": {
                    "buy_pressure_24h":   round(buy_pressure, 3),
                    "buys_h1":            buys_h1,
                    "sells_h1":           sells_h1,
                    "vol_acceleration":   round(vol_accel, 2),
                    "vol_h1_usd":         round(vol_h1, 0),
                    "vol_h24_usd":        round(vol_h24, 0),
                },
                "interpretation_hints": {
                    "is_panic_selling":   sells_h1 > buys_h1 * 2 and h1_change < -10,
                    "is_fomo_buying":     buys_h1 > sells_h1 * 2 and vol_accel > 2.0,
                    "is_distribution":    buy_pressure < 0.35 and h24_change > 20,
                    "is_accumulation":    buy_pressure > 0.65 and h24_change < 5,
                    "is_dead":            vol_h24 < 500 and total_h24 < 10,
                }
            }
        except Exception as e:
            return {"error": str(e)}

    def _collect_deployer_intention(self, token_address: str, deployer_signals: dict) -> dict:
        """
        Read deployer intention from their history and current token structure.
        Builders leave different traces than farmers. Ruggers leave different ones again.
        """
        intention_signals = {}

        # Previous token history
        prev_tokens = deployer_signals.get("previous_tokens", 0)
        rug_rate    = deployer_signals.get("rug_rate_pct", 0)
        thriving    = deployer_signals.get("thriving_count", 0)
        history     = deployer_signals.get("deployer_history", "unknown")

        if history == "known":
            intention_signals["track_record"] = {
                "tokens_launched": prev_tokens,
                "rug_rate_pct":    rug_rate,
                "thriving_count":  thriving,
                "pattern": (
                    "serial_rugger"    if rug_rate > 60 else
                    "repeat_builder"   if thriving > prev_tokens * 0.5 and prev_tokens > 2 else
                    "mixed_history"    if prev_tokens > 0 else
                    "first_launch"
                )
            }
        else:
            intention_signals["track_record"] = {
                "pattern": "unknown_deployer",
                "note": "No previous token history — read other signals for intent"
            }

        # Wallet behaviour from on-chain
        wallet_data = deployer_signals.get("wallet", {})
        if wallet_data:
            tx_count = wallet_data.get("total_tx_count", 0) or \
                       wallet_data.get("base_tx_count", 0)
            intention_signals["wallet_behaviour"] = {
                "transaction_count": tx_count,
                "age_signal":        wallet_data.get("activity_level", "unknown"),
                "cross_chain":       wallet_data.get("cross_chain_presence", False),
                "note": (
                    "Established multi-chain actor — less likely to rug and run" if
                    wallet_data.get("cross_chain_presence") and tx_count > 100
                    else "New or single-chain wallet — higher uncertainty on intention"
                    if tx_count < 20 else
                    "Active wallet with some history"
                )
            }

        # Token structural signals (honeypot indicators if available)
        token_profiles = deployer_signals.get("token_profiles", [])
        if token_profiles:
            recent = token_profiles[:3]
            outcomes = [t.get("outcome", "unknown") for t in recent]
            intention_signals["recent_token_outcomes"] = outcomes
            intention_signals["outcome_pattern"] = {
                "last_3": outcomes,
                "reads_as": (
                    "exits quickly after launch" if outcomes.count("rugged") >= 2 else
                    "builds and holds" if outcomes.count("thriving") >= 2 else
                    "inconsistent — read carefully"
                )
            }

        return intention_signals

    def _collect_social_emotion(self, token_symbol: str, promoter_signals: dict) -> dict:
        """
        Read emotional quality of social signals.
        Organic excitement feels different from coordinated hype.
        Genuine community feels different from bot amplification.
        """
        social = {}

        # Farcaster signals from existing promoter data
        fc_mentions   = promoter_signals.get("farcaster_mentions", []) or []
        mention_count = len(fc_mentions)
        bot_ratio     = float(promoter_signals.get("bot_ratio", 0) or 0)
        trusted_count = int(promoter_signals.get("trusted_promoters", 0) or 0)

        social["volume"] = {
            "mention_count":    mention_count,
            "trusted_mentions": trusted_count,
            "bot_ratio_pct":    round(bot_ratio * 100, 1),
        }

        # Sentiment quality — what are people actually saying?
        if fc_mentions:
            texts = [m.get("text", "") if isinstance(m, dict) else str(m) for m in fc_mentions[:10]]
            # Surface emotional keywords
            fear_words      = ["rug", "scam", "dump", "exit", "careful", "warning", "fake"]
            excitement_words = ["moon", "gem", "alpha", "early", "bullish", "huge", "massive"]
            fud_words       = ["dead", "over", "failed", "abandoned", "slow"]
            shill_words     = ["buy now", "don't miss", "guaranteed", "100x", "easy money"]

            full_text = " ".join(texts).lower()
            fear_hits      = sum(1 for w in fear_words      if w in full_text)
            excitement_hits = sum(1 for w in excitement_words if w in full_text)
            fud_hits        = sum(1 for w in fud_words       if w in full_text)
            shill_hits      = sum(1 for w in shill_words     if w in full_text)

            social["sentiment_texture"] = {
                "fear_signals":       fear_hits,
                "excitement_signals": excitement_hits,
                "fud_signals":        fud_hits,
                "shill_signals":      shill_hits,
                "dominant_tone": (
                    "coordinated_shill"  if shill_hits > 2 and bot_ratio > 0.4 else
                    "organic_excitement" if excitement_hits > fear_hits and bot_ratio < 0.2 else
                    "community_fear"     if fear_hits > excitement_hits else
                    "mixed_sentiment"
                ),
                "sample_texts": texts[:3],
            }
        else:
            social["sentiment_texture"] = {
                "dominant_tone": "no_social_signal",
                "note": "Token has not reached social awareness yet"
            }

        # Promoter credibility
        if trusted_count > 0:
            social["promoter_quality"] = {
                "trusted_actors":   trusted_count,
                "signal":           "high" if trusted_count > 3 else "moderate" if trusted_count > 0 else "low",
                "note": f"{trusted_count} trusted Farcaster accounts mentioned this token"
            }

        return social

    def _collect_narrative_momentum(self, token_address: str, token_signals: dict) -> dict:
        """
        Is there a real story building here, or just noise?
        Strong narratives compound. Weak ones evaporate.
        """
        narrative = {}

        symbol     = token_signals.get("symbol", "")
        name       = token_signals.get("name", "")
        liq        = float(token_signals.get("liquidity_usd", 0) or 0)
        fdv        = float(token_signals.get("fdv_usd", 0) or 0)
        age_hours  = float(token_signals.get("age_hours", 0) or 0)
        pair_count = int(token_signals.get("pair_count", 1) or 1)

        # Name/symbol pattern analysis
        name_lower = (name + " " + symbol).lower()
        ai_themed   = any(w in name_lower for w in ["ai", "agent", "gpt", "neural", "bot", "llm", "agi"])
        meme_themed = any(w in name_lower for w in ["dog", "cat", "pepe", "based", "shib", "degen", "ape"])
        defi_themed = any(w in name_lower for w in ["swap", "lend", "vault", "yield", "stake", "pool"])
        base_native = any(w in name_lower for w in ["base", "coinbase", "cb", "blue"])

        narrative["theme"] = {
            "is_ai_narrative":   ai_themed,
            "is_meme_narrative": meme_themed,
            "is_defi_narrative": defi_themed,
            "is_base_native":    base_native,
            "dominant": (
                "AI/agent" if ai_themed else
                "meme/culture" if meme_themed else
                "DeFi/utility" if defi_themed else
                "Base ecosystem" if base_native else
                "unclear"
            )
        }

        # Traction signals
        fdv_liq_ratio = fdv / max(liq, 1)
        narrative["traction"] = {
            "liquidity_usd":     round(liq, 0),
            "fdv_usd":           round(fdv, 0),
            "fdv_liq_ratio":     round(fdv_liq_ratio, 1),
            "pair_count":        pair_count,
            "age_hours":         round(age_hours, 1),
            "traction_signal": (
                "over-extended" if fdv_liq_ratio > 100 else
                "healthy_growth" if 5 < fdv_liq_ratio < 50 else
                "under-discovered" if fdv_liq_ratio < 5 else
                "speculative"
            ),
            "timing": (
                "very_early"   if age_hours < 2 else
                "early"        if age_hours < 12 else
                "established"  if age_hours < 72 else
                "mature"
            )
        }

        # Cross-listing signal (multiple pairs = more legitimacy)
        narrative["distribution"] = {
            "dex_pairs":  pair_count,
            "signal": "widely_listed" if pair_count > 3 else
                      "moderately_listed" if pair_count > 1 else
                      "single_market"
        }

        return narrative

    # ── Venice emotional synthesis ────────────────────────────────────────────

    def _consult_venice_emotion(self, all_signals: dict) -> dict:
        """
        Venice reads all emotional signals and reasons like an experienced trader.

        This is not a scoring function.
        Venice should output its intuitive read of the market's emotional state
        and what that implies for the next 24 hours.
        """
        prompt = f"""You are an experienced crypto trader who has survived multiple market cycles.
You have deep intuition for reading market emotion — you know the difference between genuine excitement and coordinated hype, between healthy accumulation and silent distribution, between a builder and a farmer.

You are looking at a brand new token on Base chain, minutes to hours after launch.
Your job is to read the emotional and intentional signals, not the technical ones.

Think about:
- What is the market feeling right now about this token?
- What does the deployer actually intend to do here?
- Is the community emotion building towards something real, or dissipating?
- What's the narrative — is there a story that will pull people in, or is this noise?
- If you had to bet on the next 24 hours based purely on the human dynamics here, what would you bet?

Do NOT repeat the raw numbers back. 
Do NOT give a mechanical score.
Reason from the signals like a trader who reads charts with their gut, not a spreadsheet.

════════ SIGNALS ════════
{json.dumps(all_signals, indent=2, default=str)}
═════════════════════════

Respond ONLY with valid JSON — no markdown:
{{
  "market_emotion": "<FEAR | GREED | CONVICTION | CONFUSION | EUPHORIA | EXHAUSTION>",
  "deployer_intention": "<BUILDING | EXITING | FARMING | UNKNOWN>",
  "community_momentum": "<GROWING | PEAKING | COLLAPSING | DORMANT | NASCENT>",
  "narrative_strength": "<STRONG | WEAK | MANUFACTURED | EMERGING | ABSENT>",
  "edge_thesis": "<2-4 sentences. Your gut read on what the market is actually doing here and why. Be direct. If you see a clear edge — say it. If you see a trap — say it.>",
  "trade_intuition": "<BUY | SELL | WAIT | AVOID>",
  "conviction": "<LOW | MEDIUM | HIGH>",
  "key_signal": "<The single most important signal that shaped your read — be specific>"
}}"""

        try:
            # Use Venice semaphore if available
            from threading import Semaphore
            try:
                from prophecy_engine import _venice_lock
                lock = _venice_lock
            except ImportError:
                lock = Semaphore(1)

            acquired = lock.acquire(timeout=90)
            if not acquired:
                raise TimeoutError("Venice lock timeout")
            try:
                r = requests.post(
                    "https://api.venice.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {VENICE_API_KEY}",
                        "Content-Type":  "application/json",
                    },
                    json={
                        "model":       VENICE_MODEL,
                        "messages":    [{"role": "user", "content": prompt}],
                        "temperature": 0.55,   # slightly higher — we want intuitive reasoning, not deterministic
                        "max_tokens":  350,
                    },
                    timeout=int(os.getenv("VENICE_TIMEOUT", "60")),
                )
                r.raise_for_status()
                raw = r.json()["choices"][0]["message"]["content"].strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                return json.loads(raw)
            finally:
                lock.release()

        except Exception as e:
            log.warning(f"Venice emotion read failed: {e}")
            return {
                "market_emotion":     "CONFUSION",
                "deployer_intention": "UNKNOWN",
                "community_momentum": "DORMANT",
                "narrative_strength": "ABSENT",
                "edge_thesis":        "Venice unavailable — no emotional read possible.",
                "trade_intuition":    "WAIT",
                "conviction":         "LOW",
                "key_signal":         "insufficient data",
            }

    # ── Main entrypoint ────────────────────────────────────────────────────────

    def read(
        self,
        token_address:   str,
        pair_data:       dict,       # raw DexScreener pair
        token_signals:   dict,       # from prophecy_engine
        deployer_signals: dict,      # from prophecy_engine
        promoter_signals: dict,      # from prophecy_engine
    ) -> dict:
        """
        Full emotional read on a token.
        Returns Venice's intuitive assessment of market emotion and intention.
        """
        log.info(f"Reading emotion for {token_address[:10]}...")

        market_emotion    = self._collect_market_emotion(token_address, pair_data)
        deployer_intention = self._collect_deployer_intention(token_address, deployer_signals)
        social_emotion    = self._collect_social_emotion(
            token_signals.get("symbol", ""), promoter_signals
        )
        narrative         = self._collect_narrative_momentum(token_address, token_signals)

        all_signals = {
            "token":          token_address,
            "symbol":         token_signals.get("symbol", "unknown"),
            "market_emotion": market_emotion,
            "deployer_intention": deployer_intention,
            "social_emotion": social_emotion,
            "narrative":      narrative,
        }

        venice_read = self._consult_venice_emotion(all_signals)

        return {
            **venice_read,
            "raw_emotional_signals": all_signals,
            "read_at": int(time.time()),
        }


_emotion_engine = None

def get_emotion_engine() -> EmotionEngine:
    global _emotion_engine
    if _emotion_engine is None:
        _emotion_engine = EmotionEngine()
    return _emotion_engine
