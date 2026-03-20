"""
trust_engine.py
----------------
Combines token safety + deployer history + social promotion
into a single unified trust assessment.

This is the core of the Oracle's value proposition:
  - Token analysis alone can be fooled by a clean-looking contract
  - Social analysis alone misses on-chain risk
  - Together they catch what neither can alone
"""

import time
import hashlib
import json
import logging

log = logging.getLogger("trust_engine")


# ── Scoring weights ───────────────────────────────────────────────────────────

WEIGHTS = {
    "token":    0.50,   # on-chain metrics via DexScreener + Venice
    "deployer": 0.30,   # who deployed this contract
    "promoter": 0.20,   # who is pushing it on social
}

# Minimum deployer score before hard veto triggers
DEPLOYER_VETO_THRESHOLD = 20

# Minimum promoter score before bot-farm flag triggers
PROMOTER_VETO_THRESHOLD = 15


# ── Combined trust assessment ─────────────────────────────────────────────────

def calculate_combined_trust(
    token_score:    int,   # 0-100
    deployer_score: int,   # 0-100
    promoter_score: int,   # 0-100
    deployer_is_known_rugger: bool = False,
    bot_farm_detected: bool        = False,
) -> dict:
    """
    Combine three signal layers into a final trust verdict.

    Hard vetoes:
    - Known rugger deployer → immediate CURSED regardless of token score
    - Bot farm promotion    → immediate CURSED regardless of token score

    Returns a dict with final_score (0-100), verdict, and breakdown.
    """

    # ── Hard veto checks ──────────────────────────────────────────────────────
    if deployer_is_known_rugger:
        return {
            "final_score":   0,
            "verdict":       "CURSED",
            "veto_reason":   "known_rugger_deployer",
            "confidence":    "HIGH",
            "breakdown": {
                "token_score":    token_score,
                "deployer_score": deployer_score,
                "promoter_score": promoter_score,
            }
        }

    if bot_farm_detected:
        return {
            "final_score":   5,
            "verdict":       "CURSED",
            "veto_reason":   "bot_farm_promotion",
            "confidence":    "HIGH",
            "breakdown": {
                "token_score":    token_score,
                "deployer_score": deployer_score,
                "promoter_score": promoter_score,
            }
        }

    # ── Soft veto: very low deployer score pulls verdict down ─────────────────
    if deployer_score < DEPLOYER_VETO_THRESHOLD:
        # Cap token score contribution
        token_score = min(token_score, 40)

    # ── Weighted score ────────────────────────────────────────────────────────
    final_score = int(
        token_score    * WEIGHTS["token"]    +
        deployer_score * WEIGHTS["deployer"] +
        promoter_score * WEIGHTS["promoter"]
    )
    final_score = max(0, min(100, final_score))

    # ── Verdict ───────────────────────────────────────────────────────────────
    if final_score >= 70:
        verdict = "BLESSED"
    elif final_score >= 40:
        verdict = "MORTAL"
    else:
        verdict = "CURSED"

    # ── Confidence: how aligned are the three signals? ────────────────────────
    scores    = [token_score, deployer_score, promoter_score]
    spread    = max(scores) - min(scores)
    confidence = (
        "HIGH"   if spread < 20 else
        "MEDIUM" if spread < 40 else
        "LOW"
    )

    return {
        "final_score": final_score,
        "verdict":     verdict,
        "confidence":  confidence,
        "veto_reason": None,
        "breakdown": {
            "token_score":    token_score,
            "deployer_score": deployer_score,
            "promoter_score": promoter_score,
            "weights":        WEIGHTS,
        }
    }


# ── Full combined prophecy ────────────────────────────────────────────────────

def full_prophecy(
    token_address:   str,
    financial_prophet,
    social_prophet,
) -> dict:
    """
    Run a complete trust assessment on a token:
      1. Token safety analysis (DexScreener + Venice AI)
      2. Deployer history check
      3. Social promoter analysis (Farcaster)
      4. Combined scoring with hard vetoes

    Returns the most comprehensive trust signal the Oracle can produce.
    """
    start = time.time()
    log.info(f"Starting full prophecy for {token_address}")

    # ── Step 1: Full token + deployer + promoter analysis ────────────────────
    token_result = financial_prophet.consult_the_stars(token_address)

    # Only fail if DexScreener genuinely couldn't find the token.
    # Venice failures (score=0 with SILENT verdict) should still proceed
    # so deployer + promoter signals can contribute.
    error_msg = str(token_result.get('details', {}).get('error', ''))
    dexscreener_failed = (
        'not found' in error_msg.lower() or
        'no base pairs' in error_msg.lower() or
        'no data' in token_result.get('verdict', '').lower()
    )
    if dexscreener_failed:
        return {
            "status":        "failed",
            "reason":        "token_not_found",
            "token_address": token_address,
        }

    # If Venice failed but DexScreener worked, default token_score to 50
    # so deployer + promoter signals still contribute to final verdict
    if not token_result.get('token_score'):
        token_result['token_score'] = 50

    # Extract sub-scores from prophecy_engine's result
    # prophecy_engine now returns token_score, deployer_score, promoter_score
    raw_token_score    = token_result.get('token_score',    50)
    raw_deployer_score = token_result.get('deployer_score', 50)
    raw_promoter_score = token_result.get('promoter_score', 50)

    deployer_details  = token_result.get('details', {}).get('deployer', {})
    promoter_details  = token_result.get('details', {}).get('promoters', {})

    is_known_rugger   = deployer_details.get('details', {}).get('is_known_rugger', False)
    bot_farm_detected = promoter_details.get('details', {}).get('bot_farm_detected', False)

    # ── Step 2: Combined trust scoring ───────────────────────────────────────
    trust = calculate_combined_trust(
        token_score              = raw_token_score,
        deployer_score           = raw_deployer_score,
        promoter_score           = raw_promoter_score,
        deployer_is_known_rugger = is_known_rugger,
        bot_farm_detected        = bot_farm_detected,
    )

    # ── Step 3: Generate unified attestation ─────────────────────────────────
    attestation = financial_prophet.generate_attestation(token_address, {
        "score":          trust['final_score'] * 100,  # 0-10000 ERC-8004 scale
        "token_score":    raw_token_score,
        "deployer_score": raw_deployer_score,
        "promoter_score": raw_promoter_score,
    })

    elapsed = round(time.time() - start, 2)
    log.info(f"Full prophecy complete in {elapsed}s | verdict={trust['verdict']} | score={trust['final_score']}")

    return {
        "status":        "success",
        "token_address": token_address,
        "verdict":       trust['verdict'],
        "final_score":   trust['final_score'],
        "confidence":    trust['confidence'],
        "veto_reason":   trust['veto_reason'],
        "breakdown": {
            "token":    {
                "score":   raw_token_score,
                "details": token_result.get('details', {}),
                "reason":  token_result.get('verdict', ''),
            },
            "deployer": {
                "score":   raw_deployer_score,
                "details": deployer_details,
            },
            "promoter": {
                "score":   raw_promoter_score,
                "details": promoter_details,
            },
        },
        "attestation":   attestation,
        "elapsed_sec":   elapsed,
    }