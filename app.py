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

from prophecy_engine import FinancialProphet
from social_prophet import SocialProphet
from prediction_store import save_prediction, get_reputation_stats
from resolution_engine import run_resolution_cycle

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
AGENT_ID       = "34499"
PRIVATE_KEY    = os.getenv("AGENT_PRIVATE_KEY")
WALLET_ADDRESS = "0x1EA37E2Fb76Aa396072204C90fcEF88093CEb920"

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
        "description": "AI financial prophecy",
        "mimeType":    "application/json"
    },
    "GET /social-prophecy": {
        "accepts": [{
            "scheme":  "exact",
            "price":   "$0.01",
            "network": "eip155:8453",
            "payTo":   WALLET_ADDRESS
        }],
        "description": "AI social prophecy",
        "mimeType":    "application/json"
    },
}


# ── Background resolution scheduler ──────────────────────────────────────────
# NOTE: Called at module level so gunicorn picks it up on import,
# not just when running `python app.py` directly.

_scheduler_started = False

def start_resolution_scheduler():
    """
    Starts the resolution engine in a daemon thread.
    Guard flag prevents duplicate threads if gunicorn spawns multiple workers.
    """
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

# ← Runs at import time, so gunicorn triggers it automatically
start_resolution_scheduler()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/prophecy', methods=['GET'])
def get_financial_prophecy():
    token_address = request.args.get('token')
    if not token_address:
        return jsonify({"error": "Missing 'token'"}), 400
    try:
        fate = financial_oracle.consult_the_stars(token_address)
        if fate.get('score', 0) == 0:
            return jsonify({"error": "Could not analyze", "details": fate.get('details')}), 404

        receipt = financial_oracle.generate_attestation(token_address, fate)

        prediction_id = save_prediction(
            agent_id            = AGENT_ID,
            prediction_type     = "token",
            subject             = token_address,
            verdict             = fate.get("verdict", "UNKNOWN"),
            score               = fate.get("score", 0),
            raw_data            = fate,
            attestation_uid     = receipt.get("uid", ""),
            resolve_after_hours = 0,
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
            verdict             = fate.get("verdict", "UNKNOWN"),
            score               = fate.get("score", 0),
            raw_data            = fate,
            attestation_uid     = receipt.get("uid", ""),
            resolve_after_hours = 24,
        )
        log.info(f"Prediction saved | id={prediction_id[:8]} | handle={handle}")

        return jsonify({
            "prophecy":      fate,
            "receipt":       receipt,
            "prediction_id": prediction_id,
        })
    except Exception as e:
        log.error(f"/social-prophecy error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/trust-check', methods=['GET'])
def trust_check():
    """
    Simple yes/no trust gate for agent-to-agent commerce.
    Returns whether this Oracle meets a minimum trust threshold.
    """
    stats = get_reputation_stats(AGENT_ID)
    trust_score = stats.get("trust_score") or 0
    total = stats.get("total_resolved") or 0
    
    return jsonify({
        "trusted": trust_score >= 70 and total >= 5,
        "trust_score": trust_score,
        "total_resolved": total,
        "meets_threshold": {
            "min_score": 70,
            "min_resolved": 5,
        }
    })


@app.route('/resolve', methods=['GET','POST'])
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
    })


# ── x402 Payment Middleware ───────────────────────────────────────────────────
PaymentMiddleware(app, server, routes)

# ── Local dev entry point ─────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False)