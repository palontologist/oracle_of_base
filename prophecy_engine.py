import json
import time
import hashlib
import urllib.request
import urllib.error
import os
import requests

# ERC-8004 Registry Addresses (Base Mainnet)
IDENTITY_REGISTRY   = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
REPUTATION_REGISTRY = "0x8004BAa17C55a88189AE136b182e5fdA19dE9b63"

# Base RPC for on-chain deployer checks
BASE_RPC = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")


class FinancialProphet:
    def __init__(self, agent_id, private_key):
        self.agent_id   = agent_id
        self.private_key = private_key
        self.endpoint   = "https://oracle.nanobot.dev/api/v1"

    # ── Data Fetching ─────────────────────────────────────────────────────────

    def fetch_token_data(self, token_address):
        """Fetch token data from DexScreener, return most liquid Base pair."""
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        try:
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
                if not data.get('pairs'):
                    return None
                base_pairs = [p for p in data['pairs'] if p['chainId'] == 'base']
                if not base_pairs:
                    return None
                return sorted(
                    base_pairs,
                    key=lambda x: float(x.get('liquidity', {}).get('usd', 0) or 0),
                    reverse=True
                )[0]
        except Exception as e:
            print(f"Error fetching token data: {e}")
            return None

    def fetch_deployer_wallet(self, token_address: str) -> str | None:
        """
        Get the deployer wallet of a token contract via Base RPC.
        Traces back to the wallet that created the contract.
        """
        try:
            payload = {
                "jsonrpc": "2.0",
                "method":  "eth_getTransactionByHash",
                "params":  [token_address],
                "id":      1
            }
            # Get contract creation tx via DexScreener pair info
            url  = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            req  = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as r:
                data  = json.loads(r.read().decode())
                pairs = [p for p in (data.get('pairs') or []) if p['chainId'] == 'base']
                if pairs:
                    return pairs[0].get('baseToken', {}).get('address')
            return None
        except Exception as e:
            print(f"Error fetching deployer: {e}")
            return None

    def assess_deployer(self, deployer_wallet: str) -> dict:
        """
        Assess the wallet that deployed this token.
        Checks previous token deployments and their outcomes via DexScreener.
        Returns a deployer score 0-100.
        """
        if not deployer_wallet:
            return {"score": 50, "reason": "unknown_deployer", "details": {}}

        try:
            # Search DexScreener for other tokens from this deployer
            url = f"https://api.dexscreener.com/latest/dex/search?q={deployer_wallet}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as r:
                data  = json.loads(r.read().decode())
                pairs = [
                    p for p in (data.get('pairs') or [])
                    if p.get('chainId') == 'base'
                ]

            if not pairs:
                # Brand new deployer — neutral, slight penalty for unknown
                return {
                    "score":  45,
                    "reason": "first_time_deployer",
                    "details": {"previous_tokens": 0}
                }

            total_tokens = len(pairs)
            rugged       = 0
            successful   = 0

            for pair in pairs:
                liquidity = float(pair.get('liquidity', {}).get('usd', 0) or 0)
                volume    = float(pair.get('volume', {}).get('h24', 0) or 0)
                age_hours = (
                    (time.time() * 1000 - pair.get('pairCreatedAt', time.time() * 1000))
                    / (1000 * 3600)
                )

                # Rug pattern: old pair, no liquidity, no volume
                if age_hours > 48 and liquidity < 1000 and volume < 100:
                    rugged += 1
                elif liquidity > 10000 and volume > 1000:
                    successful += 1

            rug_rate = rugged / total_tokens if total_tokens > 0 else 0

            # Hard veto: >50% rug rate = known rugger
            if rug_rate > 0.5:
                score = max(0, int(10 - rug_rate * 10))
            else:
                score = min(100, int(
                    50 +
                    (successful / max(total_tokens, 1)) * 30 -
                    (rug_rate * 50)
                ))

            return {
                "score":  score,
                "reason": "known_rugger" if rug_rate > 0.5 else "clean_history" if score > 70 else "mixed_history",
                "details": {
                    "total_tokens_deployed": total_tokens,
                    "successful":            successful,
                    "rugged":                rugged,
                    "rug_rate_pct":          round(rug_rate * 100, 1),
                    "is_known_rugger":       rug_rate > 0.5,
                }
            }

        except Exception as e:
            print(f"Error assessing deployer {deployer_wallet}: {e}")
            return {"score": 50, "reason": "assessment_failed", "details": {"error": str(e)}}

    def assess_promoters(self, token_address: str, token_symbol: str) -> dict:
        """
        Check who is promoting this token on Farcaster.
        Bot farms promoting = red flag.
        Trusted agents promoting = green flag.
        """
        try:
            # Search Warpcast for token mentions
            search_term = token_symbol.replace('$', '').lower()
            url = f"https://client.warpcast.com/v2/search-casts?q=%24{search_term}&limit=20"
            headers = {'User-Agent': 'Mozilla/5.0'}
            resp = requests.get(url, headers=headers, timeout=8)

            if resp.status_code != 200:
                return {"score": 50, "reason": "no_social_data", "details": {}}

            casts = resp.json().get('result', {}).get('casts', [])

            if not casts:
                return {"score": 60, "reason": "no_promoters_found", "details": {"cast_count": 0}}

            bot_keywords   = ['bot', 'agent', 'ai', 'automated', 'oracle', 'autonomous']
            human_keywords = ['founder', 'builder', 'engineer', 'ceo', 'investor']

            bot_promoters     = 0
            human_promoters   = 0
            trusted_promoters = 0
            total             = len(casts)

            for cast in casts:
                author  = cast.get('author', {})
                bio     = (author.get('profile', {}).get('bio', {}).get('text') or '').lower()
                followers = int(author.get('followerCount', 0))

                is_bot   = any(kw in bio for kw in bot_keywords)
                is_human = any(kw in bio for kw in human_keywords)

                if is_bot:
                    bot_promoters += 1
                elif is_human:
                    human_promoters += 1

                # High follower promoters = trusted signal
                if followers > 5000:
                    trusted_promoters += 1

            bot_ratio = bot_promoters / total if total > 0 else 0

            # Bot farm pattern: >70% bot promoters = coordinated pump
            if bot_ratio > 0.7:
                score  = 15
                reason = "bot_farm_detected"
            elif trusted_promoters > 3:
                score  = 85
                reason = "trusted_promoters"
            elif bot_ratio > 0.4:
                score  = 40
                reason = "mixed_promotion"
            else:
                score  = 65
                reason = "organic_promotion"

            return {
                "score":  score,
                "reason": reason,
                "details": {
                    "total_casts":        total,
                    "bot_promoters":      bot_promoters,
                    "human_promoters":    human_promoters,
                    "trusted_promoters":  trusted_promoters,
                    "bot_ratio_pct":      round(bot_ratio * 100, 1),
                    "bot_farm_detected":  bot_ratio > 0.7,
                }
            }

        except Exception as e:
            print(f"Error assessing promoters: {e}")
            return {"score": 50, "reason": "assessment_failed", "details": {"error": str(e)}}

    # ── Core Analysis ─────────────────────────────────────────────────────────

    def consult_the_stars(self, token_address):
        """
        Full token analysis: on-chain metrics + deployer history + social promotion.
        Returns a unified score and verdict.
        """
        print(f"🔮 Gazing into the void for token: {token_address}...")

        token_data = self.fetch_token_data(token_address)

        if not token_data:
            return {
                "score":   0,
                "verdict": "UNKNOWN (No Data)",
                "details": {"error": "Token not found on DexScreener or no Base pairs"}
            }

        # ── Extract on-chain metrics ──────────────────────────────────────────
        liquidity_usd  = float(token_data['liquidity']['usd'])
        fdv            = float(token_data.get('fdv', 0))
        volume_24h     = float(token_data['volume']['h24'])
        pair_age_hours = (time.time() * 1000 - token_data['pairCreatedAt']) / (1000 * 3600)
        token_symbol   = token_data['baseToken']['symbol']
        deployer_wallet = token_data.get('baseToken', {}).get('address')

        analysis_data = {
            "symbol":          token_symbol,
            "name":            token_data['baseToken']['name'],
            "liquidity_usd":   liquidity_usd,
            "volume_24h":      volume_24h,
            "fdv":             fdv,
            "pair_age_hours":  pair_age_hours,
            "price_change_24h": float(token_data.get('priceChange', {}).get('h24', 0)),
            "price_usd":       float(token_data.get('priceUsd', 0) or 0),
        }

        # ── Deployer assessment ───────────────────────────────────────────────
        print(f"🕵️  Checking deployer history...")
        deployer_assessment = self.assess_deployer(deployer_wallet)

        # Hard veto: known rugger overrides everything
        if deployer_assessment.get('details', {}).get('is_known_rugger'):
            return {
                "score":            0,
                "verdict":          "CURSED",
                "reason":           "Known rugger deployed this token",
                "deployer_score":   deployer_assessment['score'],
                "promoter_score":   0,
                "details":          {**analysis_data, "deployer": deployer_assessment},
            }

        # ── Promoter assessment ───────────────────────────────────────────────
        print(f"📢 Checking social promotion...")
        promoter_assessment = self.assess_promoters(token_address, token_symbol)

        # ── Venice AI scores all three layers in one call ───────────────────
        print(f"🧠 Venice scoring all three trust layers...")
        venice_result  = self._call_venice(
            analysis_data  = analysis_data,
            deployer_data  = deployer_assessment,
            promoter_data  = promoter_assessment,
        )

        token_score    = venice_result.get('token_score',    50)
        deployer_score = venice_result.get('deployer_score', 20)
        promoter_score = venice_result.get('promoter_score', 20)
        venice_verdict = venice_result.get('verdict',        'MORTAL')
        venice_reason  = venice_result.get('reason',         '')

        # ── Weighted final score ──────────────────────────────────────────────
        final_score = int(
            token_score    * 0.50 +
            deployer_score * 0.30 +
            promoter_score * 0.20
        )
        final_score = max(0, min(100, final_score))

        # Use Venice verdict if available, fall back to score-based
        if venice_verdict in ("BLESSED", "MORTAL", "CURSED"):
            verdict = venice_verdict
        else:
            verdict = (
                "BLESSED" if final_score >= 70 else
                "MORTAL"  if final_score >= 40 else
                "CURSED"
            )

        return {
            "score":          final_score * 100,  # 0-10000 for ERC-8004 compat
            "verdict":        f"{verdict} ({venice_reason})",
            "token_score":    token_score,
            "deployer_score": deployer_score,
            "promoter_score": promoter_score,
            "details": {
                **analysis_data,
                "deployer":      deployer_assessment,
                "promoters":     promoter_assessment,
                "venice_reason": venice_reason,
            }
        }

    def _call_venice(
        self,
        analysis_data:    dict,
        deployer_data:    dict | None = None,
        promoter_data:    dict | None = None,
    ) -> dict:
        """
        Call Venice AI to score all three trust layers in one prompt.
        Venice reasons holistically across token + deployer + promoter data
        rather than using hardcoded heuristics for each layer.
        Returns individual scores for token, deployer, promoter + combined verdict.
        """
        venice_api_key = os.getenv("VENICE_API_KEY")
        if not venice_api_key:
            return {
                "token_score":    50,
                "deployer_score": 20,
                "promoter_score": 20,
                "verdict":        "MORTAL",
                "reason":         "No Venice API key configured",
            }

        try:
            headers = {
                "Authorization": f"Bearer {venice_api_key}",
                "Content-Type":  "application/json"
            }

            prompt = f"""
You are The Oracle of Base — an AI that judges crypto tokens for safety and trust.
Analyze ALL THREE layers of trust data below and score each independently.

═══════════════════════════════════════
LAYER 1 — TOKEN ON-CHAIN METRICS
═══════════════════════════════════════
{json.dumps(analysis_data, indent=2)}

═══════════════════════════════════════
LAYER 2 — DEPLOYER HISTORY
═══════════════════════════════════════
{json.dumps(deployer_data or {{"note": "No deployer history found"}}, indent=2)}

═══════════════════════════════════════
LAYER 3 — SOCIAL PROMOTION (Farcaster)
═══════════════════════════════════════
{json.dumps(promoter_data or {{"note": "No social promotion data found"}}, indent=2)}

═══════════════════════════════════════
SCORING INSTRUCTIONS
═══════════════════════════════════════
Score each layer from 0 to 100:

token_score:
  - 0-30:  Rug signals (no liquidity, zero volume, suspicious age)
  - 31-69: Risky (low liquidity, high FDV, volatile)
  - 70-100: Safe (deep liquidity, healthy volume, good age)

deployer_score:
  - 0-20:  Known rugger (>50% of their tokens failed)
  - 21-49: Suspicious (first-time or mixed history)
  - 50-79: Neutral (clean but limited history)
  - 80-100: Trusted (multiple successful tokens)
  - If no deployer data: score 20 (suspicious by default)

promoter_score:
  - 0-20:  Bot farm detected (>70% bot promoters)
  - 21-49: Mixed signals (heavy bot presence)
  - 50-79: Organic (mix of real users)
  - 80-100: Trusted promoters (high-follower real accounts)
  - If no promoter data: score 20 (unknown by default)

Then give an overall verdict (BLESSED/MORTAL/CURSED) and a short poetic reason.

Return ONLY this JSON, no other text:
{{
    "token_score":    <0-100>,
    "deployer_score": <0-100>,
    "promoter_score": <0-100>,
    "verdict":        "<BLESSED|MORTAL|CURSED>",
    "reason":         "<one sentence poetic reason>"
}}
"""

            payload = {
                "model": "qwen3-5-9b",
                "messages": [
                    {
                        "role":    "system",
                        "content": "You are a precise crypto trust oracle. Output only valid JSON. No markdown, no explanation, no preamble."
                    },
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.4,  # Lower temp = more consistent scoring
            }

            response = requests.post(
                "https://api.venice.ai/api/v1/chat/completions",
                headers=headers, json=payload, timeout=45
            )
            response.raise_for_status()
            result = response.json()['choices'][0]['message']['content']

            # Strip markdown fences if present
            if "```json" in result:
                result = result.split("```json")[1].split("```")[0].strip()
            elif "```" in result:
                result = result.split("```")[1].split("```")[0].strip()

            parsed = json.loads(result)

            return {
                "token_score":    int(parsed.get('token_score',    50)),
                "deployer_score": int(parsed.get('deployer_score', 20)),
                "promoter_score": int(parsed.get('promoter_score', 20)),
                "verdict":        parsed.get('verdict', 'MORTAL'),
                "reason":         parsed.get('reason',  ''),
            }

        except Exception as e:
            print(f"❌ Venice API error: {e}")
            return {
                "token_score":    50,
                "deployer_score": 20,
                "promoter_score": 20,
                "verdict":        "MORTAL",
                "reason":         f"Venice unreachable: {str(e)}",
            }

    # ── Attestation ───────────────────────────────────────────────────────────

    def generate_attestation(self, token_address, analysis_result):
        """Format analysis into ERC-8004 Reputation Registry payload."""
        return {
            "agentId":        self.agent_id,
            "target":         token_address,
            "value":          analysis_result['score'],
            "valueDecimals":  2,
            "tag1":           "financial-prophecy",
            "tag2":           "token-safety",
            "token_score":    analysis_result.get('token_score'),
            "deployer_score": analysis_result.get('deployer_score'),
            "promoter_score": analysis_result.get('promoter_score'),
            "endpoint":       self.endpoint,
            "metadata_ipfs":  "ipfs://QmPlaceholderForFullReport",
            "timestamp":      int(time.time()),
            "uid":            hashlib.sha256(
                f"{self.agent_id}{token_address}{int(time.time())}".encode()
            ).hexdigest(),
        }