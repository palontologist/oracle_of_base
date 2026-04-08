"""
agents/seer.py — SEER (Social Analyst)
---------------------------------------
Reads social signals around a token — Farcaster mentions, promoter credibility,
bot patterns, community health. Reports sentiment texture, not just counts.
Reports to: PROPHET orchestrator.
"""

import os, logging, requests
log = logging.getLogger("seer")

NEYNAR_API_KEY = os.getenv("NEYNAR_API_KEY", "NEYNAR_API_DOCS")

class Seer:
    """Social signal collector and bot-detection agent."""

    AGENT_ID   = "SEER"
    ROLE       = "social_analyst"
    REPORTS_TO = "PROPHET"

    def fetch(self, symbol: str) -> dict:
        log.info(f"[SEER] Reading social signals for ${symbol}...")
        casts    = self._fetch_farcaster(symbol)
        profiles = self._build_profiles(casts)
        signals  = self._analyse_profiles(profiles)
        signals["agent"]  = self.AGENT_ID
        signals["symbol"] = symbol
        log.info(f"[SEER] ${symbol} | {signals.get('cast_count',0)} casts | bots={signals.get('bot_promoters',0)} | trusted={signals.get('trusted_promoters',0)}")
        return signals

    def _fetch_farcaster(self, symbol: str) -> list:
        try:
            r = requests.get(
                "https://api.neynar.com/v2/farcaster/cast/search",
                params={"q": f"${symbol}", "limit": 20},
                headers={"api_key": NEYNAR_API_KEY, "accept": "application/json"},
                timeout=8
            )
            if r.ok:
                return r.json().get("result", {}).get("casts", [])
        except Exception as e:
            log.debug(f"[SEER] Farcaster search: {e}")
        return []

    def _build_profiles(self, casts: list) -> list:
        profiles = []
        for cast in casts[:20]:
            author = cast.get("author") or cast.get("cast", {}).get("author", {})
            if not author:
                continue
            followers = int(author.get("follower_count", 0) or 0)
            following = int(author.get("following_count", 0) or 0)
            profiles.append({
                "username":    author.get("username", ""),
                "followers":   followers,
                "following":   following,
                "follow_ratio": round(following / max(followers, 1), 2),
                "bio_snippet": (author.get("profile", {}).get("bio", {}).get("text", "") or "")[:80],
                "cast_text":   cast.get("text", cast.get("cast", {}).get("text", ""))[:120],
                "power_badge": author.get("power_badge", False),
            })
        return profiles

    def _analyse_profiles(self, profiles: list) -> dict:
        if not profiles:
            return {
                "social_presence": "not_found",
                "cast_count":      0,
                "bot_promoters":   0,
                "trusted_promoters": 0,
                "promoter_profiles": [],
                "note": "No social mentions found",
            }

        bots    = sum(1 for p in profiles if p["follow_ratio"] > 5 and p["followers"] < 50)
        trusted = sum(1 for p in profiles if p["followers"] > 1000 and p["follow_ratio"] < 2)
        power   = sum(1 for p in profiles if p.get("power_badge"))

        # Sentiment texture
        full_text = " ".join(p["cast_text"].lower() for p in profiles)
        fear_hits  = sum(1 for w in ["rug","scam","dump","exit","warning"] if w in full_text)
        hype_hits  = sum(1 for w in ["moon","gem","alpha","early","100x"] if w in full_text)
        shill_hits = sum(1 for w in ["buy now","don't miss","guaranteed","easy money"] if w in full_text)

        tone = (
            "coordinated_shill" if shill_hits > 2 and bots > 3
            else "organic_excitement" if hype_hits > fear_hits and bots < 3
            else "community_fear"     if fear_hits > hype_hits
            else "mixed"
        )

        return {
            "social_presence":   "found",
            "cast_count":        len(profiles),
            "bot_promoters":     bots,
            "trusted_promoters": trusted,
            "power_badge_holders": power,
            "sentiment_tone":    tone,
            "fear_signals":      fear_hits,
            "hype_signals":      hype_hits,
            "shill_signals":     shill_hits,
            "promoter_profiles": profiles,
            "note": f"Venice should assess: are these real community members or coordinated shills?",
        }