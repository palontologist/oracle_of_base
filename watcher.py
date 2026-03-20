"""
watcher.py
-----------
Event-driven token watcher. Listens to Base chain for PairCreated
events via WebSocket, then pulls DexScreener data and auto-predicts.

No polling — the blockchain tells us the moment a new token launches.

Supported DEXes on Base:
  - Uniswap V2  (PairCreated)
  - Uniswap V3  (PoolCreated)
  - Aerodrome   (PoolCreated) — dominant DEX on Base

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

from prophecy_engine  import FinancialProphet
# FIX: Import engine instead of get_conn for SQLAlchemy
from prediction_store import save_prediction, engine
from sqlalchemy import text

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("watcher")

# ── Config ────────────────────────────────────────────────────────────────────
AGENT_ID      = os.getenv("AGENT_ID", "34499")
PRIVATE_KEY   = os.getenv("AGENT_PRIVATE_KEY")
RESOLVE_HOURS = int(os.getenv("RESOLVE_AFTER_HOURS", "24"))

# Base WebSocket RPC — Alchemy or QuickNode recommended for reliability
# Free public wss also works but may be rate limited
BASE_WSS = os.getenv(
    "BASE_WSS_URL",
    "wss://base-mainnet.g.alchemy.com/v2/" + os.getenv("ALCHEMY_API_KEY", "demo")
)

# How long to wait after PairCreated before pulling DexScreener
# DexScreener needs ~60s to index a new pair
DEXSCREENER_INDEX_DELAY = int(os.getenv("DEXSCREENER_INDEX_DELAY", "90"))

# Minimum liquidity before predicting
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", "1000"))

# Max predictions per hour (protect Venice rate limits)
MAX_PREDICTIONS_PER_HOUR = int(os.getenv("MAX_PREDICTIONS_PER_HOUR", "20"))

# ── Base DEX Factory Contracts ────────────────────────────────────────────────
# These emit events the moment a new token pair is created

FACTORIES = {
    # Uniswap V2 on Base
    "uniswap_v2": {
        "address":   "0x8909Dc15e40173Ff4699343b6eB8132c65e18eC",
        "event":     "PairCreated(address,address,address,uint256)",
        "topic":     "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9",
        "token_arg": 0,  # first arg is token0
    },
    # Uniswap V3 on Base
    "uniswap_v3": {
        "address":   "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
        "event":     "PoolCreated(address,address,uint24,int24,address)",
        "topic":     "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118",
        "token_arg": 0,
    },
    # Aerodrome (dominant DEX on Base)
    "aerodrome": {
        "address":   "0x420DD381b31aEf6683db6B902084cB0FFECe40Da",
        "event":     "PoolCreated(address,address,bool,address,uint256)",
        "topic":     "0x2128d88d14c80cb081c1252a5acff7a264671bf199ce226b53788fb26065005e",
        "token_arg": 0,
    },
}

# Tokens to ignore (WETH, stablecoins — they're always one side of a pair)
IGNORE_ADDRESSES = {
    "0x4200000000000000000000000000000000000006",  # WETH on Base
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC on Base
    "0x50c5725949a6f0c72e6c4a641f24049a917db0cb",  # DAI on Base
    "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca",  # USDbC on Base
}

# Track predictions per hour for rate limiting
_prediction_times: list[float] = []
_prediction_lock = threading.Lock()

# Oracle instance
oracle = FinancialProphet(AGENT_ID, PRIVATE_KEY)


# ── Rate limiting ─────────────────────────────────────────────────────────────

def can_predict() -> bool:
    """Check if we're under the per-hour prediction limit."""
    with _prediction_lock:
        now = time.time()
        # Remove predictions older than 1 hour
        global _prediction_times
        _prediction_times = [t for t in _prediction_times if now - t < 3600]
        return len(_prediction_times) < MAX_PREDICTIONS_PER_HOUR


def record_prediction():
    with _prediction_lock:
        _prediction_times.append(time.time())


# ── Already-predicted check ───────────────────────────────────────────────────

def already_predicted(token_address: str) -> bool:
    try:
        # FIX: Use SQLAlchemy engine connection
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT 1 FROM predictions WHERE subject = :subject LIMIT 1"),
                {"subject": token_address.lower()}
            ).fetchone()
            return result is not None
    except Exception as e:
        log.error(f"DB check error: {e}")
        return False


# ── DexScreener with retry ────────────────────────────────────────────────────

def wait_for_dexscreener(token_address: str, max_attempts: int = 6) -> dict | None:
    """
    Wait for DexScreener to index the new token then return pair data.
    Retries with backoff — DexScreener takes 60-120s to index new pairs.
    """
    for attempt in range(max_attempts):
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as r:
                data  = json.loads(r.read().decode())
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

        wait = 30 * (attempt + 1)  # 30s, 60s, 90s, 120s, 150s, 180s
        log.info(f"Waiting {wait}s for DexScreener to index...")
        time.sleep(wait)

    log.warning(f"DexScreener never indexed {token_address[:10]}... — skipping")
    return None


# ── Event processing ──────────────────────────────────────────────────────────

def decode_token_address(log_data: dict, token_arg: int) -> str | None:
    """
    Decode a token address from a PairCreated/PoolCreated event log.
    Topics[1] = token0, topics[2] = token1 for most factory events.
    """
    try:
        topics = log_data.get('topics', [])
        # token0 is topics[1], token1 is topics[2]
        idx    = token_arg + 1
        if len(topics) > idx:
            raw   = topics[idx]
            # Pad/strip to get the address (last 20 bytes of 32-byte topic)
            addr  = '0x' + raw[-40:]
            return addr.lower()
        return None
    except Exception:
        return None


def handle_new_pair_event(event: dict, dex_name: str, token_arg: int):
    """
    Called when a PairCreated/PoolCreated event is detected.
    Runs in its own thread so it doesn't block the WebSocket listener.
    """
    try:
        token_address = decode_token_address(event, token_arg)
        if not token_address:
            return

        # Skip known tokens (WETH, stablecoins)
        if token_address in IGNORE_ADDRESSES:
            # Try the other token in the pair
            other_arg     = 1 if token_arg == 0 else 0
            token_address = decode_token_address(event, other_arg)
            if not token_address or token_address in IGNORE_ADDRESSES:
                return

        log.info(f"🆕 New pair detected on {dex_name} | token={token_address[:10]}...")

        # Check rate limit
        if not can_predict():
            log.warning(f"Rate limit reached ({MAX_PREDICTIONS_PER_HOUR}/hr) — queuing {token_address[:10]}...")
            return

        # Skip if already predicted
        if already_predicted(token_address):
            log.debug(f"Already predicted {token_address[:10]}... — skipping")
            return

        # Wait for DexScreener to index the token
        log.info(f"Waiting {DEXSCREENER_INDEX_DELAY}s for DexScreener to index...")
        time.sleep(DEXSCREENER_INDEX_DELAY)

        # Pull from DexScreener with retry
        pair_data = wait_for_dexscreener(token_address)
        if not pair_data:
            return

        # Check liquidity threshold
        liquidity = float(pair_data.get('liquidity', {}).get('usd', 0) or 0)
        if liquidity < MIN_LIQUIDITY_USD:
            log.info(f"Liquidity ${liquidity:,.0f} below threshold — skipping {token_address[:10]}...")
            return

        symbol = pair_data.get('baseToken', {}).get('symbol', token_address[:8])
        log.info(f"🔮 Running prophecy for {symbol} | liquidity=${liquidity:,.0f} | dex={dex_name}")

        # Full prophecy
        fate    = oracle.consult_the_stars(token_address)
        receipt = oracle.generate_attestation(token_address, fate)

        raw_verdict = fate.get('verdict', 'UNKNOWN')
        verdict     = raw_verdict.split(' ')[0] if ' ' in raw_verdict else raw_verdict

        prediction_id = save_prediction(
            agent_id            = AGENT_ID,
            prediction_type     = "token",
            subject             = token_address.lower(),
            verdict             = verdict,
            score               = fate.get('score', 0),
            raw_data            = fate,
            attestation_uid     = receipt.get('uid', ''),
            resolve_after_hours = RESOLVE_HOURS,
        )

        record_prediction()

        log.info(
            f"✅ AUTO-PREDICTED {symbol} | "
            f"id={prediction_id[:8]} | "
            f"verdict={verdict} | "
            f"score={fate.get('score', 0)} | "
            f"dex={dex_name}"
        )

    except Exception as e:
        log.error(f"handle_new_pair_event error: {e}", exc_info=True)


# ── WebSocket listener ────────────────────────────────────────────────────────

def listen_for_pairs():
    """
    Connect to Base via WebSocket and subscribe to PairCreated/PoolCreated
    events from all supported DEX factory contracts.

    Uses eth_subscribe with log filters — the chain pushes events to us
    the moment they're confirmed. No polling needed.
    """
    try:
        import websocket
    except ImportError:
        log.error("websocket-client not installed. Run: pip install websocket-client")
        return

    factory_addresses = [f['address'].lower() for f in FACTORIES.values()]
    factory_topics    = [f['topic'] for f in FACTORIES.values()]

    # Map topic hash → factory config for fast lookup
    topic_to_factory  = {f['topic']: (name, f) for name, f in FACTORIES.items()}

    subscribe_msg = json.dumps({
        "jsonrpc": "2.0",
        "id":      1,
        "method":  "eth_subscribe",
        "params":  [
            "logs",
            {
                "address": factory_addresses,
                "topics":  [factory_topics],  # OR filter — any of these topics
            }
        ]
    })

    def on_message(ws, message):
        try:
            data   = json.loads(message)
            result = data.get('params', {}).get('result', {})
            if not result:
                return

            topics    = result.get('topics', [])
            if not topics:
                return

            event_topic = topics[0].lower()

            # Find which factory this event came from
            matched = None
            for topic_hash, (name, factory) in topic_to_factory.items():
                if event_topic == topic_hash.lower():
                    matched = (name, factory)
                    break

            if not matched:
                return

            dex_name, factory = matched
            log.info(f"📡 Event received from {dex_name} factory")

            # Handle in background thread so listener stays unblocked
            t = threading.Thread(
                target = handle_new_pair_event,
                args   = (result, dex_name, factory['token_arg']),
                daemon = True,
            )
            t.start()

        except Exception as e:
            log.error(f"WebSocket message error: {e}")

    def on_error(ws, error):
        log.error(f"WebSocket error: {error}")

    def on_close(ws, close_status, close_msg):
        log.warning(f"WebSocket closed: {close_status} {close_msg}")

    def on_open(ws):
        log.info(f"WebSocket connected to Base | subscribing to {len(FACTORIES)} factories...")
        ws.send(subscribe_msg)
        log.info("Subscribed to PairCreated/PoolCreated events ✅")

    # Auto-reconnect loop
    while True:
        try:
            log.info(f"Connecting to Base WebSocket: {BASE_WSS[:50]}...")
            ws = websocket.WebSocketApp(
                BASE_WSS,
                on_open    = on_open,
                on_message = on_message,
                on_error   = on_error,
                on_close   = on_close,
            )
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            log.error(f"WebSocket connection failed: {e}")

        log.info("Reconnecting in 15s...")
        time.sleep(15)


# ── Fallback: periodic DexScreener scan ──────────────────────────────────────

def run_fallback_scan() -> list[dict]:
    """
    Fallback scan using GeckoTerminal's 'New Pools' endpoint.
    This is more reliable for finding recent launches than DexScreener's undocumented endpoints.
    """
    predictions = []
    try:
        # FIX: Use GeckoTerminal for discovery (it has a dedicated new_pools endpoint)
        url = "https://api.geckoterminal.com/api/v2/networks/base/new_pools"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
            # GeckoTerminal returns { "data": [ { "attributes": { ... } } ] }
            pools = data.get('data', [])

        now_ms = time.time() * 1000
        candidates = []
        
        for p in pools:
            attr = p.get('attributes', {})
            
            # Extract token address (GeckoTerminal gives pool address, we need base token)
            # Usually the name is "TOKEN / WETH". We might need to fetch details.
            # Actually, GeckoTerminal provides the pool address. 
            # We can use the pool address to look up the token on DexScreener.
            pool_address = attr.get('address')
            if not pool_address:
                continue

            # 1. Check if already predicted (using pool address as proxy or fetching token)
            # To be safe, let's fetch the pair data from DexScreener using the pool address
            # This maps the GeckoTerminal discovery to our DexScreener analysis pipeline.
            
            # We skip the "already_predicted" check here because we don't have the token address yet.
            # We'll do it after fetching from DexScreener.

            # 2. Check Age (GeckoTerminal 'pool_created_at')
            created_at_str = attr.get('pool_created_at')
            if created_at_str:
                # Parse ISO format: 2024-05-20T12:00:00Z
                try:
                    dt = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                    age_hours = (time.time() - dt.timestamp()) / 3600
                    if age_hours > 24:
                        continue
                except ValueError:
                    pass # Ignore parsing errors

            candidates.append(pool_address)

        log.info(f"Fallback scan: Found {len(candidates)} new pools on GeckoTerminal")

        # Process the top 5 candidates
        for pool_addr in candidates[:5]:
            if not can_predict():
                break
            
            # Bridge: GeckoTerminal Pool -> DexScreener Pair
            # DexScreener can look up by pair address too!
            url_pair = f"https://api.dexscreener.com/latest/dex/pairs/base/{pool_addr}"
            try:
                req_pair = urllib.request.Request(url_pair, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req_pair, timeout=10) as r2:
                    ds_data = json.loads(r2.read().decode())
                    pairs = ds_data.get('pairs', [])
                    
                if not pairs:
                    continue
                    
                pair_data = pairs[0]
                token_addr = pair_data.get('baseToken', {}).get('address')
                
                if not token_addr or already_predicted(token_addr):
                    continue

                # Liquidity Check ($100)
                liquidity = float(pair_data.get('liquidity', {}).get('usd', 0) or 0)
                if liquidity < 100:
                    log.info(f"Skipping {token_addr}: Low liquidity (${liquidity:.0f})")
                    continue
                
                # Symbol Check
                symbol = pair_data.get('baseToken', {}).get('symbol', '?')
                if any(kw in symbol.upper() for kw in ['USD', 'USDC', 'USDT', 'WETH', 'WBTC', 'DAI']):
                    continue

                # 🚀 PREDICT
                fate       = oracle.consult_the_stars(token_addr)
                receipt    = oracle.generate_attestation(token_addr, fate)
                
                raw_verdict = fate.get('verdict', 'UNKNOWN')
                verdict     = raw_verdict.split(' ')[0] if ' ' in raw_verdict else raw_verdict

                pid = save_prediction(
                    agent_id            = AGENT_ID,
                    prediction_type     = "token",
                    subject             = token_addr.lower(),
                    verdict             = verdict,
                    score               = fate.get('score', 0),
                    raw_data            = fate,
                    attestation_uid     = receipt.get('uid', ''),
                    resolve_after_hours = RESOLVE_HOURS,
                )
                record_prediction()
                predictions.append({"prediction_id": pid, "symbol": symbol, "verdict": verdict})
                log.info(f"✅ Fallback Predicted: {symbol} ({pid})")
                time.sleep(3)

            except Exception as e:
                log.error(f"Error processing candidate {pool_addr}: {e}")

    except Exception as e:
        log.error(f"Fallback scan error: {e}", exc_info=True)

    return predictions


# ── Public API (called by app.py) ────────────────────────────────────────────

def run_watch_cycle() -> list[dict]:
    """
    Public entry point called by app.py /watch endpoint and the background thread.
    Uses WebSocket-based detection if Alchemy key is set, otherwise fallback scan.
    For manual/scheduled calls, always runs the DexScreener scan directly.
    """
    return run_fallback_scan()


# ── Entry point ─────────────────────────────────────────────────────────────────

def run_forever():
    """
    Start the watcher. Uses WebSocket if Alchemy key is configured,
    falls back to periodic DexScreener scan otherwise.
    """
    alchemy_key = os.getenv("ALCHEMY_API_KEY")

    if alchemy_key and alchemy_key != "demo":
        log.info("Alchemy key found — using WebSocket event listener (real-time)")
        listen_for_pairs()  # blocks, auto-reconnects
    else:
        log.warning(
            "No ALCHEMY_API_KEY set — falling back to periodic DexScreener scan. "
            "Add ALCHEMY_API_KEY for real-time event-driven predictions."
        )
        interval = int(os.getenv("WATCH_INTERVAL_SECONDS", "600"))
        while True:
            try:
                run_fallback_scan()
            except Exception as e:
                log.error(f"Fallback scan error: {e}")
            time.sleep(interval)


if __name__ == "__main__":
    run_forever()
