from flask import Flask, request, jsonify
import os
import sys
import threading
import logging
from dotenv import load_dotenv

# x402 imports
from x402.server import x402ResourceServerSync
from x402.http.facilitator_client import HTTPFacilitatorClientSync
from x402.http.middleware.flask import PaymentMiddleware
from x402.mechanisms.evm.exact import ExactEvmServerScheme

load_dotenv()
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from prophecy_engine  import FinancialProphet
from social_prophet   import SocialProphet
from trust_engine     import full_prophecy
from prediction_store import save_prediction, get_reputation_stats, get_conn
import psycopg2
import psycopg2.extras
from resolution_engine import run_resolution_cycle
from watcher import run_watch_cycle

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
AGENT_ID       = "34499"
PRIVATE_KEY    = os.getenv("AGENT_PRIVATE_KEY")
WALLET_ADDRESS = "0x1EA37E2Fb76Aa396072204C90fcEF88093CEb920"
RESOLVE_HOURS  = int(os.getenv("RESOLVE_AFTER_HOURS", "24"))

# ── Oracles ───────────────────────────────────────────────────────────────────
financial_oracle = FinancialProphet(AGENT_ID, PRIVATE_KEY)
social_oracle    = SocialProphet(AGENT_ID, PRIVATE_KEY)

# ── x402 Setup ────────────────────────────────────────────────────────────────
facilitator = HTTPFacilitatorClientSync({"url": "https://facilitator.x402.org"})
server      = x402ResourceServerSync(facilitator_clients=[facilitator])
server.register("eip155:8453", ExactEvmServerScheme())

routes = {
    "GET /prophecy": {
        "accepts": [{
            "scheme":  "exact",
            "price":   "$0.01",
            "network": "eip155:8453",
            "payTo":   WALLET_ADDRESS,
            "token":   "USDC"
        }],
        "description": "AI financial prophecy — token safety score",
        "mimeType":    "application/json"
    },
    "GET /social-prophecy": {
        "accepts": [{
            "scheme":  "exact",
            "price":   "$0.01",
            "network": "eip155:8453",
            "payTo":   WALLET_ADDRESS
        }],
        "description": "AI social prophecy — agent purity score",
        "mimeType":    "application/json"
    },
    "GET /combined-prophecy": {
        "accepts": [{
            "scheme":  "exact",
            "price":   "$0.05",
            "network": "eip155:8453",
            "payTo":   WALLET_ADDRESS,
            "token":   "USDC"
        }],
        "description": "Full trust assessment — token + deployer + social signals combined",
        "mimeType":    "application/json"
    },
}


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/prophecy', methods=['GET'])
def get_financial_prophecy():
    """Token safety score — on-chain metrics via DexScreener + Venice AI."""
    token_address = request.args.get('token')
    if not token_address:
        return jsonify({"error": "Missing 'token'"}), 400
    try:
        fate    = financial_oracle.consult_the_stars(token_address)
        if fate.get('score', 0) == 0:
            return jsonify({"error": "Could not analyze", "details": fate.get('details')}), 404

        receipt = financial_oracle.generate_attestation(token_address, fate)

        prediction_id = save_prediction(
            agent_id            = AGENT_ID,
            prediction_type     = "token",
            subject             = token_address,
            verdict             = fate.get("verdict", "UNKNOWN").split(" ")[0],
            score               = fate.get("score", 0) // 100,
            raw_data            = fate,
            attestation_uid     = receipt.get("uid", ""),
            resolve_after_hours = RESOLVE_HOURS,
        )
        log.info(f"Prediction saved | id={prediction_id[:8]} | token={token_address}")

        return jsonify({
            "prophecy":      fate,
            "receipt":       receipt,
            "prediction_id": prediction_id,
        })
    except Exception as e:
        log.error(f"/prophecy error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/social-prophecy', methods=['GET'])
def get_social_prophecy():
    """Agent purity score — Farcaster identity + posting behaviour + wallet activity."""
    handle = request.args.get('handle')
    if not handle:
        return jsonify({"error": "Missing 'handle'"}), 400
    try:
        fate    = social_oracle.consult_the_spirits(handle)
        receipt = social_oracle.generate_attestation(handle, fate)

        prediction_id = save_prediction(
            agent_id            = AGENT_ID,
            prediction_type     = "social",
            subject             = handle,
            verdict             = fate.get("verdict", "UNKNOWN").split(" ")[0],
            score               = fate.get("score", 0) // 100,
            raw_data            = fate,
            attestation_uid     = receipt.get("uid", ""),
            resolve_after_hours = RESOLVE_HOURS,
        )
        log.info(f"Social prediction saved | id={prediction_id[:8]} | handle={handle}")

        return jsonify({
            "prophecy":      fate,
            "receipt":       receipt,
            "prediction_id": prediction_id,
        })
    except Exception as e:
        log.error(f"/social-prophecy error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/combined-prophecy', methods=['GET'])
def get_combined_prophecy():
    """
    Full trust assessment combining all three signal layers:
      - Token safety (on-chain metrics + Venice AI)
      - Deployer history (previous tokens + rug rate)
      - Social promotion (Farcaster mentions + bot detection)

    This is the premium endpoint — $0.05 via x402.
    Agents with X-Moltbook-Identity header get their karma logged.
    Returns a single unified verdict with confidence score.
    """
    token_address = request.args.get('token')
    if not token_address:
        return jsonify({"error": "Missing 'token'"}), 400

    # ── Moltbook identity check (optional — enriches caller info) ────────
    caller_info = {}
    moltbook_token = request.headers.get('X-Moltbook-Identity')
    if moltbook_token:
        try:
            from moltbook_client import verify_identity
            identity = verify_identity(moltbook_token)
            if identity.get('agent'):
                caller_info = {
                    "moltbook_agent": identity['agent'].get('name'),
                    "karma":          identity['agent'].get('karma', 0),
                    "verified":       identity['agent'].get('verified', False),
                }
                log.info(f"Moltbook caller: {caller_info['moltbook_agent']} karma={caller_info['karma']}")
        except Exception as e:
            log.debug(f"Moltbook identity check skipped: {e}")

    try:
        result = full_prophecy(
            token_address    = token_address,
            financial_prophet = financial_oracle,
            social_prophet    = social_oracle,
        )

        if result.get('status') == 'failed':
            return jsonify({"error": result.get('reason'), "details": result}), 404

        # Save as a combined prediction for resolution tracking
        prediction_id = save_prediction(
            agent_id            = AGENT_ID,
            prediction_type     = "token",
            subject             = token_address,
            verdict             = result.get("verdict", "UNKNOWN"),
            score               = result.get("final_score", 0),
            raw_data            = result,
            attestation_uid     = result.get('attestation', {}).get('uid', ''),
            resolve_after_hours = RESOLVE_HOURS,
        )
        log.info(
            f"Combined prophecy saved | id={prediction_id[:8]} | "
            f"token={token_address} | verdict={result.get('verdict')} | "
            f"score={result.get('final_score')} | confidence={result.get('confidence')}"
        )

        return jsonify({
            **result,
            "prediction_id": prediction_id,
            **({"caller": caller_info} if caller_info else {}),
        })

    except Exception as e:
        log.error(f"/combined-prophecy error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/moltbook/status', methods=['GET'])
def moltbook_status():
    """Check Oracle's Moltbook registration and karma status."""
    try:
        from moltbook_client import get_status, get_profile
        status  = get_status()
        profile = get_profile()
        return jsonify({
            "status":  status,
            "profile": profile,
            "posting_verdicts": list(
                os.getenv("MOLTBOOK_POST_VERDICTS", "CURSED").upper().split(",")
            ),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/predictions', methods=['GET'])
def get_predictions():
    """
    View all predictions with optional status filter.
    Useful for monitoring what the watcher has auto-predicted.

    Query params:
      status=pending|resolved|all  (default: all)
      limit=N                      (default: 20, max: 100)
      verdict=BLESSED|MORTAL|CURSED
    """
    status  = request.args.get('status',  'all').upper()
    limit   = min(int(request.args.get('limit', 20)), 100)
    verdict = request.args.get('verdict', '').upper()

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

                filters = []
                params  = []

                if status != 'ALL':
                    filters.append("status = %s")
                    params.append(status)

                if verdict:
                    filters.append("verdict = %s")
                    params.append(verdict)

                where = ("WHERE " + " AND ".join(filters)) if filters else ""
                params.append(limit)

                cur.execute(f"""
                    SELECT
                        id,
                        subject         AS token_address,
                        verdict,
                        score,
                        status,
                        created_at,
                        resolve_after,
                        resolved_at,
                        resolution_uid,
                        attestation_uid
                    FROM predictions
                    {where}
                    ORDER BY created_at DESC
                    LIMIT %s
                """, params)

                rows = [dict(r) for r in cur.fetchall()]

                # Summary counts
                cur.execute("""
                    SELECT
                        status,
                        verdict,
                        COUNT(*) as count
                    FROM predictions
                    GROUP BY status, verdict
                    ORDER BY status, verdict
                """)
                summary = [dict(r) for r in cur.fetchall()]

        return jsonify({
            "count":       len(rows),
            "summary":     summary,
            "predictions": rows,
        })

    except Exception as e:
        log.error(f"/predictions error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/reputation', methods=['GET'])
def get_reputation():
    """Public trust stats — queryable by any agent before buying signals."""
    agent_id = request.args.get('agent_id', AGENT_ID)
    stats    = get_reputation_stats(agent_id)
    return jsonify(stats)


@app.route('/trust-check', methods=['GET'])
def trust_check():
    """
    Simple yes/no trust gate for agent-to-agent commerce.
    Free endpoint — drives discovery.
    Other agents call this before deciding to pay for full signals.
    """
    stats       = get_reputation_stats(AGENT_ID)
    trust_score = stats.get("trust_score") or 0
    total       = stats.get("total_resolved") or 0

    return jsonify({
        "trusted":       trust_score >= 70 and total >= 5,
        "trust_score":   trust_score,
        "total_resolved": total,
        "agent_id":      AGENT_ID,
        "wallet":        WALLET_ADDRESS,
        "meets_threshold": {
            "min_score":    70,
            "min_resolved": 5,
        }
    })


@app.route('/watch', methods=['GET', 'POST'])
def trigger_watch():
    """
    Manually trigger a watch cycle.
    Fires in a background thread and returns immediately —
    Venice calls take 45s+ per token so the full cycle takes several minutes.
    Check /predictions afterwards to see new results.
    """
    try:
        from watcher import run_watch_cycle as _run_watch_cycle

        def _bg():
            try:
                results = _run_watch_cycle()
                log.info(f"/watch cycle complete: {len(results)} predicted")
            except Exception as e:
                log.error(f"/watch background error: {e}", exc_info=True)

        t = threading.Thread(target=_bg, daemon=True, name="manual-watch")
        t.start()

        return jsonify({
            "status":  "started",
            "message": (
                "Watch cycle started in background. "
                "Check /predictions in ~5 minutes for new results."
            ),
        })

    except Exception as e:
        log.error(f"/watch error: {e}", exc_info=True)
        return jsonify({"error": str(e), "type": type(e).__name__}), 500


@app.route('/resolve', methods=['GET', 'POST'])
def trigger_resolution():
    """Manually trigger a resolution cycle — useful for testing."""
    results = run_resolution_cycle()
    return jsonify({
        "resolved": len(results),
        "results":  results,
    })


@app.route('/health', methods=['GET'])
def health_check():
    stats = get_reputation_stats(AGENT_ID)
    return jsonify({
        "status":      "ok",
        "service":     "Oracle of Base",
        "trust_score": stats.get("trust_score"),
        "resolved":    stats.get("total_resolved"),
        "pending":     stats.get("pending"),
        "endpoints": {
            "free":    ["/health", "/trust-check", "/reputation"],
            "$0.01":   ["/prophecy", "/social-prophecy"],
            "$0.05":   ["/combined-prophecy"],
        },
        "watcher": {
            "interval_seconds": int(os.getenv("WATCH_INTERVAL_SECONDS", "600")),
            "max_token_age_hours": float(os.getenv("MAX_TOKEN_AGE_HOURS", "2")),
            "min_liquidity_usd": float(os.getenv("MIN_LIQUIDITY_USD", "1000")),
        }
    })


# ── Background resolution scheduler ──────────────────────────────────────────

_scheduler_started = False

def start_resolution_scheduler():
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def _loop():
        import time
        interval = int(os.getenv("RESOLUTION_POLL_SECONDS", "300"))
        log.info(f"Resolution scheduler started (interval={interval}s)")
        while True:
            try:
                results = run_resolution_cycle()
                if results:
                    log.info(f"Resolution cycle: {len(results)} resolved")
            except Exception as e:
                log.error(f"Resolution cycle error: {e}", exc_info=True)
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="resolution-engine")
    t.start()
    log.info("Resolution engine thread started.")

# Start at import time so gunicorn picks it up
start_resolution_scheduler()


# ── Background watcher ────────────────────────────────────────────────────────

_watcher_started = False

def start_watcher():
    global _watcher_started
    if _watcher_started:
        return
    _watcher_started = True

    def _loop():
        import time as _time
        from watcher import run_watch_cycle as _run_watch_cycle
        interval = int(os.getenv("WATCH_INTERVAL_SECONDS", "600"))
        log.info(f"Watcher thread started (interval={interval}s)")
        # Small initial delay so app finishes booting before first scan
        _time.sleep(30)
        while True:
            try:
                results = _run_watch_cycle()
                if results:
                    log.info(f"Watcher cycle: {len(results)} new predictions")
            except Exception as e:
                log.error(f"Watcher error: {e}", exc_info=True)
            _time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="watcher")
    t.start()
    log.info("Watcher thread started.")

start_watcher()

# ── x402 Payment Middleware ───────────────────────────────────────────────────
PaymentMiddleware(app, server, routes)

# ── Local dev entry point ─────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False)