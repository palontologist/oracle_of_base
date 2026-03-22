"""
resolution_engine.py
---------------------
Wakes up every N minutes, finds predictions due for resolution,
checks actual outcomes via DexScreener / Farcaster, and issues
a signed Resolution Attestation on-chain.

Run standalone:
    python resolution_engine.py

Or import and call run_resolution_cycle() from a scheduler.
"""

import os
import json
import time
import logging
import requests
from datetime import datetime, timezone
from eth_account import Account
from eth_account.messages import encode_defunct
from dotenv import load_dotenv

from prediction_store import (
    get_pending_for_resolution,
    save_resolution,
    get_reputation_stats,
)

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("resolution_engine")

AGENT_ID      = "34499"
PRIVATE_KEY   = os.getenv("AGENT_PRIVATE_KEY")
POLL_INTERVAL = int(os.getenv("RESOLUTION_POLL_SECONDS", "300"))  # default 5 min

# ── Thresholds ────────────────────────────────────────────────────────────────
# A BLESSED token is considered a TRUE prediction if price didn't drop > 20%
# A CURSED token is TRUE if price dropped > 40% (rug confirmed)
BLESSED_CRASH_THRESHOLD = -0.20   # -20%
MORTAL_CRASH_THRESHOLD  = -0.40   # -40%
CURSED_CRASH_THRESHOLD  = -0.40   # -40% confirms CURSED call


# ── DexScreener helpers ───────────────────────────────────────────────────────

def fetch_token_data(token_address: str) -> dict | None:
    """Fetch current token data from DexScreener."""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        pairs = r.json().get("pairs") or []
        if not pairs:
            return None
        # Use the pair with highest liquidity
        return max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
    except Exception as e:
        log.warning(f"DexScreener fetch failed for {token_address}: {e}")
        return None


def resolve_token_prediction(prediction: dict) -> tuple[str, float, dict]:
    """
    Compare predicted verdict against actual 24h outcome.
    Returns (outcome, accuracy, actual_data).
    outcome  : 'TRUE' | 'FALSE' | 'PARTIAL' | 'UNRESOLVABLE'
    accuracy : 0.0 – 1.0
    """
    token_address = prediction["subject"]
    verdict       = prediction["verdict"]
    orig_score    = prediction["score"]
    # Postgres JSONB returns a dict directly — json.loads only needed for plain TEXT
    raw_data = prediction["raw_data"] or {}
    raw = raw_data if isinstance(raw_data, dict) else json.loads(raw_data)
    orig_price    = float(raw.get("price_usd") or 0)

    current = fetch_token_data(token_address)
    if not current:
        return "UNRESOLVABLE", 0.0, {"reason": "token_not_found"}

    current_price = float(current.get("priceUsd") or 0)
    price_change  = (
        (current_price - orig_price) / orig_price
        if orig_price > 0 else 0.0
    )
    liquidity_usd = float(
        current.get("liquidity", {}).get("usd") or 0
    )

    actual_data = {
        "token_address":  token_address,
        "orig_price_usd": orig_price,
        "curr_price_usd": current_price,
        "price_change_pct": round(price_change * 100, 2),
        "liquidity_usd":  liquidity_usd,
        "checked_at":     datetime.now(timezone.utc).isoformat(),
    }

    # ── Verdict resolution logic ──────────────────────────────────────────────
    if verdict == "BLESSED":
        if price_change >= BLESSED_CRASH_THRESHOLD:
            return "TRUE", 1.0, actual_data
        elif price_change >= BLESSED_CRASH_THRESHOLD * 2:
            return "PARTIAL", 0.5, actual_data
        else:
            return "FALSE", 0.0, actual_data

    elif verdict == "CURSED":
        if price_change <= CURSED_CRASH_THRESHOLD or liquidity_usd < 1000:
            return "TRUE", 1.0, actual_data
        elif price_change <= CURSED_CRASH_THRESHOLD / 2:
            return "PARTIAL", 0.5, actual_data
        else:
            return "FALSE", 0.0, actual_data

    elif verdict == "MORTAL":
        # MORTAL = risky. TRUE if it either held (-20%~+∞) or crashed hard
        # The signal was "be careful" — partial credit for any non-rug
        if price_change >= MORTAL_CRASH_THRESHOLD:
            return "TRUE", 0.8, actual_data
        else:
            return "PARTIAL", 0.4, actual_data

    return "UNRESOLVABLE", 0.0, actual_data


# ── Social resolution ─────────────────────────────────────────────────────────

def fetch_farcaster_data(handle: str) -> dict | None:
    """Fetch current Farcaster profile data."""
    try:
        url = f"https://api.neynar.com/v2/farcaster/user/search?q={handle}&limit=1"
        r = requests.get(url, timeout=10, headers={"Accept": "application/json"})
        r.raise_for_status()
        users = r.json().get("result", {}).get("users", [])
        return users[0] if users else None
    except Exception as e:
        log.warning(f"Farcaster fetch failed for {handle}: {e}")
        return None


def resolve_social_prediction(prediction: dict) -> tuple[str, float, dict]:
    """
    Validate social oracle prediction against current Farcaster data.
    """
    handle  = prediction["subject"]
    verdict = prediction["verdict"]
    raw_data = prediction["raw_data"] or {}
    raw = raw_data if isinstance(raw_data, dict) else json.loads(raw_data)
    orig_followers = int(raw.get("followers", 0))

    current = fetch_farcaster_data(handle)
    if not current:
        return "UNRESOLVABLE", 0.0, {"reason": "handle_not_found"}

    curr_followers  = int(current.get("follower_count", 0))
    curr_bio        = (current.get("profile", {}).get("bio", {}).get("text") or "").lower()
    follower_growth = (
        (curr_followers - orig_followers) / orig_followers
        if orig_followers > 0 else 0.0
    )

    bot_keywords  = {"bot", "autonomous", "agent", "ai", "automated"}
    bio_has_bot   = any(kw in curr_bio for kw in bot_keywords)

    actual_data = {
        "handle":          handle,
        "orig_followers":  orig_followers,
        "curr_followers":  curr_followers,
        "follower_growth": round(follower_growth * 100, 2),
        "bio_snippet":     curr_bio[:120],
        "checked_at":      datetime.now(timezone.utc).isoformat(),
    }

    # PURE AGENT should have bot-like bio and low/stable followers
    if verdict == "PURE AGENT":
        if bio_has_bot:
            return "TRUE", 1.0, actual_data
        elif follower_growth < 0.5:  # minimal organic growth → likely bot
            return "PARTIAL", 0.6, actual_data
        else:
            return "FALSE", 0.0, actual_data

    elif verdict == "HUMAN":
        if not bio_has_bot and follower_growth > 0.1:
            return "TRUE", 1.0, actual_data
        elif not bio_has_bot:
            return "PARTIAL", 0.6, actual_data
        else:
            return "FALSE", 0.0, actual_data

    elif verdict == "CYBORG":
        # Cyborg is inherently ambiguous — partial is the best we can do
        return "PARTIAL", 0.7, actual_data

    return "UNRESOLVABLE", 0.0, actual_data


# ── On-chain attestation ──────────────────────────────────────────────────────

def issue_resolution_attestation(
    prediction_id: str,
    prediction_uid: str,
    outcome: str,
    accuracy: float,
    actual_data: dict,
) -> str:
    """
    Signs and records a Resolution Attestation.
    In production this writes to EAS on Base.
    Returns the attestation UID (signature hash used as UID).
    """
    if not PRIVATE_KEY:
        log.warning("No AGENT_PRIVATE_KEY set — using mock attestation UID")
        return f"mock-resolution-{prediction_id[:8]}"

    payload = {
        "schema":        "RESOLUTION_V1",
        "agent_id":      AGENT_ID,
        "prediction_id": prediction_id,
        "ref_uid":       prediction_uid,
        "outcome":       outcome,
        "accuracy":      accuracy,
        "actual_data":   actual_data,
        "timestamp":     datetime.now(timezone.utc).isoformat(),
    }

    account = Account.from_key(PRIVATE_KEY)
    message_hash = encode_defunct(text=json.dumps(payload, sort_keys=True))
    signed = account.sign_message(message_hash)
    uid = signed.signature.hex()

    log.info(f"Resolution attestation issued | uid={uid[:16]}... | outcome={outcome}")
    return uid


# ── Core resolution cycle ─────────────────────────────────────────────────────

def run_resolution_cycle() -> list[dict]:
    """
    Main entry point. Finds all predictions due for resolution,
    evaluates outcomes, issues attestations, persists results.
    Returns list of resolution results for this cycle.
    """
    due = get_pending_for_resolution()
    if not due:
        log.info("No predictions due for resolution.")
        return []

    log.info(f"Found {len(due)} prediction(s) due for resolution.")
    results = []

    for pred in due:
        pred_id  = pred["id"]
        pred_uid = pred["attestation_uid"] or pred_id
        ptype    = pred["prediction_type"]
        subject  = pred["subject"]

        log.info(f"Resolving [{ptype}] {subject} | id={pred_id[:8]}...")

        try:
            if ptype == "token":
                outcome, accuracy, actual_data = resolve_token_prediction(pred)
            elif ptype == "social":
                outcome, accuracy, actual_data = resolve_social_prediction(pred)
            else:
                log.warning(f"Unknown prediction type: {ptype}")
                continue

            if outcome == "UNRESOLVABLE":
                log.warning(f"Could not resolve {subject} — skipping attestation")
                continue

            res_uid = issue_resolution_attestation(
                pred_id, pred_uid, outcome, accuracy, actual_data
            )

            save_resolution(pred_id, outcome, accuracy, actual_data, res_uid)

            result = {
                "prediction_id":  pred_id,
                "subject":        subject,
                "type":           ptype,
                "original_verdict": pred["verdict"],
                "outcome":        outcome,
                "accuracy":       accuracy,
                "resolution_uid": res_uid,
            }
            results.append(result)
            log.info(
                f"✓ Resolved {subject} | verdict={pred['verdict']} "
                f"→ outcome={outcome} | accuracy={accuracy:.0%}"
            )

        except Exception as e:
            log.error(f"Failed to resolve prediction {pred_id}: {e}", exc_info=True)

    # Log updated reputation after cycle
    stats = get_reputation_stats(AGENT_ID)
    log.info(
        f"Reputation update | trust_score={stats['trust_score']}% | "
        f"resolved={stats['total_resolved']} | "
        f"correct={stats['correct']} | wrong={stats['wrong']}"
    )

    return results


# ── Scheduler loop ────────────────────────────────────────────────────────────

def run_forever():
    """Run resolution cycles on a loop. Use this for standalone deployment."""
    log.info(f"Resolution engine started. Poll interval: {POLL_INTERVAL}s")
    while True:
        try:
            run_resolution_cycle()
        except Exception as e:
            log.error(f"Resolution cycle error: {e}", exc_info=True)
        log.info(f"Sleeping {POLL_INTERVAL}s until next cycle...")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run_forever()