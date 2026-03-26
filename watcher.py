"""
watcher.py
-----------
Event-driven + scheduled token watcher for Base chain.

Discovery:  GeckoTerminal /networks/base/new_pools (sorted by age)
Enrichment: DexScreener   /latest/dex/tokens/{addr} (rich metrics)
Scoring:    Venice AI via prophecy_engine (token + deployer + social)
Storage:    Postgres via prediction_store

Two modes:
  1. WebSocket (real-time) — listens to Base chain PairCreated events
     Requires ALCHEMY_API_KEY env var
  2. Scheduled fallback — polls GeckoTerminal every WATCH_INTERVAL_SECONDS
     Works with no API key
"""

import os
import sys
import time
import json
import logging
import threading
import urllib.request
import requests
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from prophecy_engine  import FinancialProphet
from prediction_store import save_prediction, get_conn

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("watcher")

# ── Config ────────────────────────────────────────────────────────────────────
AGENT_ID      = os.getenv("AGENT_ID", "34499")
PRIVATE_KEY   = os.getenv("AGENT_PRIVATE_KEY")
RESOLVE_HOURS = int(os.getenv("RESOLVE_AFTER_HOURS", "24"))
WATCH_INTERVAL        = int(os.getenv("WATCH_INTERVAL_SECONDS",    "600"))
# Auto-watcher discovery filter — only watches NEW launches autonomously.
# The /prophecy endpoint scores ANY token regardless of age.
MAX_TOKEN_AGE_HOURS   = float(os.getenv("MAX_TOKEN_AGE_HOURS",     "48"))   # bumped: catch tokens up to 48h old
MIN_LIQUIDITY_USD     = float(os.getenv("MIN_LIQUIDITY_USD",        "1000"))
MAX_PREDICTIONS_CYCLE = int(os.getenv("MAX_PREDICTIONS_PER_CYCLE", "5"))
MAX_PREDICTIONS_HOUR  = int(os.getenv("MAX_PREDICTIONS_PER_HOUR",  "20"))
DEXSCREENER_DELAY     = int(os.getenv("DEXSCREENER_INDEX_DELAY",   "90"))

BASE_WSS = os.getenv(
    "BASE_WSS_URL",
    "wss://base-mainnet.g.alchemy.com/v2/" + os.getenv("ALCHEMY_API_KEY", "demo")
)

# ── Constants ─────────────────────────────────────────────────────────────────
GECKO_URL = "https://api.geckoterminal.com/api/v2"
GECKO_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept":     "application/json;version=20230302",
}
DEXSCREENER_HEADERS = {'User-Agent': 'Mozilla/5.0'}

IGNORE_ADDRESSES = {
    "0x4200000000000000000000000000000000000006",  # WETH
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC
    "0x50c5725949a6f0c72e6c4a641f24049a917db0cb",  # DAI
    "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca",  # USDbC
}

IGNORE_SYMBOLS = {'USD', 'USDC', 'USDT', 'WETH', 'WBTC', 'DAI', 'USDB'}

FACTORIES = {
    "uniswap_v2": {
        "address": "0x8909Dc15e40173Ff4699343b6eB8132c65e18eC",
        "topic":   "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9",
        "token_arg": 0,
    },
    "uniswap_v3": {
        "address": "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
        "topic":   "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118",
        "token_arg": 0,
    },
    "aerodrome": {
        "address": "0x420DD381b31aEf6683db6B902084cB0FFECe40Da",
        "topic":   "0x2128d88d14c80cb081c1252a5acff7a264671bf199ce226b53788fb26065005e",
        "token_arg": 0,
    },
}

# Rate limiting state
_prediction_times: list[float] = []
_prediction_lock  = threading.Lock()

# Oracle
oracle = FinancialProphet(AGENT_ID, PRIVATE_KEY)


# ── Helpers ───────────────────────────────────────────────────────────────────

def can_predict() -> bool:
    with _prediction_lock:
        now = time.time()
        global _prediction_times
        _prediction_times = [t for t in _prediction_times if now - t < 3600]
        return len(_prediction_times) < MAX_PREDICTIONS_HOUR


def record_prediction():
    with _prediction_lock:
        _prediction_times.append(time.time())


def already_predicted(token_address: str) -> bool:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM predictions WHERE subject = %s LIMIT 1",
                    (token_address.lower(),)
                )
                return cur.fetchone() is not None
    except Exception as e:
        log.error(f"DB check error: {e}")
        return False


def is_stablecoin(symbol: str) -> bool:
    return any(kw in symbol.upper() for kw in IGNORE_SYMBOLS)


def parse_age_hours(created_at: str) -> float:
    """Parse ISO timestamp and return age in hours."""
    try:
        dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except Exception:
        return 0.0


# ── GeckoTerminal — discovery ─────────────────────────────────────────────────

def fetch_gecko_new_pools(page: int = 1) -> list[dict]:
    """
    Fetch new Base pools from GeckoTerminal sorted by creation time.
    This is the primary discovery feed — purpose built for new launches.
    """
    try:
        url = (
            f"{GECKO_URL}/networks/base/new_pools"
            f"?include=base_token,quote_token&page={page}"
        )
        req = urllib.request.Request(url, headers=GECKO_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())

        pools    = data.get('data', [])
        included = {
            item['id']: item
            for item in data.get('included', [])
        }

        results = []
        for pool in pools:
            try:
                attrs = pool.get('attributes', {})
                rels  = pool.get('relationships', {})

                # Resolve base token
                base_ref  = rels.get('base_token', {}).get('data', {})
                base_id   = base_ref.get('id', '')
                base_info = included.get(base_id, {}).get('attributes', {})
                token_addr = base_info.get('address', '')

                if not token_addr:
                    continue

                results.append({
                    "token_address": token_addr.lower(),
                    "symbol":        base_info.get('symbol', ''),
                    "name":          base_info.get('name', ''),
                    "pool_address":  attrs.get('address', ''),
                    "price_usd":     float(attrs.get('base_token_price_usd') or 0),
                    "liquidity_usd": float(attrs.get('reserve_in_usd') or 0),
                    "volume_24h":    float(
                        (attrs.get('volume_usd') or {}).get('h24') or 0
                    ),
                    "created_at":    attrs.get('pool_created_at', ''),
                    "buys_24h":  int(
                        (attrs.get('transactions') or {})
                        .get('h24', {}).get('buys', 0) or 0
                    ),
                    "sells_24h": int(
                        (attrs.get('transactions') or {})
                        .get('h24', {}).get('sells', 0) or 0
                    ),
                })
            except Exception as e:
                log.debug(f"Pool parse error: {e}")

        log.info(f"GeckoTerminal page {page}: {len(results)} Base pools")
        return results

    except Exception as e:
        log.error(f"GeckoTerminal fetch error: {e}")
        return []


# ── DexScreener — enrichment ──────────────────────────────────────────────────

def resolve_dexscreener(token_address: str) -> dict | None:
    """
    Enrich a token with full DexScreener pair data.
    GeckoTerminal finds it; DexScreener gives Venice the behavioural signals.
    """
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        req = urllib.request.Request(url, headers=DEXSCREENER_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        base_pairs = [
            p for p in (data.get('pairs') or [])
            if p.get('chainId') == 'base'
        ]
        if not base_pairs:
            return None
        return sorted(
            base_pairs,
            key=lambda x: float(x.get('liquidity', {}).get('usd', 0) or 0),
            reverse=True
        )[0]
    except Exception as e:
        log.warning(f"DexScreener resolve error for {token_address[:10]}: {e}")
        return None


def dexscreener_fallback() -> list[dict]:
    """
    Fallback discovery via DexScreener token-profiles feed
    if GeckoTerminal is unavailable.
    """
    pairs = []
    try:
        url = "https://api.dexscreener.com/token-profiles/latest/v1"
        req = urllib.request.Request(url, headers=DEXSCREENER_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        entries = data if isinstance(data, list) else []
        base    = [
            e for e in entries
            if e.get('chainId') == 'base'
            and e.get('tokenAddress')
            and e.get('tokenAddress').lower() not in IGNORE_ADDRESSES
        ]
        log.info(f"DexScreener fallback: {len(base)} Base profiles")
        for e in base[:10]:
            pair = resolve_dexscreener(e['tokenAddress'])
            if pair:
                pairs.append(pair)
            time.sleep(0.3)
    except Exception as e:
        log.error(f"DexScreener fallback error: {e}")
    return pairs


# ── Main fetch pipeline ───────────────────────────────────────────────────────

def fetch_new_base_pairs() -> list[dict]:
    """
    Full discovery + enrichment pipeline.
    1. GeckoTerminal  → new Base pools sorted by creation time
    2. Filter         → age, liquidity, stablecoins, already predicted
    3. DexScreener    → enrich with full behavioural signals for Venice
    """
    # Pages 1 + 2 = ~40 pools
    gecko_pools = fetch_gecko_new_pools(1) + fetch_gecko_new_pools(2)

    if not gecko_pools:
        log.warning("GeckoTerminal empty — using DexScreener fallback")
        return dexscreener_fallback()

    # Filter candidates
    candidates = []
    for pool in gecko_pools:
        addr    = pool.get('token_address', '')
        symbol  = pool.get('symbol', '')
        liq     = pool.get('liquidity_usd', 0)
        created = pool.get('created_at', '')

        age_hours = parse_age_hours(created)

        if age_hours > MAX_TOKEN_AGE_HOURS:
            continue
        if liq < MIN_LIQUIDITY_USD:
            continue
        if is_stablecoin(symbol):
            continue
        if addr in IGNORE_ADDRESSES:
            continue
        if already_predicted(addr):
            log.debug(f"Already predicted {symbol} — skipping")
            continue

        candidates.append(pool)

    log.info(f"GeckoTerminal candidates after filter: {len(candidates)}")

    # Enrich with DexScreener
    pairs = []
    for pool in candidates[:MAX_PREDICTIONS_CYCLE]:
        pair = resolve_dexscreener(pool['token_address'])
        if pair:
            pairs.append(pair)
        time.sleep(0.3)

    log.info(f"Enriched {len(pairs)} pairs ready for prophecy")
    return pairs


# ── Auto prediction ───────────────────────────────────────────────────────────

def auto_predict(pair: dict) -> dict | None:
    """Run a full prophecy on a token and save it to the DB."""
    token_addr = pair.get('baseToken', {}).get('address', '')
    symbol     = pair.get('baseToken', {}).get('symbol', '')
    liquidity  = float(pair.get('liquidity', {}).get('usd', 0) or 0)

    if not token_addr:
        return None

    log.info(f"🔮 Auto-predicting {symbol} ({token_addr[:10]}...) | liq=${liquidity:,.0f}")

    try:
        fate    = oracle.consult_the_stars(token_addr)
        receipt = oracle.generate_attestation(token_addr, fate)

        # Extract clean verdict — strip Venice reason text
        # e.g. "CURSED (Ancient pair...)" → "CURSED"
        raw_verdict = fate.get('verdict', 'UNKNOWN')
        verdict     = raw_verdict.split(' ')[0].split('(')[0].strip()
        if verdict not in ('BLESSED', 'MORTAL', 'CURSED'):
            verdict = 'UNKNOWN' 

        prediction_id = save_prediction(
            agent_id            = AGENT_ID,
            prediction_type     = "token",
            subject             = token_addr.lower(),
            verdict             = verdict,
            score               = fate.get('score', 0) // 100,
            raw_data            = fate,
            attestation_uid     = receipt.get('uid', ''),
            resolve_after_hours = RESOLVE_HOURS,
        )
        record_prediction()

        score = fate.get('score', 0) // 100
        log.info(
            f"✅ {symbol} | id={prediction_id[:8]} | verdict={verdict} | "
            f"score={score} | "
            f"token={fate.get('token_score')} "
            f"deployer={fate.get('deployer_score')} "
            f"promoter={fate.get('promoter_score')}"
        )

        # ── Emotion read ────────────────────────────────────────────────────
        emotion_read = {}
        try:
            from emotion_engine import get_emotion_engine
            ee = get_emotion_engine()
            emotion_read = ee.read(
                token_address    = token_addr,
                pair_data        = pair,
                token_signals    = fate.get("details", {}).get("token_signals", {}),
                deployer_signals = fate.get("details", {}).get("deployer_signals", {}),
                promoter_signals = fate.get("details", {}).get("promoter_signals", {}),
            )
            log.info(
                f"🎭 Emotion | {symbol} | "
                f"emotion={emotion_read.get('market_emotion')} | "
                f"intention={emotion_read.get('deployer_intention')} | "
                f"intuition={emotion_read.get('trade_intuition')} | "
                f"conviction={emotion_read.get('conviction')}"
            )
        except Exception as e:
            log.debug(f"Emotion read skipped: {e}")

        # ── Post to Moltbook autonomously ─────────────────────────────────
        try:
            from moltbook_client import post_prediction as mb_post
            mb_post(
                symbol         = symbol,
                verdict        = verdict,
                score          = score,
                token_address  = token_addr,
                reason         = fate.get('details', {}).get('venice_reason', ''),
                token_score    = fate.get('token_score', 0) or 0,
                deployer_score = fate.get('deployer_score', 0) or 0,
                promoter_score = fate.get('promoter_score', 0) or 0,
                liquidity_usd  = float(pair.get('liquidity', {}).get('usd', 0) or 0),
            )
        except Exception as e:
            log.warning(f"Moltbook post skipped: {e}")

        # ── Consider opening a fund position ──────────────────────────────
        try:
            from fund_manager import get_fund_manager
            fm = get_fund_manager()
            entry = fm.consider_entry(
                token_address = token_addr,
                oracle_score  = score,
                verdict       = verdict,
                symbol        = symbol,
            )
            if entry.get("action") == "bought":
                log.info(
                    f"💰 Fund entry | {symbol} | ${entry.get('usdc_spent')} USDC | "
                    f"score={score} | tx={str(entry.get('tx',''))[:16]}..."
                )
            elif entry.get("action") not in ("skip", "skipped"):
                log.warning(f"Fund entry failed | {symbol} | {entry}")
        except Exception as e:
            log.warning(f"Fund entry check skipped: {e}")

        # ── Record prediction cost + Sapience forecast search ─────────────
        try:
            from edge_engine import get_edge_engine
            from sapience_trader import get_sapience_trader
            get_edge_engine().record_prediction_made()
            st = get_sapience_trader()
            if st.enabled:
                st.process_prophecy(
                    prophecy      = fate,
                    token_address = token_addr,
                    symbol        = symbol,
                    bankroll      = 10.0,
                    emotion_read  = emotion_read,
                )
        except Exception as e:
            log.debug(f"Edge/Sapience processing skipped: {e}")

        return {
            "prediction_id":  prediction_id,
            "token_address":  token_addr,
            "symbol":         symbol,
            "verdict":        verdict,
            "score":          score,
            "token_score":    fate.get('token_score'),
            "deployer_score": fate.get('deployer_score'),
            "promoter_score": fate.get('promoter_score'),
        }
    except Exception as e:
        log.error(f"Auto-predict failed for {symbol}: {e}", exc_info=True)
        return None


# ── Scheduled scan ────────────────────────────────────────────────────────────

def run_fallback_scan() -> list[dict]:
    """
    Scheduled scan — called by the background thread and /watch endpoint.
    Uses GeckoTerminal for discovery + DexScreener for enrichment.
    """
    predictions = []
    try:
        pairs = fetch_new_base_pairs()
        log.info(f"Scan found {len(pairs)} enriched pairs to predict")

        for pair in pairs:
            if not can_predict():
                log.warning("Hourly rate limit reached — stopping cycle")
                break
            result = auto_predict(pair)
            if result:
                predictions.append(result)
            time.sleep(3)

    except Exception as e:
        log.error(f"Fallback scan error: {e}", exc_info=True)

    return predictions


# ── WebSocket listener ────────────────────────────────────────────────────────

def decode_token_address(log_data: dict, token_arg: int) -> str | None:
    try:
        topics = log_data.get('topics', [])
        idx    = token_arg + 1
        if len(topics) > idx:
            return ('0x' + topics[idx][-40:]).lower()
        return None
    except Exception:
        return None


def handle_new_pair_event(event: dict, dex_name: str, token_arg: int):
    """Handle a blockchain PairCreated event in a background thread."""
    try:
        token_address = decode_token_address(event, token_arg)
        if not token_address:
            return
        if token_address in IGNORE_ADDRESSES:
            other = decode_token_address(event, 1 if token_arg == 0 else 0)
            if not other or other in IGNORE_ADDRESSES:
                return
            token_address = other

        log.info(f"📡 {dex_name} PairCreated | token={token_address[:10]}...")

        if not can_predict() or already_predicted(token_address):
            return

        log.info(f"Waiting {DEXSCREENER_DELAY}s for DexScreener to index...")
        time.sleep(DEXSCREENER_DELAY)

        pair = resolve_dexscreener(token_address)
        if not pair:
            return

        liquidity = float(pair.get('liquidity', {}).get('usd', 0) or 0)
        if liquidity < MIN_LIQUIDITY_USD:
            return

        auto_predict(pair)

    except Exception as e:
        log.error(f"handle_new_pair_event error: {e}", exc_info=True)


def listen_for_pairs():
    """WebSocket listener for real-time Base chain events."""
    try:
        import websocket
    except ImportError:
        log.error("websocket-client not installed — run: pip install websocket-client")
        return

    topic_to_factory  = {f['topic']: (name, f) for name, f in FACTORIES.items()}
    factory_addresses = [f['address'].lower() for f in FACTORIES.values()]
    factory_topics    = [f['topic'] for f in FACTORIES.values()]

    subscribe_msg = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "method":  "eth_subscribe",
        "params":  ["logs", {
            "address": factory_addresses,
            "topics":  [factory_topics],
        }]
    })

    def on_message(ws, message):
        try:
            data   = json.loads(message)
            result = data.get('params', {}).get('result', {})
            topics = result.get('topics', [])
            if not topics:
                return
            for topic_hash, (name, factory) in topic_to_factory.items():
                if topics[0].lower() == topic_hash.lower():
                    threading.Thread(
                        target=handle_new_pair_event,
                        args=(result, name, factory['token_arg']),
                        daemon=True,
                    ).start()
                    break
        except Exception as e:
            log.error(f"WebSocket message error: {e}")

    def on_error(ws, error): log.error(f"WebSocket error: {error}")
    def on_close(ws, *_):    log.warning("WebSocket closed")
    def on_open(ws):
        ws.send(subscribe_msg)
        log.info("WebSocket subscribed to Base PairCreated events ✅")

    while True:
        try:
            log.info(f"Connecting to Base WebSocket...")
            websocket.WebSocketApp(
                BASE_WSS,
                on_open=on_open, on_message=on_message,
                on_error=on_error, on_close=on_close,
            ).run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            log.error(f"WebSocket error: {e}")
        log.info("Reconnecting in 15s...")
        time.sleep(15)


# ── Public API ────────────────────────────────────────────────────────────────

def run_watch_cycle() -> list[dict]:
    """
    Public entry point called by app.py /watch endpoint and background thread.
    Always runs the GeckoTerminal scan directly.
    """
    return run_fallback_scan()


def run_forever():
    """
    Start the watcher. WebSocket if Alchemy key set, scheduled scan otherwise.
    """
    alchemy_key = os.getenv("ALCHEMY_API_KEY")
    if alchemy_key and alchemy_key != "demo":
        log.info("Alchemy key found — starting WebSocket listener (real-time)")
        listen_for_pairs()
    else:
        log.info(
            "No ALCHEMY_API_KEY — using scheduled GeckoTerminal scan "
            f"every {WATCH_INTERVAL}s. Add ALCHEMY_API_KEY for real-time events."
        )
        while True:
            try:
                run_fallback_scan()
            except Exception as e:
                log.error(f"Scan error: {e}")
            time.sleep(WATCH_INTERVAL)


if __name__ == "__main__":
    run_forever()