from flask import Flask, request, jsonify
import os
import sys
from dotenv import load_dotenv
# --- CORRECT IMPORTS FOR x402 ---
from x402.server import x402ResourceServerSync
from x402.http.facilitator_client import HTTPFacilitatorClientSync
from x402.http.middleware.flask import PaymentMiddleware
from x402.mechanisms.evm.exact import ExactEvmServerScheme

# Load environment variables
load_dotenv()

# Add current directory to path to import prophecy_engine
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from prophecy_engine import FinancialProphet
from social_prophet import SocialProphet

app = Flask(__name__)

# Initialize Oracles
AGENT_ID = "34499"
PRIVATE_KEY = os.getenv("AGENT_PRIVATE_KEY")
WALLET_ADDRESS = "0x1EA37E2Fb76Aa396072204C90fcEF88093CEb920" # Your burner wallet

# Initialize with AGENT_ID and PRIVATE_KEY (Matches your engine)
financial_oracle = FinancialProphet(AGENT_ID, PRIVATE_KEY)
social_oracle = SocialProphet(AGENT_ID, PRIVATE_KEY)

# --- x402 Payment Setup (FIXED) ---
# 1. Create Facilitator Client (Sync)
# Use config dict for initialization
facilitator = HTTPFacilitatorClientSync(config={"url": "https://facilitator.x402.org"})

# 2. Create Server (Sync)
# Pass the client object in a list
server = x402ResourceServerSync(
    facilitator_clients=[facilitator]
)

# 3. Register Payment Scheme (Base Mainnet USDC)
server.register_scheme(ExactEvmServerScheme)

# 4. Define Routes with Pricing
routes = {
    "GET /prophecy": {
        "accepts": [
            {
                "scheme": "exact",
                "price": "$0.01", # 1 cent per prophecy
                "network": "eip155:8453", # Base Mainnet
                "payTo": WALLET_ADDRESS,
                "token": "USDC" # Optional, defaults to USDC on Base
            }
        ],
        "description": "Get a Venice-powered AI prophecy for a token on Base.",
        "mimeType": "application/json"
    },
    "GET /social-prophecy": {
        "accepts": [
            {
                "scheme": "exact",
                "price": "$0.01",
                "network": "eip155:8453",
                "payTo": WALLET_ADDRESS
            }
        ],
        "description": "Get an AI analysis of a Farcaster handle.",
        "mimeType": "application/json"
    }
}

# 5. Apply Middleware
app.wsgi_app = PaymentMiddleware(app.wsgi_app, server, routes)

@app.route('/prophecy', methods=['GET'])
def get_financial_prophecy():
    token_address = request.args.get('token')
    if not token_address:
        return jsonify({"error": "Missing 'token' parameter"}), 400
    
    try:
        # Generate Prophecy
        fate = financial_oracle.consult_the_stars(token_address)
        
        if fate['score'] == 0:
            return jsonify({"error": "Could not analyze token", "details": fate.get('details')}), 404
            
        # Generate Receipt
        receipt = financial_oracle.generate_attestation(token_address, fate)
        
        return jsonify({
            "prophecy": fate,
            "receipt": receipt
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/social-prophecy', methods=['GET'])
def get_social_prophecy():
    handle = request.args.get('handle')
    if not handle:
        return jsonify({"error": "Missing 'handle' parameter"}), 400
        
    try:
        # Generate Prophecy
        fate = social_oracle.consult_the_spirits(handle)
        
        # Generate Receipt
        receipt = social_oracle.generate_attestation(handle, fate)
        
        return jsonify({
            "prophecy": fate,
            "receipt": receipt
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "service": "The Oracle of Base"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)
