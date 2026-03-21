import json
import time
import hashlib
import urllib.request
import os
import requests

# ERC-8004 Registry Addresses (Base Mainnet)
# Global semaphore — only one Venice call at a time across all threads
import threading as _threading
_venice_lock = _threading.Semaphore(1)
IDENTITY_REGISTRY   = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
REPUTATION_REGISTRY = "0x8004BAa17C55a88189AE136b182e5fdA19dE9b63"

BASE_RPC = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")


class FinancialProphet:
    def __init__(self, agent_id, private_key):
        self.agent_id    = agent_id
        self.private_key = private_key
        self.endpoint    = "https://oracle.nanobot.dev/api/v1"

    # ── Raw Signal Collection ─────────────────────────────────────────────────
    # These methods collect EVERYTHING DexScreener knows.
    # No scoring, no thresholds — just raw data for Venice to reason about.

    def fetch_token_data(self, token_address: str) -> dict | None:
        """Fetch most liquid Base pair from DexScreener."""
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode())
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
            print(f"DexScreener fetch error: {e}")
            return None

    def collect_token_signals(self, token_data: dict) -> dict:
        """
        Extract ALL behavioural signals from a DexScreener pair.
        This is the raw material Venice reasons about — no scoring here.
        """
        now_ms         = time.time() * 1000
        pair_created   = token_data.get('pairCreatedAt', now_ms)
        age_hours      = (now_ms - pair_created) / (1000 * 3600)
        age_days       = age_hours / 24

        liquidity_usd  = float(token_data.get('liquidity', {}).get('usd', 0) or 0)
        fdv            = float(token_data.get('fdv', 0) or 0)
        market_cap     = float(token_data.get('marketCap', 0) or 0)
        price_usd      = float(token_data.get('priceUsd', 0) or 0)

        volume         = token_data.get('volume', {})
        vol_5m         = float(volume.get('m5',  0) or 0)
        vol_1h         = float(volume.get('h1',  0) or 0)
        vol_6h         = float(volume.get('h6',  0) or 0)
        vol_24h        = float(volume.get('h24', 0) or 0)

        price_change   = token_data.get('priceChange', {})
        change_5m      = float(price_change.get('m5',  0) or 0)
        change_1h      = float(price_change.get('h1',  0) or 0)
        change_6h      = float(price_change.get('h6',  0) or 0)
        change_24h     = float(price_change.get('h24', 0) or 0)

        txns           = token_data.get('txns', {})
        buys_5m        = int(txns.get('m5',  {}).get('buys',  0) or 0)
        sells_5m       = int(txns.get('m5',  {}).get('sells', 0) or 0)
        buys_1h        = int(txns.get('h1',  {}).get('buys',  0) or 0)
        sells_1h       = int(txns.get('h1',  {}).get('sells', 0) or 0)
        buys_24h       = int(txns.get('h24', {}).get('buys',  0) or 0)
        sells_24h      = int(txns.get('h24', {}).get('sells', 0) or 0)

        total_txns_24h = buys_24h + sells_24h
        buy_ratio_24h  = (
            round(buys_24h / total_txns_24h, 3)
            if total_txns_24h > 0 else 0
        )

        # Derived ratios that signal risk
        fdv_liquidity_ratio = round(fdv / liquidity_usd, 2) if liquidity_usd > 0 else 999
        vol_liquidity_ratio = round(vol_24h / liquidity_usd, 3) if liquidity_usd > 0 else 0

        # Volume deceleration: is volume dying off?
        vol_trend = None
        if vol_1h > 0 and vol_6h > 0:
            hourly_avg_6h = vol_6h / 6
            vol_trend = "accelerating" if vol_1h > hourly_avg_6h * 1.5 else \
                        "decelerating" if vol_1h < hourly_avg_6h * 0.5 else "stable"

        return {
            # Identity
            "symbol":             token_data['baseToken']['symbol'],
            "name":               token_data['baseToken']['name'],
            "token_address":      token_data['baseToken']['address'],
            "pair_address":       token_data.get('pairAddress', ''),
            "dex":                token_data.get('dexId', ''),

            # Age
            "age_hours":          round(age_hours, 1),
            "age_days":           round(age_days, 1),
            "is_new":             age_hours < 48,

            # Liquidity
            "liquidity_usd":      liquidity_usd,
            "fdv":                fdv,
            "market_cap":         market_cap,
            "price_usd":          price_usd,
            "fdv_liquidity_ratio": fdv_liquidity_ratio,  # >100 = very overvalued vs liquidity

            # Volume
            "volume_5m":          vol_5m,
            "volume_1h":          vol_1h,
            "volume_6h":          vol_6h,
            "volume_24h":         vol_24h,
            "vol_liquidity_ratio": vol_liquidity_ratio,  # >1 = high turnover
            "volume_trend":       vol_trend,

            # Price action
            "price_change_5m":    change_5m,
            "price_change_1h":    change_1h,
            "price_change_6h":    change_6h,
            "price_change_24h":   change_24h,

            # Transaction behaviour
            "buys_5m":            buys_5m,
            "sells_5m":           sells_5m,
            "buys_1h":            buys_1h,
            "sells_1h":           sells_1h,
            "buys_24h":           buys_24h,
            "sells_24h":          sells_24h,
            "total_txns_24h":     total_txns_24h,
            "buy_ratio_24h":      buy_ratio_24h,  # >0.9 = suspicious one-sided buying

            # Liquidity providers
            "lp_count":           token_data.get('liquidity', {}).get('count', 0),
        }

    def collect_deployer_signals(self, token_address: str) -> dict:
        """
        Collect raw deployer behavioural signals from DexScreener.
        For new deployers with no history, the token's OWN behaviour
        IS the deployer signal — Venice reasons from that.
        """
        try:
            url = f"https://api.dexscreener.com/latest/dex/search?q={token_address}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as r:
                data  = json.loads(r.read().decode())
                pairs = [p for p in (data.get('pairs') or []) if p.get('chainId') == 'base']

            if not pairs or len(pairs) <= 1:
                return {
                    "deployer_history": "unknown",
                    "note": "First token from this deployer or deployer not identifiable",
                    "previous_tokens":  0,
                    "risk_implication": "No track record — treat with caution",
                }

            # Analyse all other tokens from same deployer
            token_profiles = []
            for pair in pairs[:10]:  # cap at 10
                p_age_h    = (time.time() * 1000 - pair.get('pairCreatedAt', 0)) / (1000 * 3600)
                p_liq      = float(pair.get('liquidity', {}).get('usd', 0) or 0)
                p_vol_24h  = float(pair.get('volume', {}).get('h24', 0) or 0)
                p_change   = float(pair.get('priceChange', {}).get('h24', 0) or 0)

                outcome = (
                    "rugged"     if p_age_h > 48 and p_liq < 1000 and p_vol_24h < 100 else
                    "thriving"   if p_liq > 50000 and p_vol_24h > 5000 else
                    "alive"      if p_liq > 5000 else
                    "struggling"
                )

                token_profiles.append({
                    "symbol":       pair['baseToken']['symbol'],
                    "age_hours":    round(p_age_h, 1),
                    "liquidity":    p_liq,
                    "volume_24h":   p_vol_24h,
                    "price_change": p_change,
                    "outcome":      outcome,
                })

            total   = len(token_profiles)
            rugged  = sum(1 for t in token_profiles if t['outcome'] == 'rugged')
            thriving = sum(1 for t in token_profiles if t['outcome'] == 'thriving')

            return {
                "deployer_history":  "known",
                "previous_tokens":   total,
                "rugged_count":      rugged,
                "thriving_count":    thriving,
                "rug_rate_pct":      round(rugged / total * 100, 1) if total else 0,
                "token_profiles":    token_profiles,
                "risk_implication":  (
                    "KNOWN RUGGER — multiple failed tokens"  if rugged / total > 0.5 else
                    "TRUSTED DEPLOYER — strong track record" if thriving / total > 0.5 else
                    "MIXED HISTORY — proceed with caution"
                ) if total > 0 else "No history"
            }

        except Exception as e:
            print(f"Deployer signal error: {e}")
            return {
                "deployer_history": "error",
                "note": str(e),
                "previous_tokens": 0,
            }

    def collect_promoter_signals(self, token_symbol: str) -> dict:
        """
        Collect raw social promotion signals from Farcaster.
        Venice reads who is talking about this token and draws its own conclusions.
        """
        try:
            search_term = token_symbol.replace('$', '').lower()
            url     = f"https://client.warpcast.com/v2/search-casts?q=%24{search_term}&limit=20"
            resp    = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)

            if resp.status_code != 200:
                return {
                    "social_presence": "none",
                    "note": "No Farcaster data available",
                    "cast_count": 0,
                }

            casts = resp.json().get('result', {}).get('casts', [])
            if not casts:
                return {
                    "social_presence": "none",
                    "cast_count": 0,
                    "note": "No casts found — unknown social footprint",
                }

            promoter_profiles = []
            for cast in casts[:20]:
                author    = cast.get('author', {})
                bio       = (author.get('profile', {}).get('bio', {}).get('text') or '').lower()
                followers = int(author.get('followerCount', 0))
                following = int(author.get('followingCount', 0))
                username  = author.get('username', '')

                promoter_profiles.append({
                    "username":     username,
                    "bio_snippet":  bio[:100],
                    "followers":    followers,
                    "following":    following,
                    "follow_ratio": round(following / max(followers, 1), 2),
                    "cast_text":    (cast.get('text') or '')[:120],
                })

            return {
                "social_presence":  "found",
                "cast_count":       len(casts),
                "promoter_profiles": promoter_profiles,
                "note": "Venice should assess: are these real community members or coordinated shills?"
            }

        except Exception as e:
            print(f"Promoter signal error: {e}")
            return {
                "social_presence": "error",
                "note": str(e),
                "cast_count": 0,
            }

    # ── Venice AI — the brain ─────────────────────────────────────────────────

    def _trim_for_venice(self, signals: dict) -> dict:
        """
        Keep only the highest-signal keys before sending to Venice.
        Reduces prompt from ~2000 tokens to ~300 tokens.
        Faster response, lower cost, same analytical quality.
        """
        priority = {
            # Token signals
            'symbol', 'liquidity_usd', 'volume_24h', 'fdv',
            'age_hours', 'is_new', 'buy_ratio_24h',
            'fdv_liquidity_ratio', 'vol_liquidity_ratio',
            'price_change_24h', 'price_change_1h',
            'volume_trend', 'total_txns_24h', 'lp_count',
            # Deployer signals
            'deployer_history', 'previous_tokens', 'rugged_count',
            'thriving_count', 'rug_rate_pct', 'risk_implication',
            # Promoter signals
            'social_presence', 'cast_count', 'bot_promoters',
            'trusted_promoters', 'note',
        }
        return {k: v for k, v in signals.items() if k in priority}

    def _call_venice(
        self,
        token_signals:    dict,
        deployer_signals: dict,
        promoter_signals: dict,
    ) -> dict:
        """
        Pass ALL raw signals to Venice and let it reason like an analyst.
        Venice derives its own scores from the data — no hardcoded thresholds.
        The prompt tells Venice WHAT signals matter and WHY, not what score to give.
        """
        venice_api_key = os.getenv("VENICE_API_KEY")
        if not venice_api_key:
            return {
                "token_score":    50,
                "deployer_score": 20,
                "promoter_score": 20,
                "verdict":        "MORTAL",
                "reason":         "No Venice API key — cannot assess",
            }

        try:
            headers = {
                "Authorization": f"Bearer {venice_api_key}",
                "Content-Type":  "application/json"
            }

            # Trim signals to reduce prompt size and speed up Venice
            token_s    = self._trim_for_venice(token_signals)
            deployer_s = self._trim_for_venice(deployer_signals)
            promoter_s = self._trim_for_venice(promoter_signals)

            prompt = f"""
You are The Oracle of Base — an agentic AI analyst that assesses crypto token trust.
You receive raw signals and reason like an experienced DeFi analyst, not a rules engine.

Your job: read the signals, spot the patterns, assign independent scores for each layer.

════════════════════════════════════════════
SIGNAL LAYER 1: TOKEN ON-CHAIN BEHAVIOUR
════════════════════════════════════════════
{json.dumps(token_s, indent=2)}

WHAT TO LOOK FOR:
- Is buy_ratio_24h suspiciously high (>0.85)? Coordinated buying = pump incoming.
- Is fdv_liquidity_ratio massive (>100)? Token is wildly overvalued vs real liquidity.
- Does volume dry up quickly (decelerating trend)? Interest dying fast.
- New token (<48h) with huge price spike then drop? Classic pump and dump shape.
- Healthy token has: growing liquidity, balanced buy/sell ratio, sustainable volume.

════════════════════════════════════════════
SIGNAL LAYER 2: DEPLOYER TRACK RECORD
════════════════════════════════════════════
{json.dumps(deployer_s, indent=2)}

WHAT TO LOOK FOR:
- Unknown deployer (no history) is NOT automatically bad — every deployer starts somewhere.
  But unknown + suspicious token signals = treat harshly.
- Known rugger (>50% failed tokens) = hard penalty regardless of how good the token looks.
- Serial successful deployer = strong trust boost.
- Look at the token_profiles: what happened to their previous tokens over time?

════════════════════════════════════════════
SIGNAL LAYER 3: SOCIAL PROMOTION PATTERNS
════════════════════════════════════════════
{json.dumps(promoter_s, indent=2)}

WHAT TO LOOK FOR:
- Promoters with high follow_ratio (following >> followers) = bot accounts.
- Multiple accounts posting similar text = coordinated shill campaign.
- No social presence is NOT automatically bad for a new token — could be organic.
- Real community: diverse bios, genuine engagement, varied follower counts.
- Bot farm: identical bios, high follow ratios, low followers, repetitive cast text.

════════════════════════════════════════════
SCORING
════════════════════════════════════════════
Score each layer 0-100 based on what the signals actually show.
Use your analyst judgment — do not apply mechanical rules.

token_score:    How safe is this token based on on-chain behaviour?
deployer_score: How trustworthy is this deployer based on their history?
promoter_score: How organic and legitimate is the social promotion?

Then give your overall verdict and one sentence of reasoning.

Return ONLY this exact JSON:
{{
    "token_score":    <0-100>,
    "deployer_score": <0-100>,
    "promoter_score": <0-100>,
    "verdict":        "<BLESSED|MORTAL|CURSED>",
    "reason":         "<one sentence analyst-style reasoning>"
}}
"""

            payload = {
                "model": os.getenv("VENICE_MODEL", "grok-41-fast"),
                "messages": [
                    {
                        "role":    "system",
                        "content": "You are an expert DeFi analyst. Output only valid JSON. No markdown, no preamble, no explanation outside the JSON."
                    },
                    {"role": "user", "content": prompt}
                ],
                "temperature": float(os.getenv("VENICE_TEMPERATURE", "0.9")),
                "max_tokens":  200,  # scores + verdict only — keep response small and fast
            }

            import time as _time
            acquired = _venice_lock.acquire(timeout=300)  # wait up to 5 min
            if not acquired:
                raise TimeoutError("Venice lock wait timed out — too many queued predictions")
            try:
                response = requests.post(
                    "https://api.venice.ai/api/v1/chat/completions",
                    headers=headers, json=payload,
                    timeout=int(os.getenv("VENICE_TIMEOUT", "60"))
                )
                response.raise_for_status()
                result = response.json()['choices'][0]['message']['content']
            finally:
                _venice_lock.release()

            # Strip markdown fences
            if "```json" in result:
                result = result.split("```json")[1].split("```")[0].strip()
            elif "```" in result:
                result = result.split("```")[1].split("```")[0].strip()

            parsed = json.loads(result)
            return {
                "token_score":    max(0, min(100, int(parsed.get('token_score',    50)))),
                "deployer_score": max(0, min(100, int(parsed.get('deployer_score', 20)))),
                "promoter_score": max(0, min(100, int(parsed.get('promoter_score', 20)))),
                "verdict":        parsed.get('verdict', 'MORTAL'),
                "reason":         parsed.get('reason', ''),
            }

        except Exception as e:
            print(f"Venice API error: {e}")
            return {
                "token_score":    50,
                "deployer_score": 20,
                "promoter_score": 20,
                "verdict":        "MORTAL",
                "reason":         f"Venice unreachable: {str(e)}",
            }

    # ── Core Analysis ─────────────────────────────────────────────────────────

    def consult_the_stars(self, token_address: str) -> dict:
        """
        Full agentic token analysis.
        Collects all raw signals, passes to Venice, returns unified verdict.
        """
        print(f"🔮 Gazing into the void for token: {token_address}...")

        token_data = self.fetch_token_data(token_address)
        if not token_data:
            return {
                "score":   0,
                "verdict": "UNKNOWN (No Data)",
                "details": {"error": "Token not found on DexScreener or no Base pairs"}
            }

        # ── Collect raw signals ───────────────────────────────────────────────
        print("📊 Collecting on-chain signals...")
        token_signals = self.collect_token_signals(token_data)

        print("🕵️  Collecting deployer signals...")
        deployer_signals = self.collect_deployer_signals(token_address)

        print("📢 Collecting social signals...")
        promoter_signals = self.collect_promoter_signals(token_signals['symbol'])

        # ── Venice reasons across all signals ────────────────────────────────
        print("🧠 Venice analysing all signals...")
        venice = self._call_venice(token_signals, deployer_signals, promoter_signals)

        token_score    = venice['token_score']
        deployer_score = venice['deployer_score']
        promoter_score = venice['promoter_score']
        venice_verdict = venice['verdict']
        venice_reason  = venice['reason']

        # ── Weighted final score ──────────────────────────────────────────────
        final_score = int(
            token_score    * 0.50 +
            deployer_score * 0.30 +
            promoter_score * 0.20
        )
        final_score = max(0, min(100, final_score))

        # Trust Venice's verdict if it's valid, fall back to score-based
        verdict = (
            venice_verdict
            if venice_verdict in ("BLESSED", "MORTAL", "CURSED")
            else (
                "BLESSED" if final_score >= 70 else
                "MORTAL"  if final_score >= 40 else
                "CURSED"
            )
        )

        return {
            "score":          final_score * 100,  # 0-10000 for ERC-8004
            "verdict":        f"{verdict} ({venice_reason})",
            "token_score":    token_score,
            "deployer_score": deployer_score,
            "promoter_score": promoter_score,
            "price_usd":      token_signals.get('price_usd', 0),
            "details": {
                "token":    token_signals,
                "deployer": deployer_signals,
                "promoter": promoter_signals,
                "venice_reason": venice_reason,
            }
        }

    # ── Attestation ───────────────────────────────────────────────────────────

    def generate_attestation(self, token_address: str, analysis_result: dict) -> dict:
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