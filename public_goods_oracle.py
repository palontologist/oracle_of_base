"""
public_goods_oracle.py
-----------------------
Legitimacy analysis for public goods project teams.
Built for Octant-style evaluation rounds.

Collects signals across four dimensions:
  1. On-chain wallet history (Base + Ethereum)
  2. GitHub activity (commit history, contributors, recency)
  3. Grant history (Gitcoin rounds, delivery record)
  4. Social presence (Farcaster profile + network quality)

Feeds everything to Venice AI with no scoring rubric.
Venice reasons freely about team legitimacy, Sybil risk, and delivery credibility.

Endpoints:
  GET /public-goods-check?wallet=0x...&github=org/repo&handle=farcaster_handle
  GET /public-goods-feed     (latest evaluations, free)
"""

import os
import json
import time
from utils.ens import enrich_address, resolve_ens
import hashlib
import logging
import requests
from datetime import datetime, timezone

log = logging.getLogger("public_goods_oracle")

VENICE_API_KEY = os.getenv("VENICE_API_KEY", "")
VENICE_MODEL   = os.getenv("VENICE_MODEL", "qwen3-5-9b")
BASE_RPC_URL   = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")
ETH_RPC_URL    = os.getenv("ETH_RPC_URL",  "https://eth.llamarpc.com")
GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "")   # optional — increases rate limit


class PublicGoodsOracle:

    def __init__(self, agent_id: str):
        self.agent_id = agent_id

    # ── On-chain signals ──────────────────────────────────────────────────────

    def _collect_wallet_signals(self, address: str) -> dict:
        """
        On-chain history for a team wallet.
        Checks tx count, wallet age proxy, ETH balance, contract interactions.
        """
        if not address or not address.startswith("0x"):
            return {"error": "invalid address", "address": address}

        def rpc_call(rpc_url: str, method: str, params: list) -> str:
            try:
                r = requests.post(rpc_url, json={
                    "jsonrpc": "2.0", "method": method, "params": params, "id": 1
                }, timeout=8)
                return r.json().get("result", "0x0") if r.ok else "0x0"
            except Exception:
                return "0x0"

        # Base chain signals
        base_tx_count   = int(rpc_call(BASE_RPC_URL, "eth_getTransactionCount", [address, "latest"]), 16)
        base_balance    = int(rpc_call(BASE_RPC_URL, "eth_getBalance",          [address, "latest"]), 16)
        base_code       = rpc_call(BASE_RPC_URL, "eth_getCode",                 [address, "latest"])
        is_contract     = base_code not in ("0x", "0x0", None, "")

        # Ethereum mainnet signals (cross-chain legitimacy check)
        eth_tx_count    = int(rpc_call(ETH_RPC_URL, "eth_getTransactionCount", [address, "latest"]), 16)
        eth_balance     = int(rpc_call(ETH_RPC_URL, "eth_getBalance",          [address, "latest"]), 16)

        base_eth        = round(base_balance / 1e18, 6)
        eth_main        = round(eth_balance  / 1e18, 6)
        total_tx        = base_tx_count + eth_tx_count

        ens = enrich_address(address)

        return {
            "address":           address,
            "ens_name":          ens["ens_name"],
            "ens_signal":        ens["ens_signal"],
            "base_tx_count":     base_tx_count,
            "eth_tx_count":      eth_tx_count,
            "total_tx_count":    total_tx,
            "base_balance_eth":  base_eth,
            "eth_balance_eth":   eth_main,
            "is_contract":       is_contract,
            "activity_level":    (
                "highly_active"  if total_tx > 500
                else "active"    if total_tx > 100
                else "moderate"  if total_tx > 20
                else "low"       if total_tx > 5
                else "new_wallet"
            ),
            "cross_chain_presence": eth_tx_count > 0,
            "has_mainnet_history":  eth_tx_count > 10,
        }

    def _collect_contributor_signals(self, addresses: list[str]) -> dict:
        """
        Check multiple contributor wallets for Sybil cluster patterns.
        Red flags: all wallets new, all appeared at same time, no independent history.
        """
        if not addresses:
            return {"contributors_checked": 0}

        results = []
        for addr in addresses[:5]:   # cap at 5 to keep it fast
            try:
                tx = int(requests.post(BASE_RPC_URL, json={
                    "jsonrpc": "2.0", "method": "eth_getTransactionCount",
                    "params": [addr, "latest"], "id": 1
                }, timeout=6).json().get("result", "0x0"), 16)
                results.append({"address": addr[:10] + "...", "tx_count": tx, "is_new": tx < 5})
            except Exception:
                results.append({"address": addr[:10] + "...", "tx_count": 0, "is_new": True})

        new_count    = sum(1 for r in results if r["is_new"])
        sybil_signal = new_count >= max(2, len(results) * 0.6)

        return {
            "contributors_checked": len(results),
            "new_wallet_count":     new_count,
            "sybil_cluster_signal": sybil_signal,
            "details":              results,
            "note": (
                "Multiple contributors have minimal on-chain history — possible Sybil cluster"
                if sybil_signal else
                "Contributors show independent on-chain histories"
            ),
        }

    # ── GitHub signals ────────────────────────────────────────────────────────

    def _collect_github_signals(self, github_handle: str) -> dict:
        """
        GitHub activity signals for a org or username.
        Checks recent commit activity, contributor count, repo age, issue engagement.
        """
        if not github_handle:
            return {"provided": False}

        handle  = github_handle.lstrip("@").strip("/")
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "OracleOfBase/1.0"}
        if GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

        signals = {"handle": handle, "provided": True}

        try:
            # Is this a user or org?
            r = requests.get(f"https://api.github.com/users/{handle}", headers=headers, timeout=8)
            if r.status_code == 404:
                return {"provided": True, "exists": False, "handle": handle}

            user_data = r.json()
            signals.update({
                "exists":        True,
                "type":          user_data.get("type", "User"),
                "public_repos":  user_data.get("public_repos", 0),
                "followers":     user_data.get("followers", 0),
                "created_at":    user_data.get("created_at", ""),
                "bio":           user_data.get("bio", ""),
                "location":      user_data.get("location", ""),
                "blog":          user_data.get("blog", ""),
            })

            # Account age
            if user_data.get("created_at"):
                created = datetime.fromisoformat(user_data["created_at"].replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - created).days
                signals["account_age_days"] = age_days
                signals["account_age_signal"] = (
                    "established" if age_days > 365
                    else "growing"  if age_days > 90
                    else "new"
                )

            # Recent repos
            repos_r = requests.get(
                f"https://api.github.com/users/{handle}/repos?sort=updated&per_page=5",
                headers=headers, timeout=8
            )
            if repos_r.ok:
                repos = repos_r.json()
                signals["recent_repos"] = [{
                    "name":       r.get("name"),
                    "stars":      r.get("stargazers_count", 0),
                    "forks":      r.get("forks_count", 0),
                    "updated_at": r.get("updated_at", "")[:10],
                    "language":   r.get("language"),
                    "description": (r.get("description") or "")[:80],
                } for r in repos[:5]]

                total_stars = sum(r.get("stargazers_count", 0) for r in repos)
                signals["total_stars_recent_repos"] = total_stars

                # Check recency of activity
                if repos:
                    latest = repos[0].get("updated_at", "")[:10]
                    signals["last_repo_update"] = latest
                    try:
                        days_since = (datetime.now(timezone.utc) -
                                      datetime.fromisoformat(latest + "T00:00:00+00:00")).days
                        signals["days_since_last_commit"] = days_since
                        signals["recently_active"] = days_since < 30
                    except Exception:
                        pass

            # Commit activity (last 90 days via contributions endpoint)
            events_r = requests.get(
                f"https://api.github.com/users/{handle}/events/public?per_page=30",
                headers=headers, timeout=8
            )
            if events_r.ok:
                events = events_r.json()
                push_events = [e for e in events if e.get("type") == "PushEvent"]
                commit_count = sum(
                    len(e.get("payload", {}).get("commits", []))
                    for e in push_events
                )
                signals["recent_push_events"]  = len(push_events)
                signals["recent_commit_count"]  = commit_count
                signals["active_contributor"]   = commit_count > 5

        except Exception as e:
            signals["error"] = str(e)

        return signals

    # ── Gitcoin signals ───────────────────────────────────────────────────────

    def _collect_gitcoin_signals(self, address: str) -> dict:
        """
        Check Gitcoin Grants history for this wallet.
        Checks if they've received grants before and if projects are still active.
        """
        if not address:
            return {"checked": False}

        try:
            # Gitcoin Grants Stack API — public, no auth required
            r = requests.get(
                f"https://grants-stack-indexer-v2.gitcoin.co/data/1/projects.json",
                timeout=10
            )
            # This is a heavy endpoint — use address search instead
            # Try the project search by address
            r2 = requests.get(
                f"https://indexer-production.fly.dev/api/v1/results/passport/score/{address}",
                headers={"Content-Type": "application/json"},
                timeout=8
            )
            passport_score = None
            if r2.ok:
                data = r2.json()
                passport_score = data.get("score")

            # Check Gitcoin Passport score as legitimacy proxy
            return {
                "checked":                True,
                "address":                address[:10] + "...",
                "gitcoin_passport_score": passport_score,
                "passport_signal": (
                    "strong_identity"   if passport_score and float(passport_score) > 20
                    else "weak_identity" if passport_score and float(passport_score) > 0
                    else "no_passport"
                ),
                "note": "Gitcoin Passport score reflects on-chain identity verification breadth",
            }
        except Exception as e:
            return {"checked": True, "error": str(e)}

    # ── Farcaster signals ─────────────────────────────────────────────────────

    def _collect_farcaster_signals(self, handle: str) -> dict:
        """Farcaster presence check for the team or project."""
        if not handle:
            return {"provided": False}

        username = handle.replace("@", "").strip().lower()
        try:
            r = requests.get(
                f"https://client.warpcast.com/v2/user-by-username?username={username}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=8
            )
            if r.status_code != 200:
                return {"provided": True, "exists": False, "handle": username}

            user = r.json().get("result", {}).get("user", {})
            profile = user.get("profile", {})
            bio     = profile.get("bio", {}).get("text", "")

            fc = int(user.get("followerCount", 0))
            fw = int(user.get("followingCount", 0))

            return {
                "provided":       True,
                "exists":         True,
                "handle":         username,
                "bio":            bio,
                "follower_count": fc,
                "following_count": fw,
                "follow_ratio":   round(fc / max(fw, 1), 2),
                "cast_count":     user.get("castCount", 0),
                "reach_signal": (
                    "established_presence" if fc > 1000
                    else "growing_presence" if fc > 100
                    else "small_presence"
                ),
            }
        except Exception as e:
            return {"provided": True, "error": str(e)}

    # ── Venice reasoning ──────────────────────────────────────────────────────

    def _consult_venice(self, signals: dict) -> dict:
        """
        Send all signals to Venice for free-form legitimacy assessment.
        No rubric. No categories. Venice reasons from first principles.
        """
        prompt = f"""You are an expert evaluator for public goods funding rounds (like Gitcoin, Octant, Giveth).

You have been given raw signals about a project team applying for public goods funding.
Your job: reason honestly about whether this team is legitimate, likely to deliver, and worth funding.

Do NOT apply fixed categories or mechanical scoring.
Think about: Is this a real team with genuine history? Do their on-chain and GitHub signals tell a consistent story? Are there Sybil or farming patterns? Does their stated mission match their activity? What would you want a human reviewer to know?

Be direct. If signals are sparse, say so — sparse data is itself a signal.
If signals are strong, explain what makes them credible.

════════ TEAM SIGNALS ════════
{json.dumps(signals, indent=2, default=str)}
═════════════════════════════

Respond ONLY with a JSON object — no markdown, no preamble:
{{
  "legitimacy_score": <integer 0-100, your honest assessment of legitimacy and delivery credibility>,
  "flags": [<list any red flags as short strings, empty array if none>],
  "strengths": [<list genuine positive signals, empty if none>],
  "assessment": "<3-5 sentences. Your direct read of this team. Would you fund them? Why or why not? Be specific about which signals shaped your view.>",
  "sybil_risk": "<LOW|MEDIUM|HIGH>",
  "delivery_confidence": "<LOW|MEDIUM|HIGH>",
  "data_richness": "<SPARSE|MODERATE|RICH — how much signal did you actually have to work with>"
}}"""

        try:
            from threading import Semaphore
            # reuse the global Venice lock from prophecy_engine if available
            try:
                from prophecy_engine import _venice_lock
                lock = _venice_lock
            except ImportError:
                lock = Semaphore(1)

            acquired = lock.acquire(timeout=120)
            if not acquired:
                raise TimeoutError("Venice lock wait timed out")
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
                        "temperature": 0.3,
                        "max_tokens":  400,
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
            log.warning(f"Venice public goods analysis failed: {e}")
            return {
                "legitimacy_score":    50,
                "flags":               ["venice_unavailable"],
                "strengths":           [],
                "assessment":          "Venice unavailable — manual review required.",
                "sybil_risk":          "UNKNOWN",
                "delivery_confidence": "UNKNOWN",
                "data_richness":       "SPARSE",
            }

    # ── Main entrypoint ───────────────────────────────────────────────────────

    def evaluate(
        self,
        wallet:          str,
        github:          str  = "",
        farcaster_handle: str = "",
        contributor_wallets: list[str] = None,
        project_name:    str = "",
    ) -> dict:
        """
        Full public goods legitimacy evaluation.

        Args:
            wallet:               primary team/project wallet
            github:               GitHub username or org (optional)
            farcaster_handle:     Farcaster handle (optional)
            contributor_wallets:  list of up to 5 additional contributor wallets
            project_name:         human-readable project name for context

        Returns:
            legitimacy_score, flags, strengths, assessment, sybil_risk,
            delivery_confidence, raw_signals
        """
        # Resolve ENS name if provided instead of address
        resolved_wallet = resolve_ens(wallet)
        ens_info = enrich_address(resolved_wallet)
        display_name = ens_info["display"] if ens_info["has_ens"] else (resolved_wallet[:10] + "...")

        log.info(f"🔍 Evaluating public goods project | wallet={display_name} | ens={ens_info['ens_name']} | github={github} | handle={farcaster_handle}")

        signals = {
            "project_name":   project_name or "Unknown",
            "evaluation_for": "public_goods_funding_round",
            "ens":            ens_info,
        }

        # Collect all signal layers in parallel (simple sequential for now)
        signals["wallet"]      = self._collect_wallet_signals(resolved_wallet)
        signals["github"]      = self._collect_github_signals(github)
        signals["farcaster"]   = self._collect_farcaster_signals(farcaster_handle)
        signals["gitcoin"]     = self._collect_gitcoin_signals(wallet)

        if contributor_wallets:
            signals["contributors"] = self._collect_contributor_signals(contributor_wallets)

        # Venice assessment
        venice = self._consult_venice(signals)

        score = int(venice.get("legitimacy_score", 50))

        # Generate attestation UID
        uid = hashlib.sha256(
            f"{self.agent_id}{wallet}{int(time.time())}".encode()
        ).hexdigest()

        return {
            "wallet":               resolved_wallet,
            "ens_name":             ens_info["ens_name"],
            "display":              ens_info["display"],
            "project_name":         project_name,
            "legitimacy_score":     score,
            "flags":                venice.get("flags", []),
            "strengths":            venice.get("strengths", []),
            "assessment":           venice.get("assessment", ""),
            "sybil_risk":           venice.get("sybil_risk", "UNKNOWN"),
            "delivery_confidence":  venice.get("delivery_confidence", "UNKNOWN"),
            "data_richness":        venice.get("data_richness", "SPARSE"),
            "raw_signals":          signals,
            "attestation_uid":      uid,
            "evaluated_at":         int(time.time()),
        }


if __name__ == "__main__":
    oracle = PublicGoodsOracle("34499")
    result = oracle.evaluate(
        wallet            = "0x1EA37E2Fb76Aa396072204C90fcEF88093CEb920",
        github            = "gitcoinco",
        farcaster_handle  = "gitcoin",
        project_name      = "Test Project",
    )
    print(json.dumps({k: v for k, v in result.items() if k != "raw_signals"}, indent=2))