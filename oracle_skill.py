"""
oracle_skill.py
----------------
The Oracle Teacher — a tiered skill sold to other agents via x402.

Agents pay once to unlock a skill level. Higher trust = deeper signal.

Tier 1 — Apprentice  ($0.10 USDC): Basic rug detection
Tier 2 — Seer        ($0.50 USDC): Full token + deployer analysis  
Tier 3 — Prophet     ($2.00 USDC): Combined signal + social + historical accuracy

Each tier returns working Python code the agent installs in its own workflow.
The code calls back to the Oracle API for live signals — so it stays fresh
and the Oracle earns on every prediction the student agent makes.
"""

import os
import time
import hashlib
import json
import requests
from flask import Blueprint, request, jsonify

skill_bp = Blueprint('skill', __name__)

AGENT_ID       = os.getenv("AGENT_ID", "34499")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "0x1EA37E2Fb76Aa396072204C90fcEF88093CEb920")
ORACLE_URL     = os.getenv("ORACLE_URL", "https://your-app.railway.app")


# ── Skill definitions ─────────────────────────────────────────────────────────

SKILLS = {
    "apprentice": {
        "name":        "Oracle Apprentice",
        "level":       1,
        "price_usdc":  0.10,
        "description": "Basic rug detection — bytecode analysis + liquidity check",
        "capabilities": [
            "Contract bytecode scan",
            "Self-destruct / mint pattern detection",
            "Basic liquidity check via DexScreener",
            "BLESSED / MORTAL / CURSED verdict",
        ],
    },
    "seer": {
        "name":        "Oracle Seer",
        "level":       2,
        "price_usdc":  0.50,
        "description": "Full token + deployer analysis — know who built it",
        "capabilities": [
            "Everything in Apprentice",
            "Deployer wallet history",
            "Previous rug rate calculation",
            "On-chain tx pattern analysis",
            "Confidence score per signal layer",
        ],
    },
    "prophet": {
        "name":        "Oracle Prophet",
        "level":       3,
        "price_usdc":  2.00,
        "description": "Full combined signal — the Oracle's complete sight",
        "capabilities": [
            "Everything in Seer",
            "Social promotion analysis (Farcaster)",
            "Bot farm detection",
            "Historical Oracle accuracy on similar tokens",
            "Priority signal access (new tokens within 10 min of launch)",
        ],
    },
}


# ── Skill code templates ──────────────────────────────────────────────────────

def generate_apprentice_code(license_key: str) -> str:
    return f'''"""
Oracle Apprentice Skill — Level 1
License: {license_key}
Issued by Oracle of Base ({ORACLE_URL})

Install: pip install requests
"""

import requests

ORACLE_URL   = "{ORACLE_URL}"
LICENSE_KEY  = "{license_key}"


class OracleApprentice:
    """
    Basic token safety checker powered by Oracle of Base.
    Checks on-chain bytecode + liquidity signals.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({{
            "X-Oracle-License": LICENSE_KEY,
            "User-Agent":       "OracleApprentice/1.0",
        }})

    def prophesy(self, token_address: str) -> dict:
        """
        Run a basic prophecy on a Base token.
        Returns verdict, score, and key risk signals.
        """
        try:
            resp = self.session.get(
                f"{{ORACLE_URL}}/prophecy",
                params={{"token": token_address}},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            prophecy = data.get("prophecy", {{}})
            verdict  = prophecy.get("verdict", "UNKNOWN").split(" ")[0]

            return {{
                "token":    token_address,
                "verdict":  verdict,
                "score":    prophecy.get("score", 0) // 100,
                "is_rug":   verdict == "CURSED",
                "details":  prophecy.get("details", {{}}),
                "license":  "apprentice",
            }}
        except Exception as e:
            return {{"error": str(e), "token": token_address}}

    def is_safe(self, token_address: str) -> bool:
        """Quick boolean check — is this token BLESSED?"""
        result = self.prophesy(token_address)
        return result.get("verdict") == "BLESSED"


# Usage:
# from oracle_apprentice import OracleApprentice
# oracle = OracleApprentice()
# prophecy = oracle.prophesy("0xTOKEN_ADDRESS")
# print(f"Verdict: {{prophecy[\'verdict\']}}")
# print(f"Score: {{prophecy[\'score\']}}/100")
# if prophecy["is_rug"]:
#     print("WARNING: DO NOT TOUCH THIS TOKEN")
'''


def generate_seer_code(license_key: str) -> str:
    return f'''"""
Oracle Seer Skill — Level 2
License: {license_key}
Issued by Oracle of Base ({ORACLE_URL})

Install: pip install requests
"""

import requests

ORACLE_URL   = "{ORACLE_URL}"
LICENSE_KEY  = "{license_key}"


class OracleSeer:
    """
    Full token + deployer analysis powered by Oracle of Base.
    Checks on-chain signals, deployer history, and trust layers.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({{
            "X-Oracle-License": LICENSE_KEY,
            "User-Agent":       "OracleSeer/1.0",
        }})

    def prophesy(self, token_address: str) -> dict:
        """
        Full prophecy including deployer history.
        Returns verdict, score breakdown, and deployer risk.
        """
        try:
            resp = self.session.get(
                f"{{ORACLE_URL}}/combined-prophecy",
                params={{"token": token_address}},
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()

            verdict = data.get("verdict", "UNKNOWN")
            verdict_clean = verdict.split(" ")[0] if " " in verdict else verdict

            deployer = data.get("breakdown", {{}}).get("deployer", {{}})
            is_known_rugger = (
                deployer.get("details", {{}})
                .get("details", {{}})
                .get("is_known_rugger", False)
            )

            return {{
                "token":            token_address,
                "verdict":          verdict_clean,
                "final_score":      data.get("final_score", 0),
                "confidence":       data.get("confidence", "LOW"),
                "is_rug":           verdict_clean == "CURSED",
                "is_known_rugger":  is_known_rugger,
                "veto_reason":      data.get("veto_reason"),
                "breakdown": {{
                    "token_score":    data.get("breakdown", {{}}).get("token", {{}}).get("score", 0),
                    "deployer_score": data.get("breakdown", {{}}).get("deployer", {{}}).get("score", 0),
                    "promoter_score": data.get("breakdown", {{}}).get("promoter", {{}}).get("score", 0),
                }},
                "deployer": deployer.get("details", {{}}),
                "license":  "seer",
            }}
        except Exception as e:
            return {{"error": str(e), "token": token_address}}

    def check_deployer(self, token_address: str) -> dict:
        """Check deployer history only — fast check before full prophecy."""
        result = self.prophesy(token_address)
        return {{
            "is_known_rugger": result.get("is_known_rugger", False),
            "deployer_score":  result.get("breakdown", {{}}).get("deployer_score", 0),
            "veto_reason":     result.get("veto_reason"),
        }}

    def is_safe_to_trade(self, token_address: str) -> bool:
        """
        Returns True only if token is BLESSED with HIGH confidence
        and deployer has clean history.
        """
        result = self.prophesy(token_address)
        return (
            result.get("verdict") == "BLESSED" and
            result.get("confidence") == "HIGH" and
            not result.get("is_known_rugger", True)
        )


# Usage:
# from oracle_seer import OracleSeer
# oracle = OracleSeer()
# prophecy = oracle.prophesy("0xTOKEN_ADDRESS")
# print(f"Verdict: {{prophecy[\'verdict\']}}")
# print(f"Confidence: {{prophecy[\'confidence\']}}")
# print(f"Breakdown: {{prophecy[\'breakdown\']}}")
# if prophecy["is_known_rugger"]:
#     print("DEPLOYER IS A KNOWN RUGGER — HARD PASS")
'''


def generate_prophet_code(license_key: str) -> str:
    return f'''"""
Oracle Prophet Skill — Level 3
License: {license_key}
Issued by Oracle of Base ({ORACLE_URL})

Install: pip install requests
"""

import requests

ORACLE_URL   = "{ORACLE_URL}"
LICENSE_KEY  = "{license_key}"


class OracleProphet:
    """
    The full Oracle sight — token + deployer + social + historical accuracy.
    Highest fidelity signal available on Base.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({{
            "X-Oracle-License": LICENSE_KEY,
            "X-Oracle-Tier":    "prophet",
            "User-Agent":       "OracleProphet/1.0",
        }})

    def prophesy(self, token_address: str) -> dict:
        """
        Full combined prophecy — all three signal layers.
        Returns the most complete trust assessment available.
        """
        try:
            resp = self.session.get(
                f"{{ORACLE_URL}}/combined-prophecy",
                params={{"token": token_address}},
                timeout=90,
            )
            resp.raise_for_status()
            data = resp.json()

            verdict_raw   = data.get("verdict", "UNKNOWN")
            verdict_clean = verdict_raw.split(" ")[0] if " " in verdict_raw else verdict_raw

            return {{
                "token":       token_address,
                "verdict":     verdict_clean,
                "final_score": data.get("final_score", 0),
                "confidence":  data.get("confidence", "LOW"),
                "veto_reason": data.get("veto_reason"),
                "is_rug":      verdict_clean == "CURSED",
                "breakdown": {{
                    "token":    data.get("breakdown", {{}}).get("token", {{}}),
                    "deployer": data.get("breakdown", {{}}).get("deployer", {{}}),
                    "promoter": data.get("breakdown", {{}}).get("promoter", {{}}),
                }},
                "license":     "prophet",
                "raw":         data,
            }}
        except Exception as e:
            return {{"error": str(e), "token": token_address}}

    def oracle_reputation(self) -> dict:
        """Check Oracle\'s historical accuracy — verify you\'re buying good signals."""
        try:
            resp = self.session.get(f"{{ORACLE_URL}}/reputation", timeout=10)
            return resp.json()
        except Exception as e:
            return {{"error": str(e)}}

    def latest_predictions(self, verdict: str = "CURSED", limit: int = 10) -> list:
        """Get Oracle\'s latest predictions — see what\'s being flagged right now."""
        try:
            resp = self.session.get(
                f"{{ORACLE_URL}}/predictions",
                params={{"verdict": verdict, "limit": limit}},
                timeout=10,
            )
            return resp.json().get("predictions", [])
        except Exception as e:
            return []

    def trust_gate(self, token_address: str, min_score: int = 70) -> dict:
        """
        Hard gate — returns go/no-go with full reasoning.
        Use this before any autonomous trade or interaction.
        """
        result = self.prophesy(token_address)
        score  = result.get("final_score", 0)
        go     = (
            score >= min_score and
            result.get("verdict") != "CURSED" and
            result.get("veto_reason") is None
        )
        return {{
            "go":          go,
            "reason":      result.get("veto_reason") or result.get("verdict"),
            "score":       score,
            "min_score":   min_score,
            "confidence":  result.get("confidence"),
            "full_result": result,
        }}


# Usage:
# from oracle_prophet import OracleProphet
# oracle = OracleProphet()
#
# gate = oracle.trust_gate("0xTOKEN_ADDRESS", min_score=70)
# if gate["go"]:
#     print(f"SAFE TO PROCEED — score {{gate[\'score\']}}")
# else:
#     print(f"BLOCKED — {{gate[\'reason\']}}")
#
# reputation = oracle.oracle_reputation()
# print(f"Oracle trust score: {{reputation[\'trust_score\']}}")
#
# latest_cursed = oracle.latest_predictions(verdict="CURSED")
# print(f"Latest rugs flagged: {{len(latest_cursed)}}")
'''


# ── License key generation ────────────────────────────────────────────────────

def generate_license_key(tier: str, buyer_wallet: str) -> str:
    """Generate a deterministic license key for a buyer + tier combo."""
    raw = f"{AGENT_ID}:{tier}:{buyer_wallet}:{os.getenv('AGENT_PRIVATE_KEY', 'secret')}"
    return "OB-" + tier[:3].upper() + "-" + hashlib.sha256(raw.encode()).hexdigest()[:24].upper()


def verify_license_key(key: str, tier: str, buyer_wallet: str) -> bool:
    """Verify a license key is valid."""
    expected = generate_license_key(tier, buyer_wallet)
    return key == expected


# ── Flask routes ──────────────────────────────────────────────────────────────

@skill_bp.route('/skills', methods=['GET'])
def list_skills():
    """List all available skill tiers and their prices."""
    return jsonify({
        "oracle":  "Oracle of Base",
        "version": "1.0",
        "skills":  SKILLS,
        "payment": {
            "scheme":  "x402",
            "network": "eip155:8453",
            "token":   "USDC",
            "wallet":  WALLET_ADDRESS,
        },
        "how_to_buy": f"GET {ORACLE_URL}/skills/{{tier}}/buy?wallet=YOUR_WALLET",
    })


@skill_bp.route('/skills/<tier>', methods=['GET'])
def get_skill(tier: str):
    """Get details about a specific skill tier."""
    skill = SKILLS.get(tier.lower())
    if not skill:
        return jsonify({"error": f"Unknown tier: {tier}. Choose: {list(SKILLS.keys())}"}), 404
    return jsonify({
        **skill,
        "buy_endpoint": f"{ORACLE_URL}/skills/{tier}/buy",
    })


@skill_bp.route('/skills/<tier>/buy', methods=['GET', 'POST'])
def buy_skill(tier: str):
    """
    Purchase a skill tier. Payment handled by x402 middleware.
    After payment, returns the skill code and license key.

    Query params:
      wallet: your agent's Base wallet address (for license binding)
    """
    skill = SKILLS.get(tier.lower())
    if not skill:
        return jsonify({"error": f"Unknown tier: {tier}"}), 404

    buyer_wallet = (
        request.args.get('wallet') or
        request.json.get('wallet') if request.is_json else None or
        request.headers.get('X-Buyer-Wallet', 'anonymous')
    )

    license_key = generate_license_key(tier, buyer_wallet)

    # Generate the appropriate skill code
    code_generators = {
        "apprentice": generate_apprentice_code,
        "seer":       generate_seer_code,
        "prophet":    generate_prophet_code,
    }
    skill_code = code_generators[tier.lower()](license_key)

    filename = f"oracle_{tier.lower()}.py"

    return jsonify({
        "skill":       skill["name"],
        "tier":        tier,
        "level":       skill["level"],
        "license_key": license_key,
        "buyer_wallet": buyer_wallet,
        "issued_at":   int(time.time()),
        "oracle_url":  ORACLE_URL,
        "instructions": [
            f"1. Save the code below as `{filename}` in your agent workspace",
            "2. pip install requests",
            f"3. from oracle_{tier.lower()} import Oracle{tier.capitalize()}",
            f"4. oracle = Oracle{tier.capitalize()}()",
            "5. prophecy = oracle.prophesy('0xTOKEN_ADDRESS')",
        ],
        "filename":    filename,
        "code":        skill_code,
        "capabilities": skill["capabilities"],
    })


@skill_bp.route('/skills/<tier>/verify', methods=['GET'])
def verify_skill(tier: str):
    """Verify a license key is valid for a given tier."""
    key    = request.args.get('key', '')
    wallet = request.args.get('wallet', '')

    if not key or not wallet:
        return jsonify({"error": "Missing key or wallet"}), 400

    valid = verify_license_key(key, tier.lower(), wallet)
    return jsonify({
        "valid":  valid,
        "tier":   tier,
        "wallet": wallet,
    })
