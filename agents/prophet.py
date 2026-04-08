"""
agents/prophet.py — PROPHET (Orchestrator)
-------------------------------------------
The master coordinator of the Oracle of Base agent team.

AGENT TEAM:
  PROPHET    → orchestrates, calls Venice, produces final verdict
  SCARAB     → collects on-chain token signals (DexScreener, CoinGecko, Basescan)
  SEER       → reads social signals (Farcaster, bot detection, sentiment)
  PHEROMONE  → stigmergic memory (decaying scores, deployer priors)

PROPHET coordinates them, applies pheromone priors, builds the lifecycle
context, calls Venice with trimmed signals, and returns a unified verdict.

This replaces the single-agent prophecy_engine for new predictions.
prophecy_engine still handles legacy endpoints.
"""

import os, json, time, logging, requests, re
from threading import Semaphore

log = logging.getLogger("prophet")

VENICE_API_KEY  = os.getenv("VENICE_API_KEY", "")
VENICE_MODEL    = os.getenv("VENICE_MODEL", "qwen3-5-9b")
VENICE_TIMEOUT  = int(os.getenv("VENICE_TIMEOUT", "90"))

# Shared Venice semaphore — Prophet respects the same lock as prophecy_engine
try:
    from prophecy_engine import _venice_lock
except ImportError:
    _venice_lock = Semaphore(1)

PRIORITY_TOKEN_KEYS = {
    "symbol", "liquidity_usd", "volume_24h", "fdv", "age_hours", "age_days",
    "lifecycle_stage", "buy_ratio_24h", "fdv_liquidity_ratio", "vol_liquidity_ratio",
    "price_change_24h", "price_change_1h", "volume_trend", "total_txns_24h",
    "market_cap_rank", "developer_score", "github_commits_4w", "description",
}

PRIORITY_DEPLOYER_KEYS = {
    "deployer_history", "previous_tokens", "rugged_count", "thriving_count",
    "rug_rate_pct", "risk_implication",
}

PRIORITY_SOCIAL_KEYS = {
    "social_presence", "cast_count", "bot_promoters", "trusted_promoters",
    "sentiment_tone", "note",
}


class Prophet:
    """Multi-agent orchestrator for the Oracle of Base team."""

    AGENT_ID = "PROPHET"
    ROLE     = "orchestrator"
    VERSION  = "2.0"

    def __init__(self):
        from agents.scarab    import Scarab
        from agents.seer      import Seer
        from agents.pheromone import get_pheromone

        self.scarab    = Scarab()
        self.seer      = Seer()
        self.pheromone = get_pheromone()

    def consult(self, token_address: str) -> dict:
        """
        Full multi-agent oracle consultation.
        Coordinates SCARAB → SEER → PHEROMONE → Venice → verdict.
        """
        log.info(f"[PROPHET] Coordinating team for {token_address[:10]}...")
        started = time.time()

        # ── 1. SCARAB: on-chain signals ───────────────────────────────────────
        token_signals = self.scarab.fetch(token_address)
        if token_signals.get("error"):
            return {
                "score":   0,
                "verdict": "UNKNOWN",
                "error":   token_signals["error"],
                "details": token_signals,
            }

        symbol = token_signals.get("symbol", "")

        # ── 2. SEER: social signals ───────────────────────────────────────────
        social_signals = self.seer.fetch(symbol)

        # ── 3. Deployer signals (from existing engine) ────────────────────────
        deployer_signals = self._collect_deployer_signals(token_address)

        # ── 4. PHEROMONE: priors + deployer memory ────────────────────────────
        pheromone_prior   = self.pheromone.get_score(token_address, "token")
        deployer_prior    = self.pheromone.get_deployer_prior(
            deployer_signals.get("deployer_address", "")
        )
        weight_dim        = self.pheromone.get_current_weight_dim()

        # ── 5. Build lifecycle context ────────────────────────────────────────
        lifecycle = self._build_lifecycle(token_signals)

        # ── 6. Venice: reason across all signals ──────────────────────────────
        venice = self._call_venice(
            token_signals    = {k: v for k, v in token_signals.items() if k in PRIORITY_TOKEN_KEYS},
            deployer_signals = {k: v for k, v in deployer_signals.items() if k in PRIORITY_DEPLOYER_KEYS},
            social_signals   = {k: v for k, v in social_signals.items() if k in PRIORITY_SOCIAL_KEYS},
            lifecycle        = lifecycle,
            pheromone_prior  = pheromone_prior,
            deployer_prior   = deployer_prior,
            weight_dim       = weight_dim,
        )

        # ── 7. Final weighted score ───────────────────────────────────────────
        ts = venice["token_score"]
        ds = venice["deployer_score"]
        ps = venice["promoter_score"]

        # Apply pheromone adjustment if available
        phero_adj = deployer_prior.get("adjustment", 0)

        # Weight dimensions rotate (anti-Goodhart)
        weights = {
            "token":    (0.65, 0.25, 0.10),
            "deployer": (0.40, 0.50, 0.10),
            "social":   (0.45, 0.25, 0.30),
            "balanced": (0.50, 0.30, 0.20),
        }.get(weight_dim, (0.50, 0.30, 0.20))

        final_score = int(ts * weights[0] + ds * weights[1] + ps * weights[2])
        final_score = max(0, min(100, final_score + int(phero_adj * 10)))

        verdict_clean = venice["verdict"].split(" ")[0].upper()
        if verdict_clean not in ("BLESSED", "MORTAL", "CURSED"):
            verdict_clean = "BLESSED" if final_score >= 70 else "MORTAL" if final_score >= 40 else "CURSED"

        # ── 8. Record pheromone ───────────────────────────────────────────────
        self.pheromone.record_prediction(token_address, "token", verdict_clean, symbol)
        if deployer_signals.get("deployer_address"):
            self.pheromone.record_prediction(
                deployer_signals["deployer_address"], "deployer", verdict_clean
            )

        elapsed = round(time.time() - started, 2)
        log.info(f"[PROPHET] {symbol} | {verdict_clean} | score={final_score} | {elapsed}s | dim={weight_dim}")

        return {
            "score":          final_score * 100,  # match legacy format (score/100 = 0-100)
            "verdict":        verdict_clean,
            "token_score":    ts,
            "deployer_score": ds,
            "promoter_score": ps,
            "venice_reason":  venice["reason"],
            "price_usd":      token_signals.get("price_usd"),
            "ens_name":       None,
            "details": {
                "token":    token_signals,
                "deployer": deployer_signals,
                "promoter": social_signals,
                "pheromone": {
                    "token_score":    pheromone_prior,
                    "deployer_prior": deployer_prior,
                    "weight_dim":     weight_dim,
                },
                "agents_used":    [self.AGENT_ID, "SCARAB", "SEER", "PHEROMONE"],
                "elapsed_seconds": elapsed,
            },
        }

    def _build_lifecycle(self, token_signals: dict) -> str:
        age_h    = float(token_signals.get("age_hours", 0) or 0)
        age_d    = age_h / 24
        liq      = float(token_signals.get("liquidity_usd", 0) or 0)
        holders  = token_signals.get("holder_count", 0) or 0
        h_str    = f" Holders: {holders:,}." if holders else ""

        if age_h < 6:
            return f"BRAND NEW TOKEN — {age_h:.1f}h old. Zero track record. Weight rug signals heavily. Liq: ${liq:,.0f}."
        elif age_h < 72:
            return f"NEW TOKEN — {age_d:.1f}d old. Early signals forming. Watch for pump-and-dump shape. Liq: ${liq:,.0f}."
        elif age_d < 30:
            return f"GROWING TOKEN — {age_d:.0f}d old. Survival past launch is positive. Score trajectory not just rug risk. Liq: ${liq:,.0f}."
        elif age_d < 180:
            return f"MATURING TOKEN — {age_d:.0f}d old ({age_d/30:.1f}mo). Focus on activity, community health, trend. Liq: ${liq:,.0f}.{h_str}"
        else:
            return f"ESTABLISHED TOKEN — {age_d:.0f}d old ({age_d/365:.1f}yr). Rug risk low — score market health. Drawdown ≠ CURSED. Liq: ${liq:,.0f}.{h_str}"

    def _collect_deployer_signals(self, token_address: str) -> dict:
        """Delegate deployer signal collection to the existing engine."""
        try:
            from prophecy_engine import FinancialProphet
            fp = FinancialProphet.__new__(FinancialProphet)
            return fp.collect_deployer_signals(token_address)
        except Exception as e:
            log.debug(f"[PROPHET] Deployer signals: {e}")
            return {"deployer_history": "unknown"}

    def _call_venice(
        self,
        token_signals:    dict,
        deployer_signals: dict,
        social_signals:   dict,
        lifecycle:        str,
        pheromone_prior:  float | None,
        deployer_prior:   dict,
        weight_dim:       str,
    ) -> dict:

        if not VENICE_API_KEY:
            return {"token_score": 50, "deployer_score": 30, "promoter_score": 30,
                    "verdict": "MORTAL", "reason": "No Venice API key"}

        phero_str = ""
        if pheromone_prior is not None:
            phero_str = f"\nPHEROMONE MEMORY: This token has been seen before. Accumulated trust score: {pheromone_prior:.1f}/10. {'Strong positive track record.' if pheromone_prior >= 7 else 'Mixed history.' if pheromone_prior >= 4 else 'Negative history — treat with caution.'}"

        dep_str = ""
        if deployer_prior.get("deployer_pheromone") is not None:
            dep_str = f"\nDEPLOYER PHEROMONE: {deployer_prior['prior']} (score {deployer_prior['deployer_pheromone']:.1f}/10)"

        weight_note = {
            "token":    "This epoch: weight TOKEN signals more heavily (50% → 65%)",
            "deployer": "This epoch: weight DEPLOYER history more heavily (30% → 50%)",
            "social":   "This epoch: weight SOCIAL signals more heavily (20% → 30%)",
            "balanced": "This epoch: balanced weights across all signals",
        }.get(weight_dim, "")

        prompt = f"""You are The Oracle of Base — a multi-agent AI system scoring Base chain tokens.
You coordinate signals from specialist agents: SCARAB (on-chain), SEER (social), PHEROMONE (memory).

{weight_note}

════ LIFECYCLE ════
{lifecycle}

════ TOKEN SIGNALS (from SCARAB) ════
{json.dumps(token_signals, indent=2)}

════ DEPLOYER SIGNALS ════
{json.dumps(deployer_signals, indent=2)}{dep_str}

════ SOCIAL SIGNALS (from SEER) ════
{json.dumps(social_signals, indent=2)}{phero_str}

Score each layer 0-100. Apply the epoch weight dimension above.
For ESTABLISHED tokens: rug risk is low — score market health, not survival.
For NEW tokens: weight rug signals heavily.

Return ONLY this JSON:
{{
    "token_score":    <0-100>,
    "deployer_score": <0-100>,
    "promoter_score": <0-100>,
    "verdict":        "BLESSED" | "MORTAL" | "CURSED",
    "reason":         "<one sentence analyst read including lifecycle context>"
}}"""

        is_thinking = any(x in VENICE_MODEL for x in ["qwen3", "qwen2.5", "deepseek-r1"])
        payload = {
            "model":       VENICE_MODEL,
            "messages":    [
                {"role": "system", "content": "Expert DeFi analyst. Output ONLY valid JSON with exactly 5 fields: token_score, deployer_score, promoter_score, verdict, reason. No markdown."},
                {"role": "user",   "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens":  8000 if is_thinking else 400,
        }
        if is_thinking:
            payload["venice_parameters"] = {"include_venice_system_prompt": False}
            payload["thinking"] = {"type": "enabled", "budget_tokens": 6000}

        fallback = {"token_score": 50, "deployer_score": 30, "promoter_score": 30,
                    "verdict": "MORTAL", "reason": "Venice unavailable"}

        acquired = _venice_lock.acquire(timeout=120)
        if not acquired:
            return {**fallback, "reason": "Venice lock timeout"}

        try:
            for attempt in range(3):
                try:
                    r = requests.post(
                        "https://api.venice.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {VENICE_API_KEY}", "Content-Type": "application/json"},
                        json=payload, timeout=VENICE_TIMEOUT,
                    )
                    r.raise_for_status()
                    body = r.text.strip()
                    if not body:
                        time.sleep(3); continue

                    content = r.json()["choices"][0]["message"]["content"].strip()
                    if not content:
                        time.sleep(3); continue

                    # Strip thinking blocks
                    content = re.sub(r'<think>[\s\S]*?</think>', '', content, flags=re.IGNORECASE).strip()
                    if "```" in content:
                        content = content.split("```")[1]
                        if content.startswith("json"): content = content[4:]
                        content = content.split("```")[0].strip()
                    if not content.startswith("{"):
                        m = re.search(r'\{[\s\S]+\}', content)
                        if m: content = m.group(0)

                    parsed = json.loads(content)
                    return {
                        "token_score":    max(0, min(100, int(parsed.get("token_score",    50)))),
                        "deployer_score": max(0, min(100, int(parsed.get("deployer_score", 30)))),
                        "promoter_score": max(0, min(100, int(parsed.get("promoter_score", 30)))),
                        "verdict":        parsed.get("verdict", "MORTAL"),
                        "reason":         parsed.get("reason", ""),
                    }
                except Exception as e:
                    log.warning(f"[PROPHET] Venice attempt {attempt+1}: {e}")
                    if attempt < 2: time.sleep(3)
        finally:
            _venice_lock.release()

        return fallback


_prophet = None

def get_prophet() -> Prophet:
    global _prophet
    if _prophet is None:
        _prophet = Prophet()
    return _prophet