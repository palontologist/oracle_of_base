from flask import Flask, request, jsonify
import os
import sys
from dotenv import load_dotenv

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

financial_oracle = FinancialProphet(AGENT_ID, PRIVATE_KEY)
social_oracle = SocialProphet(AGENT_ID, PRIVATE_KEY)

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
