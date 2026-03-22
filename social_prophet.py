"""
social_prophet.py
------------------
Social identity analysis for Oracle of Base.

Philosophy: collect raw signals, send everything to Venice, let it reason.
No predetermined categories. No keyword bias. No hard score cutoffs.
Venice sees the full picture and returns a contextual read.
"""

import requests
import json
import time
import hashlib
import os
import logging

log = logging.getLogger("social_prophet")

VENICE_API_KEY = os.getenv("VENICE_API_KEY", "")
VENICE_MODEL   = os.getenv("VENICE_MODEL", "llama-3.3-70b")
BASE_RPC_URL   = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")


class SocialProphet:
    def __init__(self, agent_id, private_key):
        self.agent_id    = agent_id
        self.private_key = private_key

    # ── Data collection ───────────────────────────────────────────────────────

    def _fetch_farcaster(self, username: str) -> dict | None:
        """Fetch full Farcaster profile from Warpcast API."""
        try:
            url  = f"https://client.warpcast.com/v2/user-by-username?username={username}"
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if resp.status_code == 200:
                return resp.json().get("result", {}).get("user")
        except Exception as e:
            log.warning(f"Farcaster fetch failed for {username}: {e}")
        return None

    def _fetch_recent_casts(self, fid: int, limit: int = 10) -> list:
        """Fetch recent casts to assess content quality and tone."""
        try:
            url  = f"https://client.warpcast.com/v2/casts?fid={fid}&limit={limit}"
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if resp.status_code == 200:
                casts = resp.json().get("result", {}).get("casts", [])
                return [c.get("text", "") for c in casts if c.get("text")]
        except Exception as e:
            log.debug(f"Casts fetch failed: {e}")
        return []

    def _fetch_wallet(self, address: str) -> dict:
        """Collect on-chain signals for a verified wallet."""
        try:
            def rpc(method, params):
                r = requests.post(BASE_RPC_URL, json={
                    "jsonrpc": "2.0", "method": method, "params": params, "id": 1
                }, timeout=8)
                return r.json().get("result", "0x0") if r.ok else "0x0"

            tx_count    = int(rpc("eth_getTransactionCount", [address, "latest"]), 16)
            balance_wei = int(rpc("eth_getBalance",          [address, "latest"]), 16)
            balance_eth = round(balance_wei / 1e18, 6)
            code        = rpc("eth_getCode",                 [address, "latest"])
            is_contract = code not in ("0x", "0x0", None)

            return {
                "address":     address,
                "tx_count":    tx_count,
                "balance_eth": balance_eth,
                "is_contract": is_contract,
                "age_signal":  "active" if tx_count > 100 else "moderate" if tx_count > 10 else "new",
            }
        except Exception as e:
            return {"address": address, "error": str(e)}

    # ── Venice reasoning ──────────────────────────────────────────────────────

    def _consult_venice(self, signals: dict) -> dict:
        """
        Pass all raw signals to Venice and ask for a free-form contextual read.
        No categories, no scoring rubric — Venice reasons from first principles.
        """
        prompt = f"""You are a perceptive analyst reading an identity's digital footprint.

You have been given raw signals from a Farcaster profile and optionally their on-chain wallet.
Your job: reason about who or what this entity is, what they do, and how they present themselves.

Do not apply labels or categories. Do not score them on a fixed scale.
Think about: What is their focus? Are they building something? Are they an AI agent, a human developer, a community member, a researcher?
What does their activity pattern suggest? What's interesting or notable about them?
Be honest — if signals are sparse, say so. If they're rich, draw from them.

════════ SIGNALS ════════
{json.dumps(signals, indent=2, default=str)}
═════════════════════════

Respond ONLY with a JSON object — no markdown, no preamble:
{{
  "score": <integer 0-100, your overall confidence this is an interesting/trustworthy/authentic entity>,
  "nature": "<2-5 word characterisation, e.g. 'active defi builder', 'protocol researcher', 'autonomous trading agent', 'community connector', 'early adopter'  — your own words, not a category>",
  "read": "<2-4 sentences. Your honest read of this entity based on the signals. Personalised, direct, no fluff.>",
  "signals_used": ["<list the 2-4 signals that most shaped your read>"],
  "confidence": "<LOW|MEDIUM|HIGH depending on data richness>"
}}"""

        try:
            resp = requests.post(
                "https://api.venice.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {VENICE_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":       VENICE_MODEL,
                    "messages":    [{"role": "user", "content": prompt}],
                    "temperature": float(os.getenv("VENICE_TEMPERATURE", "0.4")),
                    "max_tokens":  300,
                },
                timeout=int(os.getenv("VENICE_TIMEOUT", "60")),
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()

            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]

            return json.loads(raw)

        except Exception as e:
            log.warning(f"Venice social analysis failed: {e}")
            return {
                "score":       50,
                "nature":      "unknown entity",
                "read":        "Insufficient signals or Venice unavailable — no confident read possible.",
                "signals_used": [],
                "confidence":  "LOW",
            }

    # ── Main entrypoint ───────────────────────────────────────────────────────

    def consult_the_spirits(self, handle: str) -> dict:
        """
        Full social analysis. Returns a Venice-reasoned contextual profile.
        No fixed categories — output shaped by the actual signals.
        """
        username = handle.replace("@", "").strip().lower()
        log.info(f"👻 Consulting spirits for @{username}")

        fc_user = self._fetch_farcaster(username)
        signals = {"handle": username, "platform": "Farcaster"}

        if fc_user:
            profile = fc_user.get("profile", {})
            bio     = profile.get("bio", {}).get("text", "")
            fid     = fc_user.get("fid")

            signals.update({
                "exists":          True,
                "display_name":    fc_user.get("displayName", username),
                "bio":             bio,
                "follower_count":  fc_user.get("followerCount", 0),
                "following_count": fc_user.get("followingCount", 0),
                "cast_count":      fc_user.get("castCount", 0),
                "fid":             fid,
                "registered_at":   fc_user.get("registeredAt"),
                "power_badge":     fc_user.get("extras", {}).get("farcasterScore", {}).get("score") if fc_user.get("extras") else None,
            })

            # Follower/following ratio context
            fc = signals["follower_count"]
            fw = signals["following_count"]
            signals["follow_ratio"] = round(fc / max(fw, 1), 2)
            signals["reach_signal"] = (
                "high_reach" if fc > 10000
                else "growing" if fc > 1000
                else "small_community" if fc > 100
                else "early_or_niche"
            )

            # Recent cast content sample
            if fid:
                recent = self._fetch_recent_casts(fid, limit=8)
                if recent:
                    signals["recent_cast_sample"] = recent[:5]
                    signals["avg_cast_length"] = round(
                        sum(len(c) for c in recent) / len(recent)
                    )

            # Verified wallets
            verified = fc_user.get("verifiedAddresses", {})
            eth_addrs = verified.get("ethAddresses", []) if verified else []
            if eth_addrs:
                signals["wallet"] = self._fetch_wallet(eth_addrs[0])
                signals["verified_wallets_count"] = len(eth_addrs)

        else:
            signals.update({
                "exists": False,
                "note":   "Handle not found on Farcaster — no profile data available",
            })

        # Let Venice reason over everything
        venice_read = self._consult_venice(signals)

        # Score is 0-100 from Venice, scale to 0-10000 for attestation compat
        score_100  = int(venice_read.get("score", 50))
        score_full = score_100 * 100

        return {
            "score":       score_full,
            "score_100":   score_100,
            "nature":      venice_read.get("nature", "unknown"),
            "read":        venice_read.get("read", ""),
            "signals_used": venice_read.get("signals_used", []),
            "confidence":  venice_read.get("confidence", "LOW"),
            "handle":      username,
            "raw_signals": signals,
        }

    def generate_attestation(self, handle: str, result: dict) -> dict:
        return {
            "agentId":       self.agent_id,
            "target":        handle,
            "value":         result["score"],
            "valueDecimals": 2,
            "tag1":          "social-prophecy",
            "tag2":          "identity-read",
            "timestamp":     int(time.time()),
            "uid":           hashlib.sha256(
                f"{self.agent_id}{handle}{int(time.time())}".encode()
            ).hexdigest(),
        }


if __name__ == "__main__":
    oracle = SocialProphet("34499", "")
    for h in ["vitalik.eth", "dwr", "clanker", "bountycaster"]:
        print(f"\n--- @{h} ---")
        r = oracle.consult_the_spirits(h)
        print(f"Nature:     {r['nature']}")
        print(f"Score:      {r['score_100']}/100")
        print(f"Confidence: {r['confidence']}")
        print(f"Read:       {r['read']}")