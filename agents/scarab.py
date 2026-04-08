"""
agents/scarab.py — SCARAB (Token Analyzer)
------------------------------------------
Collects and interprets on-chain metrics for any Base token.
Responsible for: DexScreener, GeckoTerminal, Basescan, CoinGecko signals.
Reports to: PROPHET orchestrator.

Named after the scarab beetle — reads the earth (on-chain data) to find
what's alive and what's already dead.
"""

import os, time, json, logging, urllib.request, requests
log = logging.getLogger("scarab")

class Scarab:
    """On-chain token signal collector and interpreter."""

    AGENT_ID   = "SCARAB"
    ROLE       = "token_analyzer"
    REPORTS_TO = "PROPHET"

    def __init__(self):
        self.base_rpc = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")

    def fetch(self, token_address: str) -> dict:
        """
        Full token signal collection pipeline.
        Returns structured signals for PROPHET to route to Venice.
        """
        log.info(f"[SCARAB] Scanning {token_address[:10]}...")

        pair = self._fetch_dexscreener(token_address)
        if not pair:
            return {"agent": self.AGENT_ID, "error": "token_not_found", "address": token_address}

        signals = self._extract_signals(pair)
        signals["agent"]  = self.AGENT_ID
        signals["source"] = pair.get("_source", "dexscreener")

        # Enrich established tokens with CoinGecko
        if signals.get("age_hours", 0) > 72:
            cg = self._fetch_coingecko(token_address)
            if cg:
                signals.update(cg)

        log.info(f"[SCARAB] {signals.get('symbol')} | liq=${signals.get('liquidity_usd',0):,.0f} | age={signals.get('age_days',0):.0f}d | lifecycle={signals.get('lifecycle_stage')}")
        return signals

    def _fetch_dexscreener(self, addr: str) -> dict | None:
        for a in [addr, addr.lower()]:
            try:
                req = urllib.request.Request(
                    f"https://api.dexscreener.com/latest/dex/tokens/{a}",
                    headers={"User-Agent": "OracleOfBase/2.0"}
                )
                with urllib.request.urlopen(req, timeout=12) as r:
                    data = json.loads(r.read().decode())
                    pairs = [p for p in (data.get("pairs") or []) if p.get("chainId") == "base"]
                    if pairs:
                        return sorted(pairs, key=lambda p: float(p.get("liquidity",{}).get("usd",0) or 0), reverse=True)[0]
            except Exception as e:
                log.debug(f"[SCARAB] DexScreener {a[:8]}: {e}")

        # Search fallback
        try:
            req = urllib.request.Request(
                f"https://api.dexscreener.com/latest/dex/search?q={addr}",
                headers={"User-Agent": "OracleOfBase/2.0"}
            )
            with urllib.request.urlopen(req, timeout=12) as r:
                data = json.loads(r.read().decode())
                pairs = [p for p in (data.get("pairs") or []) if p.get("chainId") == "base"]
                if pairs:
                    return sorted(pairs, key=lambda p: float(p.get("liquidity",{}).get("usd",0) or 0), reverse=True)[0]
        except Exception as e:
            log.debug(f"[SCARAB] DexScreener search: {e}")

        # GeckoTerminal fallback
        try:
            req = urllib.request.Request(
                f"https://api.geckoterminal.com/api/v2/networks/base/tokens/{addr.lower()}",
                headers={"User-Agent": "OracleOfBase/2.0", "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode())
                attrs = data.get("data", {}).get("attributes", {})
                if attrs:
                    return {
                        "chainId":       "base",
                        "baseToken":     {"address": addr, "symbol": attrs.get("symbol",""), "name": attrs.get("name","")},
                        "priceUsd":      attrs.get("price_usd"),
                        "liquidity":     {"usd": float(attrs.get("reserve_in_usd", 0) or 0)},
                        "fdv":           float(attrs.get("fdv_usd", 0) or 0),
                        "marketCap":     float(attrs.get("market_cap_usd", 0) or 0),
                        "volume":        {"h24": float((attrs.get("volume_usd") or {}).get("h24", 0) or 0)},
                        "priceChange":   {"h24": float((attrs.get("price_change_percentage") or {}).get("h24", 0) or 0)},
                        "pairCreatedAt": 0,
                        "txns":          {},
                        "_source":       "geckoterminal",
                    }
        except Exception as e:
            log.debug(f"[SCARAB] GeckoTerminal: {e}")

        return None

    def _extract_signals(self, pair: dict) -> dict:
        now_ms      = time.time() * 1000
        created     = pair.get("pairCreatedAt", now_ms) or now_ms
        age_hours   = (now_ms - created) / (1000 * 3600)
        age_days    = age_hours / 24

        tok         = pair.get("baseToken", {})
        vol         = pair.get("volume", {})
        pc          = pair.get("priceChange", {})
        txns        = pair.get("txns", {})
        liq         = float((pair.get("liquidity") or {}).get("usd", 0) or 0)
        fdv         = float(pair.get("fdv", 0) or 0)

        buys_24h    = int((txns.get("h24") or {}).get("buys",  0) or 0)
        sells_24h   = int((txns.get("h24") or {}).get("sells", 0) or 0)
        total_txns  = buys_24h + sells_24h
        vol_24h     = float((vol.get("h24") or 0))
        vol_1h      = float((vol.get("h1")  or 0))
        vol_6h      = float((vol.get("h6")  or 0))

        lifecycle = (
            "brand_new"   if age_hours < 6
            else "new"    if age_hours < 72
            else "growing" if age_days  < 30
            else "maturing" if age_days < 180
            else "established"
        )

        return {
            "token_address":     pair.get("baseToken", {}).get("address", ""),
            "symbol":            tok.get("symbol", ""),
            "name":              tok.get("name", ""),
            "dex":               (pair.get("dexId") or "").lower(),
            "pair_address":      pair.get("pairAddress", ""),
            "price_usd":         float(pair.get("priceUsd") or 0),
            "liquidity_usd":     liq,
            "fdv":               fdv,
            "market_cap":        float(pair.get("marketCap") or 0),
            "fdv_liquidity_ratio": round(fdv / liq, 2) if liq > 0 else 0,
            "vol_liquidity_ratio": round(vol_24h / liq, 3) if liq > 0 else 0,
            "volume_24h":        vol_24h,
            "volume_1h":         vol_1h,
            "volume_6h":         vol_6h,
            "volume_trend":      "accelerating" if vol_1h * 6 > vol_6h * 1.3 else
                                 "decelerating" if vol_1h * 6 < vol_6h * 0.5 else "stable",
            "buys_24h":          buys_24h,
            "sells_24h":         sells_24h,
            "total_txns_24h":    total_txns,
            "buy_ratio_24h":     round(buys_24h / max(total_txns, 1), 3),
            "buys_1h":           int((txns.get("h1") or {}).get("buys",  0) or 0),
            "sells_1h":          int((txns.get("h1") or {}).get("sells", 0) or 0),
            "buys_5m":           int((txns.get("m5") or {}).get("buys",  0) or 0),
            "sells_5m":          int((txns.get("m5") or {}).get("sells", 0) or 0),
            "price_change_5m":   float((pc.get("m5") or 0)),
            "price_change_1h":   float((pc.get("h1") or 0)),
            "price_change_6h":   float((pc.get("h6") or 0)),
            "price_change_24h":  float((pc.get("h24") or 0)),
            "age_hours":         round(age_hours, 1),
            "age_days":          round(age_days, 1),
            "is_new":            age_hours < 48,
            "lifecycle_stage":   lifecycle,
            "lp_count":          pair.get("liquidity", {}).get("uniswap", 0) or 0,
        }

    def _fetch_coingecko(self, addr: str) -> dict | None:
        try:
            r = requests.get(
                f"https://api.coingecko.com/api/v3/coins/base/contract/{addr}",
                timeout=8
            )
            if not r.ok:
                return None
            d   = r.json()
            dev = d.get("developer_data", {})
            com = d.get("community_data", {})
            desc = (d.get("description") or {}).get("en", "")
            return {
                "coingecko_id":      d.get("id"),
                "coingecko_name":    d.get("name"),
                "market_cap_rank":   d.get("market_cap_rank"),
                "developer_score":   d.get("developer_score"),
                "community_score":   d.get("community_score"),
                "twitter_followers": com.get("twitter_followers"),
                "github_commits_4w": dev.get("commit_count_4_weeks"),
                "github_stars":      dev.get("stars"),
                "description":       desc[:200] if desc else None,
            }
        except Exception as e:
            log.debug(f"[SCARAB] CoinGecko: {e}")
            return None