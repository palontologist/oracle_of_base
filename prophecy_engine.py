import json
import time
import hashlib
import urllib.request
import os
import requests

from utils.ens import enrich_address, reverse_lookup

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
        """
        Fetch most liquid Base pair from DexScreener.
        Tries the token endpoint first, then search fallback.
        Handles both checksummed and lowercase addresses.
        """
        def _best_base_pair(pairs: list) -> dict | None:
            if not pairs:
                return None
            base_pairs = [p for p in pairs if p.get('chainId') == 'base']
            if not base_pairs:
                return None
            return sorted(
                base_pairs,
                key=lambda x: float(x.get('liquidity', {}).get('usd', 0) or 0),
                reverse=True
            )[0]

        addr = token_address.strip()

        # Try 1: /tokens/ endpoint (works for most tokens)
        for attempt_addr in [addr, addr.lower()]:
            try:
                url = f"https://api.dexscreener.com/latest/dex/tokens/{attempt_addr}"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=12) as r:
                    data = json.loads(r.read().decode())
                    result = _best_base_pair(data.get('pairs') or [])
                    if result:
                        return result
            except Exception as e:
                print(f"DexScreener token endpoint error ({attempt_addr[:10]}): {e}")

        # Try 2: /search/ endpoint (catches tokens with different indexing)
        try:
            url = f"https://api.dexscreener.com/latest/dex/search?q={addr}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=12) as r:
                data = json.loads(r.read().decode())
                result = _best_base_pair(data.get('pairs') or [])
                if result:
                    print(f"Found via DexScreener search fallback")
                    return result
        except Exception as e:
            print(f"DexScreener search error: {e}")

        # Try 3: GeckoTerminal as second source
        try:
            url = f"https://api.geckoterminal.com/api/v2/networks/base/tokens/{addr.lower()}"
            req = urllib.request.Request(
                url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode())
                attrs = data.get('data', {}).get('attributes', {})
                if attrs:
                    # Convert GeckoTerminal format to DexScreener-like shape
                    return {
                        'chainId':       'base',
                        'baseToken':     {'address': addr, 'symbol': attrs.get('symbol',''), 'name': attrs.get('name','')},
                        'priceUsd':      attrs.get('price_usd'),
                        'liquidity':     {'usd': float(attrs.get('reserve_in_usd', 0) or 0)},
                        'fdv':           float(attrs.get('fdv_usd', 0) or 0),
                        'marketCap':     float(attrs.get('market_cap_usd', 0) or 0),
                        'volume':        {'h24': float(attrs.get('volume_usd', {}).get('h24', 0) or 0)},
                        'priceChange':   {'h24': float(attrs.get('price_change_percentage', {}).get('h24', 0) or 0)},
                        'pairCreatedAt': 0,
                        'txns':          {},
                        '_source':       'geckoterminal',
                    }
        except Exception as e:
            print(f"GeckoTerminal fallback error: {e}")

        print(f"Could not find token {addr[:10]}... on any source")
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
            "lifecycle_stage": (
                "brand_new"   if age_hours < 6
                else "new"    if age_hours < 72
                else "growing" if age_days < 30
                else "maturing" if age_days < 180
                else "established"
            ),

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
                ens = enrich_address(token_address)
                return {
                    "deployer_history": "unknown",
                    "note":             "First token from this deployer or deployer not identifiable",
                    "previous_tokens":  0,
                    "ens_name":         ens["ens_name"],
                    "ens_signal":       ens["ens_signal"],
                    "deployer_display": ens["display"],
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
        token_signals:      dict,
        deployer_signals:   dict,
        promoter_signals:   dict,
        lifecycle_context:  str = "",
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
You are The Oracle of Base — an agentic AI analyst scoring any Base chain token.
You score both brand new launches AND established tokens that are weeks, months, or years old.
Read the signals like an experienced DeFi trader, not a checklist.

════ TOKEN LIFECYCLE CONTEXT ════
{lifecycle_context}
═════════════════════════════════

Your read must match the lifecycle. A new token and a year-old token
need completely different lenses. Read the age_hours signal to calibrate.

════════════════════════════════════════════
SIGNAL LAYER 1: TOKEN ON-CHAIN BEHAVIOUR
════════════════════════════════════════════
{json.dumps(token_s, indent=2)}

WHAT TO LOOK FOR — adapt by lifecycle:

NEW TOKEN (<72h):
  - Suspicious buy_ratio_24h (>0.85) = coordinated pump incoming
  - Massive fdv_liquidity_ratio (>100) = valuation has no backing
  - Price spike immediately then collapse = classic pump shape
  - Very thin liquidity + high volume = wash trading

ESTABLISHED TOKEN (weeks to months old):
  - Is liquidity growing, stable, or bleeding? Trend matters more than absolute size
  - Volume/liquidity ratio: healthy is 0.2-2x daily. <0.05 = ghost token, >5x = unusual activity
  - Price trend over time: sustained drawdown is different from a dip — read it honestly
  - Still has community? Check buy/sell balance and transaction count

MATURE/OG TOKEN (months to years, like DEGEN, BRETT, etc):
  - These tokens survived — deployer is proven, contract is audited
  - Score is about current market health and risk, not rug potential
  - Deep drawdown from ATH ≠ CURSED — it means MORTAL with context
  - Active holders (1M+) + real utility = BLESSED even at low price
  - Very low volume on high holder count = zombie token = MORTAL

════════════════════════════════════════════
SIGNAL LAYER 2: DEPLOYER TRACK RECORD
════════════════════════════════════════════
{json.dumps(deployer_s, indent=2)}

WHAT TO LOOK FOR:
- New token + unknown deployer + suspicious signals = weight this harshly
- Established token: deployer pattern matters less — the token's own survival is the signal
- Known rugger (>50% failed) = hard penalty for new tokens, context for old ones
- Serial builder with thriving tokens = trust boost at any age

════════════════════════════════════════════
SIGNAL LAYER 3: SOCIAL PROMOTION
════════════════════════════════════════════
{json.dumps(promoter_s, indent=2)}

WHAT TO LOOK FOR:
- New token: bot farm promotion = strong CURSED signal
- Established token: organic community that survived = strong positive signal
- No social presence on a mature token = not a red flag, just a quiet community
- Coordinated shill on any age token = suspicious

════════════════════════════════════════════
SCORING
════════════════════════════════════════════
Score each layer 0-100. Use analyst judgment for the token's actual lifecycle stage.

For mature/OG tokens:
  - CURSED means genuine danger right now (liquidity drain, team exit, exploit risk)
  - MORTAL means hold with caution, risk is present but survivable
  - BLESSED means healthy fundamentals, worth engaging with

Return ONLY this exact JSON:
{{
    "token_score":    <0-100>,
    "deployer_score": <0-100>,
    "promoter_score": <0-100>,
    "verdict":        "<BLESSED|MORTAL|CURSED>",
    "reason":         "<one sentence analyst-style reasoning — include lifecycle context>"
}}
"""

            model = os.getenv("VENICE_MODEL", "qwen3-5-9b")

            # qwen3-5-9b is a thinking model — it writes <think>...</think> before JSON.
            # On a large token+deployer+social prompt the model can exhaust its budget
            # thinking and produce no JSON. Two fixes:
            #   1. Raise max_tokens to 2000 so there's budget for both thinking + output
            #   2. Inject "budget_tokens" parameter to cap the think block at 800 tokens
            is_thinking_model = any(x in model for x in ["qwen3", "qwen2.5", "deepseek-r1"])
            max_tok = 8000 if is_thinking_model else 2000

            payload = {
                "model": model,
                "messages": [
                    {
                        "role":    "system",
                        "content": "You are an expert DeFi analyst. Output only valid JSON. No markdown, no preamble, no explanation outside the JSON. Be concise."
                    },
                    {"role": "user", "content": prompt}
                ],
                "temperature": float(os.getenv("VENICE_TEMPERATURE", "0.3")),
                "max_tokens":  int(os.getenv("VENICE_MAX_TOKENS", str(max_tok))),
            }

            # Thinking model controls — cap think budget so output tokens are preserved
            if is_thinking_model:
                payload["venice_parameters"] = {"include_venice_system_prompt": False}
                # budget_tokens caps the <think> block, leaving the rest for JSON output
                payload["thinking"] = {"type": "enabled", "budget_tokens": 6000}

            acquired = _venice_lock.acquire(timeout=120)
            if not acquired:
                raise TimeoutError("Venice lock wait timed out")

            last_error = None
            result = None

            try:
                # Retry up to 3 times — Venice occasionally returns empty body
                for attempt in range(3):
                    try:
                        response = requests.post(
                            "https://api.venice.ai/api/v1/chat/completions",
                            headers=headers, json=payload,
                            timeout=int(os.getenv("VENICE_TIMEOUT", "60"))
                        )
                        response.raise_for_status()

                        # Guard against empty body
                        raw_body = response.text.strip()
                        if not raw_body:
                            last_error = f"empty body (attempt {attempt+1})"
                            print(f"⚠️  Venice {last_error}")
                            time.sleep(3)
                            continue

                        data = response.json()
                        content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
                        if not content or not content.strip():
                            last_error = f"empty content (attempt {attempt+1})"
                            print(f"⚠️  Venice {last_error}")
                            time.sleep(3)
                            continue

                        result = content.strip()
                        break  # success

                    except Exception as e:
                        last_error = str(e)
                        print(f"⚠️  Venice attempt {attempt+1} failed: {e}")
                        if attempt < 2:
                            time.sleep(3)
            finally:
                _venice_lock.release()  # ALWAYS release, even on exception

            if not result:
                raise Exception(last_error or "Venice returned no usable response after 3 attempts")

            # Strip <think>...</think> blocks (qwen3, deepseek-r1 thinking models)
            import re as _re
            result_clean = _re.sub(r'<think>[\s\S]*?</think>', '', result, flags=_re.IGNORECASE).strip()
            if result_clean:
                result = result_clean

            # Strip markdown fences
            if "```json" in result:
                result = result.split("```json")[1].split("```")[0].strip()
            elif "```" in result:
                result = result.split("```")[1].split("```")[0].strip()

            # Last resort: extract first JSON object if result still has noise
            if result and not result.startswith("{"):
                json_match = _re.search(r'\{[\s\S]+\}', result)
                if json_match:
                    result = json_match.group(0)

            # Handle <think>...</think> tags from reasoning models
            if "<think>" in result:
                result = result.split("</think>")[-1].strip()

            if not result:
                raise Exception("Venice response was empty after stripping")

            parsed = json.loads(result)
            return {
                "token_score":    max(0, min(100, int(parsed.get('token_score',    50)))),
                "deployer_score": max(0, min(100, int(parsed.get('deployer_score', 20)))),
                "promoter_score": max(0, min(100, int(parsed.get('promoter_score', 20)))),
                "verdict":        parsed.get('verdict', 'MORTAL'),
                "reason":         parsed.get('reason', ''),
            }

        except Exception as e:
            # Make sure lock is released on any unexpected error
            try:
                _venice_lock.release()
            except Exception:
                pass
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

        # Enrich with Basescan holder count for established tokens
        age_h = float(token_signals.get("age_hours", 0) or 0)
        if age_h > 72:   # only worth fetching for non-brand-new tokens
            try:
                bscan = requests.get(
                    f"https://api.basescan.org/api"
                    f"?module=token&action=tokenholderlist"
                    f"&contractaddress={token_address}&page=1&offset=1",
                    timeout=6
                )
                # Basescan doesn't give total count directly but we can infer from
                # the market data; use CoinGecko as fallback for well-known tokens
                cg = requests.get(
                    f"https://api.coingecko.com/api/v3/coins/base/contract/{token_address}",
                    timeout=6
                )
                if cg.ok:
                    cg_data = cg.json()
                    token_signals["coingecko_id"]     = cg_data.get("id")
                    token_signals["coingecko_name"]   = cg_data.get("name")
                    token_signals["market_cap_rank"]  = cg_data.get("market_cap_rank")
                    token_signals["developer_score"]  = cg_data.get("developer_score")
                    token_signals["community_score"]  = cg_data.get("community_score")
                    token_signals["twitter_followers"] = cg_data.get("community_data", {}).get("twitter_followers")
                    dev = cg_data.get("developer_data", {})
                    token_signals["github_commits_4w"] = dev.get("commit_count_4_weeks")
                    token_signals["github_stars"]      = dev.get("stars")
                    desc = cg_data.get("description", {}).get("en", "")
                    token_signals["description"]       = desc[:200] if desc else None
                    # Holder count from community data
                    holders = cg_data.get("community_data", {}).get("reddit_subscribers")
                    if holders:
                        token_signals["reddit_subscribers"] = holders
            except Exception:
                pass   # enrichment is best-effort

        print("🕵️  Collecting deployer signals...")
        deployer_signals = self.collect_deployer_signals(token_address)

        print("📢 Collecting social signals...")
        promoter_signals = self.collect_promoter_signals(token_signals['symbol'])

        # ── Venice reasons across all signals ────────────────────────────────
        # ── Build lifecycle context here so it's in scope ───────────────────
        _age_h   = float(token_signals.get("age_hours", 0) or 0)
        _age_d   = _age_h / 24
        _liq     = float(token_signals.get("liquidity_usd", 0) or 0)
        _holders = token_signals.get("holder_count", 0) or 0

        if _age_h < 6:
            _lifecycle = (
                f"BRAND NEW TOKEN — {_age_h:.1f} hours old. "
                f"Zero track record. Weight rug signals heavily. "
                f"Liquidity: ${_liq:,.0f}."
            )
        elif _age_h < 72:
            _lifecycle = (
                f"NEW TOKEN — {_age_d:.1f} days old. "
                f"Early signals forming. Watch for pump-and-dump shape. "
                f"Liquidity: ${_liq:,.0f}."
            )
        elif _age_d < 30:
            _lifecycle = (
                f"GROWING TOKEN — {_age_d:.0f} days old. "
                f"Survival past launch phase is positive. "
                f"Score current health and trajectory, not just rug risk. "
                f"Liquidity: ${_liq:,.0f}."
            )
        elif _age_d < 180:
            _lifecycle = (
                f"MATURING TOKEN — {_age_d:.0f} days old ({_age_d/30:.1f} months). "
                f"Has survived multiple market cycles. "
                f"Focus on sustained activity, community health, and current trend direction. "
                f"Liquidity: ${_liq:,.0f}."
                + (f" Holders: {_holders:,}." if _holders else "")
            )
        else:
            _lifecycle = (
                f"ESTABLISHED/OG TOKEN — {_age_d:.0f} days old ({_age_d/365:.1f} years). "
                f"This token has a track record. Rug risk is low — score market health instead. "
                f"Deep drawdown from ATH is not CURSED — assess current fundamentals honestly. "
                f"Liquidity: ${_liq:,.0f}."
                + (f" Holders: {_holders:,}." if _holders else "")
            )

        print("🧠 Venice analysing all signals...")
        venice = self._call_venice(
            token_signals, deployer_signals, promoter_signals,
            lifecycle_context=_lifecycle
        )

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

        # ENS enrichment for the token itself
        token_ens = enrich_address(token_address)

        return {
            "score":            final_score * 100,  # 0-10000 for ERC-8004
            "verdict":          f"{verdict} ({venice_reason})",
            "token_score":      token_score,
            "deployer_score":   deployer_score,
            "ens_name":         token_ens["ens_name"],
            "deployer_ens":     deployer_signals.get("ens_name"),
            "deployer_display": deployer_signals.get("deployer_display", token_address[:10] + "..."),
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