"""
Event-driven token watcher. Listens to Base chain for PairCreated
events via WebSocket, then pulls DexScreener data and auto-predicts.
No polling — the blockchain tells us the moment a new token launches.
Supported DEXes on Base:
  - Uniswap V2 (PairCreated)
  - Uniswap V3 (PoolCreated)
  - Aerodrome (PoolCreated) — dominant DEX on Base

Flow:
  blockchain event → extract token address → wait for DexScreener to index
  → run full prophecy → save prediction → resolution engine handles the rest
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

from prophecy_engine import FinancialProphet
from prediction_store import save_prediction, get_conn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("watcher")

# ── Config ────────────────────────────────────────────────────────────────────
AGENT_ID = os.getenv("AGENT_ID", "34499")
PRIVATE_KEY = os.getenv("AGENT_PRIVATE_KEY")
RESOLVE_HOURS = int(os.getenv("RESOLVE_AFTER_HOURS", "24"))

# Base WebSocket RPC — Alchemy or QuickNode recommended for reliability
BASE_WSS = os.getenv(
    "BASE_WSS_URL",
    "wss://base-mainnet.g.alchemy.com/v2/" + os.getenv("ALCHEMY_API_KEY", "demo")
)

DEXSCREENER_INDEX_DELAY = int(os.getenv("DEXSCREENER_INDEX_DELAY", "90"))
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", "1000"))
MAX_PREDICTIONS_PER_HOUR = int(os.getenv("MAX_PREDICTIONS_PER_HOUR", "20"))

# ── Base DEX Factory Contracts ────────────────────────────────────────────────
FACTORIES = {
    "uniswap_v2": {
        "address": "0x8909Dc15e40173Ff4699343b6eB8132c65e18eC",
        "event": "PairCreated(address,address,address,uint256)",
        "topic": "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9",
        "token_arg": 0,
    },
    "uniswap_v3": {
        "address": "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
        "event": "PoolCreated(address,address,uint24,int24,address)",
        "topic": "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118",
        "token_arg": 0,
    },
    "aerodrome": {
        "address": "0x420DD381b31aEf6683db6B902084cB0FFECe40Da",
        "event": "PoolCreated(address,address,bool,address,uint256)",
        "topic": "0x2128d88d14c80cb081c1252a5acff7a264671bf199ce226b53788fb26065005e",
        "token_arg": 0,
    },
}

IGNORE_ADDRESSES = {
    "0x4200000000000000000000000000000000000006",  # WETH
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC
    "0x50c5725949a6f0c72e6c4a641f24049a917db0cb",  # DAI
    "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca",  # USDbC
}

_prediction_times: list[float] = []
_prediction_lock = threading.Lock()

oracle = FinancialProphet(AGENT_ID, PRIVATE_KEY)

# ── Rate limiting ─────────────────────────────────────────────────────────────
def can_predict() -> bool:
    with _prediction_lock:
        now = time.time()
        global _prediction_times
        _prediction_times = [t for t in _prediction_times if now - t < 3600]
        return len(_prediction_times) < MAX_PREDICTIONS_PER_HOUR

def record_prediction():
    with _prediction_lock:
        _prediction_times.append(time.time())

# ── Already-predicted check ───────────────────────────────────────────────────
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

# ── DexScreener helpers ───────────────────────────────────────────────────────
def wait_for_dexscreener(token_address: str, max_attempts: int = 6) -> dict | None:
    for attempt in range(max_attempts):
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode())
                pairs = [p for p in (data.get('pairs') or []) if p.get('chainId') == 'base']
                if pairs:
                    log.info(f"DexScreener indexed {token_address[:10]}... on attempt {attempt + 1}")
                    return sorted(
                        pairs,
                        key=lambda x: float(x.get('liquidity', {}).get('usd', 0) or 0),
                        reverse=True
                    )[0]
        except Exception as e:
            log.warning(f"DexScreener attempt {attempt + 1} failed: {e}")
        wait = 30 * (attempt + 1)
        log.info(f"Waiting {wait}s for DexScreener to index...")
        time.sleep(wait)
    log.warning(f"DexScreener never indexed {token_address[:10]}... — skipping")
    return None

# ── NEW: Fallback data source ─────────────────────────────────────────────────
def fetch_new_base_pairs() -> list[dict]:
    """
    Fetch recently created / active pairs on Base from DexScreener public endpoint.
    Used as fallback when WebSocket event listening is not available.
    """
    try:
        url = "https://api.dexscreener.com/latest/dex/pairs/base"
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; BaseTokenWatcher/1.0)'}
        req = urllib.request.Request(url, headers=headers)

        with urllib.request.urlopen(req, timeout=12) as response:
            data = json.loads(response.read().decode())
            pairs = data.get('pairs', [])
            
        if not pairs:
            log.warning("DexScreener returned no pairs")
            return []

        # Sort by creation time (newest first) if available
        pairs.sort(key=lambda p: p.get('pairCreatedAt', 0), reverse=True)
        
        # Limit to most recent ones to avoid processing very old pairs
        recent_pairs = pairs[:80]  # usually plenty — DexScreener returns ~100-300
        
        log.info(f"Fetched {len(recent_pairs)} recent Base pairs from DexScreener")
        return recent_pairs

    except urllib.error.HTTPError as http_err:
        log.error(f"DexScreener HTTP error {http_err.code}: {http_err.reason}")
    except Exception as e:
        log.error(f"fetch_new_base_pairs failed: {type(e).__name__} {e}", exc_info=True)
    
    return []

# ── Event processing ──────────────────────────────────────────────────────────
def decode_token_address(log_data: dict, token_arg: int) -> str | None:
    try:
        topics = log_data.get('topics', [])
        idx = token_arg + 1
        if len(topics) > idx:
            raw = topics[idx]
            addr = '0x' + raw[-40:]
            return addr.lower()
        return None
    except Exception:
        return None

def handle_new_pair_event(event: dict, dex_name: str, token_arg: int):
    try:
        token_address = decode_token_address(event, token_arg)
        if not token_address:
            return

        if token_address in IGNORE_ADDRESSES:
            other_arg = 1 if token_arg == 0 else 0
            token_address = decode_token_address(event, other_arg)
            if not token_address or token_address in IGNORE_ADDRESSES:
                return

        log.info(f"🆕 New pair detected on {dex_name} | token={token_address[:10]}...")

        if not can_predict():
            log.warning(f"Rate limit reached — skipping {token_address[:10]}...")
            return

        if already_predicted(token_address):
            log.debug(f"Already predicted {token_address[:10]}... — skipping")
            return

        log.info(f"Waiting {DEXSCREENER_INDEX_DELAY}s for DexScreener to index...")
        time.sleep(DEXSCREENER_INDEX_DELAY)

        pair_data = wait_for_dexscreener(token_address)
        if not pair_data:
            return

        liquidity = float(pair_data.get('liquidity', {}).get('usd', 0) or 0)
        if liquidity < MIN_LIQUIDITY_USD:
            log.info(f"Liquidity ${liquidity:,.0f} below threshold — skipping")
            return

        symbol = pair_data.get('baseToken', {}).get('symbol', token_address[:8])
        log.info(f"🔮 Running prophecy for {symbol} | liquidity=${liquidity:,.0f}")

        fate = oracle.consult_the_stars(token_address)
        receipt = oracle.generate_attestation(token_address, fate)
        raw_verdict = fate.get('verdict', 'UNKNOWN')
        verdict = raw_verdict.split(' ')[0] if ' ' in raw_verdict else raw_verdict

        prediction_id = save_prediction(
            agent_id=AGENT_ID,
            prediction_type="token",
            subject=token_address.lower(),
            verdict=verdict,
            score=fate.get('score', 0) // 100,
            raw_data=fate,
            attestation_uid=receipt.get('uid', ''),
            resolve_after_hours=RESOLVE_HOURS,
        )
        record_prediction()

        log.info(
            f"✅ AUTO-PREDICTED {symbol} | "
            f"id={prediction_id[:8]} | verdict={verdict} | "
            f"score={fate.get('score', 0) // 100}"
        )

    except Exception as e:
        log.error(f"handle_new_pair_event error: {e}", exc_info=True)

# ── WebSocket listener ────────────────────────────────────────────────────────
def listen_for_pairs():
    try:
        import websocket
    except ImportError:
        log.error("websocket-client not installed. Run: pip install websocket-client")
        return

    factory_addresses = [f['address'].lower() for f in FACTORIES.values()]
    factory_topics = [f['topic'] for f in FACTORIES.values()]
    topic_to_factory = {f['topic']: (name, f) for name, f in FACTORIES.items()}

    subscribe_msg = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_subscribe",
        "params": [
            "logs",
            {
                "address": factory_addresses,
                "topics": [factory_topics],
            }
        ]
    })

    def on_message(ws, message):
        try:
            data = json.loads(message)
            result = data.get('params', {}).get('result', {})
            if not result:
                return
            topics = result.get('topics', [])
            if not topics:
                return
            event_topic = topics[0].lower()
            matched = None
            for topic_hash, (name, factory) in topic_to_factory.items():
                if event_topic == topic_hash.lower():
                    matched = (name, factory)
                    break
            if not matched:
                return
            dex_name, factory = matched
            log.info(f"📡 Event received from {dex_name} factory")
            t = threading.Thread(
                target=handle_new_pair_event,
                args=(result, dex_name, factory['token_arg']),
                daemon=True,
            )
            t.start()
        except Exception as e:
            log.error(f"WebSocket message error: {e}")

    def on_error(ws, error):
        log.error(f"WebSocket error: {error}")

    def on_close(ws, close_status, close_msg):
        log.warning(f"WebSocket closed: {close_status} {close_msg}")

    def on_open(ws):
        log.info(f"WebSocket connected — subscribing to {len(FACTORIES)} factories...")
        ws.send(subscribe_msg)
        log.info("Subscribed to PairCreated/PoolCreated events")

    while True:
        try:
            log.info(f"Connecting to Base WebSocket: {BASE_WSS[:50]}...")
            ws = websocket.WebSocketApp(
                BASE_WSS,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            log.error(f"WebSocket connection failed: {e}")
        log.info("Reconnecting in 15s...")
        time.sleep(15)

# ── Fallback: periodic DexScreener scan ──────────────────────────────────────
def run_fallback_scan() -> list[dict]:
    predictions = []
    try:
        pairs = fetch_new_base_pairs()
        now_ms = time.time() * 1000

        candidates = [
            p for p in pairs
            if (
                float(p.get('liquidity', {}).get('usd', 0) or 0) >= MIN_LIQUIDITY_USD and
                not any(kw in p.get('baseToken', {}).get('symbol', '').upper()
                        for kw in ['USD', 'USDC', 'USDT', 'WETH', 'WBTC', 'DAI']) and
                not already_predicted(p.get('baseToken', {}).get('address', ''))
            )
        ]

        log.info(f"Fallback scan: {len(candidates)} new candidates from {len(pairs)} pairs")

        for pair in candidates[:5]:  # limit burst size
            if not can_predict():
                break
            token_addr = pair.get('baseToken', {}).get('address', '')
            if not token_addr:
                continue

            fate = oracle.consult_the_stars(token_addr)
            receipt = oracle.generate_attestation(token_addr, fate)
            raw_verdict = fate.get('verdict', 'UNKNOWN')
            verdict = raw_verdict.split(' ')[0] if ' ' in raw_verdict else raw_verdict

            pid = save_prediction(
                agent_id=AGENT_ID,
                prediction_type="token",
                subject=token_addr.lower(),
                verdict=verdict,
                score=fate.get('score', 0) // 100,
                raw_data=fate,
                attestation_uid=receipt.get('uid', ''),
                resolve_after_hours=RESOLVE_HOURS,
            )
            record_prediction()
            predictions.append({
                "prediction_id": pid,
                "symbol": pair['baseToken'].get('symbol', '???'),
                "verdict": verdict
            })
            time.sleep(3)  # gentle rate limit

    except Exception as e:
        log.error(f"Fallback scan error: {e}", exc_info=True)

    return predictions

# ── Public API ────────────────────────────────────────────────────────────────
def run_watch_cycle() -> list[dict]:
    return run_fallback_scan()

# ── Entry point ───────────────────────────────────────────────────────────────
def run_forever():
    alchemy_key = os.getenv("ALCHEMY_API_KEY")
    if alchemy_key and alchemy_key != "demo":
        log.info("Alchemy key found → using real-time WebSocket listener")
        listen_for_pairs()   # blocks
    else:
        log.warning("No ALCHEMY_API_KEY → falling back to periodic DexScreener scan")
        interval = int(os.getenv("WATCH_INTERVAL_SECONDS", "600"))
        while True:
            try:
                run_fallback_scan()
            except Exception as e:
                log.error(f"Main loop error: {e}")
            time.sleep(interval)

if __name__ == "__main__":
    run_forever()