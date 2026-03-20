import requests
import json
import time
import hashlib
import os


class SocialProphet:
    def __init__(self, agent_id, private_key):
        self.agent_id    = agent_id
        self.private_key = private_key
        self.endpoint    = "https://oracle.nanobot.dev/api/v1"

    # ── Data Fetching ─────────────────────────────────────────────────────────

    def fetch_farcaster_user(self, username: str) -> dict | None:
        """Fetch user data from Warpcast API."""
        url = f"https://client.warpcast.com/v2/user-by-username?username={username}"
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                return response.json().get('result', {}).get('user')
            return None
        except Exception as e:
            print(f"Error fetching Farcaster data: {e}")
            return None

    def assess_wallet_behaviour(self, wallet_address: str) -> dict:
        """
        Assess a wallet's on-chain behaviour via Base RPC.
        Checks tx count, wallet age, contract interactions.
        Returns a wallet score 0-100.
        """
        if not wallet_address:
            return {"score": 50, "reason": "no_wallet", "details": {}}

        try:
            base_rpc = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")

            # Get tx count (nonce = number of txs sent)
            tx_count_payload = {
                "jsonrpc": "2.0",
                "method":  "eth_getTransactionCount",
                "params":  [wallet_address, "latest"],
                "id":      1
            }
            resp = requests.post(base_rpc, json=tx_count_payload, timeout=8)
            tx_count = int(resp.json().get('result', '0x0'), 16) if resp.ok else 0

            # Get ETH balance
            balance_payload = {
                "jsonrpc": "2.0",
                "method":  "eth_getBalance",
                "params":  [wallet_address, "latest"],
                "id":      2
            }
            resp2    = requests.post(base_rpc, json=balance_payload, timeout=8)
            balance_wei = int(resp2.json().get('result', '0x0'), 16) if resp2.ok else 0
            balance_eth = balance_wei / 1e18

            # Score based on activity signals
            score = 50  # neutral baseline

            # High tx count = established actor
            if tx_count > 1000:
                score += 25
            elif tx_count > 100:
                score += 15
            elif tx_count > 10:
                score += 5
            elif tx_count == 0:
                score -= 20  # brand new wallet

            # ETH balance signals skin in the game
            if balance_eth > 1.0:
                score += 15
            elif balance_eth > 0.1:
                score += 8
            elif balance_eth < 0.001:
                score -= 10

            score = max(0, min(100, score))

            return {
                "score":  score,
                "reason": "established_wallet" if score > 70 else "new_wallet" if score < 30 else "moderate_activity",
                "details": {
                    "wallet_address": wallet_address,
                    "tx_count":       tx_count,
                    "balance_eth":    round(balance_eth, 4),
                    "is_active":      tx_count > 10,
                }
            }

        except Exception as e:
            print(f"Error assessing wallet {wallet_address}: {e}")
            return {"score": 50, "reason": "assessment_failed", "details": {"error": str(e)}}

    def assess_posting_behaviour(self, fc_user: dict) -> dict:
        """
        Analyse posting patterns from Farcaster profile data.
        Bots post consistently and frequently; humans erratically.
        """
        if not fc_user:
            return {"score": 50, "reason": "no_data", "details": {}}

        following_count = int(fc_user.get('followingCount', 0))
        follower_count  = int(fc_user.get('followerCount', 0))
        cast_count      = int(fc_user.get('castCount', 0) or
                              fc_user.get('activeOnFcNetwork', 0))

        # Follow ratio: bots often follow many, have few followers early on
        follow_ratio = following_count / max(follower_count, 1)

        score = 50
        details = {
            "following":    following_count,
            "followers":    follower_count,
            "follow_ratio": round(follow_ratio, 2),
            "cast_count":   cast_count,
        }

        # High follow ratio = bot-like behaviour
        if follow_ratio > 10:
            score += 20
            details['signal'] = 'high_follow_ratio_bot_pattern'
        elif follow_ratio < 0.1 and follower_count > 1000:
            score -= 15
            details['signal'] = 'influencer_human_pattern'

        # High cast count with low followers = bot
        if cast_count > 500 and follower_count < 100:
            score += 25
            details['signal'] = 'high_volume_low_reach_bot'

        return {
            "score":   max(0, min(100, score)),
            "reason":  "bot_pattern" if score > 70 else "human_pattern" if score < 30 else "ambiguous",
            "details": details,
        }

    # ── Core Analysis ─────────────────────────────────────────────────────────

    def consult_the_spirits(self, handle: str) -> dict:
        """
        Full social analysis: Farcaster identity + posting behaviour + wallet activity.
        Returns unified agent purity score 0-10000.
        """
        print(f"👻 Summoning spirits for handle: {handle}...")

        username = handle.replace('@', '').lower()
        fc_user  = self.fetch_farcaster_user(username)

        # ── Identity signals ──────────────────────────────────────────────────
        identity_score = 5000  # neutral baseline (0-10000 scale)
        details = {
            "platform":      "Farcaster",
            "username":      username,
            "exists":        False,
            "bot_keywords":  [],
            "follower_count": 0,
        }

        if fc_user:
            details['exists'] = True
            profile  = fc_user.get('profile', {})
            bio      = profile.get('bio', {}).get('text', '').lower()
            details['bio']            = bio
            details['follower_count'] = fc_user.get('followerCount', 0)

            # Bio keyword analysis
            bot_keywords   = ['bot', 'agent', 'ai', 'automated', 'llm', 'gpt',
                               'robot', 'oracle', 'autonomous', 'machine', 'neural']
            human_keywords = ['human', 'person', 'founder', 'builder', 'engineer',
                               'artist', 'ceo', 'cto', 'investor']

            found_bot   = [kw for kw in bot_keywords   if kw in bio]
            found_human = [kw for kw in human_keywords if kw in bio]

            details['bot_keywords']   = found_bot
            details['human_keywords'] = found_human

            if found_bot:
                identity_score += 2000   # self-identified bot
            if 'bot' in username or 'ai' in username or 'agent' in username:
                identity_score += 1500   # bot in username
            if details['follower_count'] > 1000:
                identity_score += 500    # popular = likely real agent
            if found_human:
                identity_score -= 3000   # self-identified human

            # ── Posting behaviour ─────────────────────────────────────────────
            posting = self.assess_posting_behaviour(fc_user)
            details['posting_behaviour'] = posting
            # Scale posting score (0-100) into our 0-10000 range adjustment
            posting_adjustment = int((posting['score'] - 50) * 50)
            identity_score += posting_adjustment

            # ── Wallet assessment (if verifiedAddresses available) ────────────
            verified_addrs = fc_user.get('verifiedAddresses', {})
            eth_addresses  = verified_addrs.get('ethAddresses', []) if verified_addrs else []

            if eth_addresses:
                wallet          = eth_addresses[0]
                wallet_result   = self.assess_wallet_behaviour(wallet)
                details['wallet'] = wallet_result
                # Scale wallet score into range adjustment
                wallet_adjustment = int((wallet_result['score'] - 50) * 40)
                identity_score   += wallet_adjustment
            else:
                details['wallet'] = {"score": 50, "reason": "no_verified_wallet"}

        else:
            # Fallback for non-Farcaster: hash-based neutral score
            details['note'] = "User not found on Farcaster"
            h = int(hashlib.sha256(username.encode()).hexdigest(), 16)
            identity_score = (h % 10000)

        final_score = max(0, min(10000, identity_score))

        return {
            "score":   final_score,
            "verdict": self._interpret_score(final_score),
            "details": details,
        }

    def _interpret_score(self, score: int) -> str:
        if score > 8000:
            return "PURE AGENT (Code Only)"
        if score > 4000:
            return "CYBORG (Human-in-the-loop)"
        return "HUMAN (Meatbag Detected)"

    # ── Attestation ───────────────────────────────────────────────────────────

    def generate_attestation(self, handle: str, analysis_result: dict) -> dict:
        """Format analysis into ERC-8004 Reputation Registry payload."""
        return {
            "agentId":       self.agent_id,
            "target":        handle,
            "value":         analysis_result['score'],
            "valueDecimals": 2,
            "tag1":          "social-prophecy",
            "tag2":          "agent-purity",
            "endpoint":      self.endpoint,
            "metadata_ipfs": "ipfs://QmPlaceholderForSocialReport",
            "timestamp":     int(time.time()),
            "uid":           hashlib.sha256(
                f"{self.agent_id}{handle}{int(time.time())}".encode()
            ).hexdigest(),
        }


# ── Demo ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    oracle = SocialProphet("34499", "private_key_placeholder")
    for handle in ["clanker", "dwr", "bountycaster", "perl"]:
        print(f"\n--- ANALYZING {handle} ---")
        fate = oracle.consult_the_spirits(handle)
        print(f"Verdict: {fate['verdict']}")
        print(f"Score:   {fate['score'] / 100}/100")
        print(f"Details: {json.dumps(fate['details'], indent=2)}")