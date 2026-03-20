from flask import Flask, request, jsonify
import os
import sys
from types import SimpleNamespace
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

app = Flask(__name__)

# Config
AGENT_ID = "34499"
PRIVATE_KEY = os.getenv("AGENT_PRIVATE_KEY")
WALLET_ADDRESS = "0x1EA37E2Fb76Aa396072204C90fcEF88093CEb920"

# Oracles
financial_oracle = FinancialProphet(AGENT_ID, PRIVATE_KEY)
social_oracle = SocialProphet(AGENT_ID, PRIVATE_KEY)

# x402 Setup
# FIX: HTTPFacilitatorClientSync expects a config object with a .url attribute,
# not a raw string. We use SimpleNamespace as a lightweight config wrapper.
_facilitator_config = SimpleNamespace(url="https://facilitator.x402.org")
facilitator = HTTPFacilitatorClientSync(_facilitator_config)

server = x402ResourceServerSync(facilitator_clients=[facilitator])
server.register("eip155:8453", ExactEvmServerScheme())

routes = {
    "GET /prophecy": {
        "accepts": [{
            "scheme": "exact",
            "price": "$0.01",
            "network": "eip155:8453",
            "payTo": WALLET_ADDRESS,
            "token": "USDC"
        }],
        "description": "AI financial prophecy",
        "mimeType": "application/json"
    },
    "GET /social-prophecy": {
        "accepts": [{
            "scheme": "exact",
            "price": "$0.01",
            "network": "eip155:8453",
            "payTo": WALLET_ADDRESS
        }],
        "description": "AI social prophecy",
        "mimeType": "application/json"
    }
}


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
        return jsonify({"prophecy": fate, "receipt": receipt})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/social-prophecy', methods=['GET'])
def get_social_prophecy():
    handle = request.args.get('handle')
    if not handle:
        return jsonify({"error": "Missing 'handle'"}), 400
    try:
        fate = social_oracle.consult_the_spirits(handle)
        receipt = social_oracle.generate_attestation(handle, fate)
        return jsonify({"prophecy": fate, "receipt": receipt})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "service": "Oracle of Base"})


# Apply PaymentMiddleware AFTER all routes are registered
app.wsgi_app = PaymentMiddleware(app.wsgi_app, server, routes)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)