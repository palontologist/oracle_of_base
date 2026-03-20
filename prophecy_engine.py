# The Oracle of Base (TOB) - Financial Prophecy Engine
# Generates "Divine Safety Scores" for tokens on Base

import json
import time
import hashlib
import urllib.request
import urllib.error
import os
import requests

# ERC-8004 Registry Addresses (Base Mainnet)
IDENTITY_REGISTRY = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
REPUTATION_REGISTRY = "0x8004BAa17C55a88189AE136b182e5fdA19dE9b63"

class FinancialProphet:
    def __init__(self, agent_id, private_key):
        self.agent_id = agent_id
        self.private_key = private_key
        self.endpoint = "https://oracle.nanobot.dev/api/v1"

    def fetch_token_data(self, token_address):
        """
        Fetches real token data from DexScreener API.
        """
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        )
        try:
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
                if not data.get('pairs'):
                    return None
                # Return the most liquid pair on Base
                base_pairs = [p for p in data['pairs'] if p['chainId'] == 'base']
                if not base_pairs:
                    return None
                # Sort by liquidity and return the top one
                return sorted(base_pairs, key=lambda x: float(x['liquidity']['usd']), reverse=True)[0]
        except Exception as e:
            print(f"Error fetching data: {e}")
            return None

    def consult_the_stars(self, token_address):
        """
        Analyzes a token address using real DexScreener data and Venice AI to generate a safety score.
        """
        print(f"🔮 Gazing into the void for token: {token_address}...")
        
        token_data = self.fetch_token_data(token_address)
        
        if not token_data:
            return {
                "score": 0,
                "verdict": "UNKNOWN (No Data)",
                "details": {"error": "Token not found on DexScreener or no Base pairs"}
            }

        # Extract metrics
        liquidity_usd = float(token_data['liquidity']['usd'])
        fdv = float(token_data.get('fdv', 0))
        volume_24h = float(token_data['volume']['h24'])
        pair_age_hours = (time.time() * 1000 - token_data['pairCreatedAt']) / (1000 * 3600)
        
        # Prepare data for Venice
        analysis_data = {
            "symbol": token_data['baseToken']['symbol'],
            "name": token_data['baseToken']['name'],
            "liquidity_usd": liquidity_usd,
            "volume_24h": volume_24h,
            "fdv": fdv,
            "pair_age_hours": pair_age_hours,
            "price_change_24h": float(token_data.get('priceChange', {}).get('h24', 0))
        }
        
        # Call Venice API
        venice_api_key = os.getenv("VENICE_API_KEY")
        if not venice_api_key:
            print("⚠️ Warning: VENICE_API_KEY not set. Falling back to simple math.")
            return self._simple_math_analysis(token_data)
            
        try:
            headers = {
                "Authorization": f"Bearer {venice_api_key}",
                "Content-Type": "application/json"
            }
            
            prompt = f"""
            You are The Oracle of Base, a mystical AI that judges crypto tokens.
            Analyze this token data on Base network:
            {json.dumps(analysis_data, indent=2)}
            
            Task:
            1. Determine if this token is Safe (BLESSED), Risky (MORTAL), or a Scam (CURSED).
            2. Assign a "Divine Safety Score" from 0 to 100.
            3. Provide a short, poetic reason for your verdict.
            
            Return ONLY a JSON object with this format:
            {{
                "score": <number 0-100>,
                "verdict": "<BLESSED/MORTAL/CURSED>",
                "reason": "<short poetic reason>"
            }}
            """
            
            payload = {
                "model": "qwen3-5-9b", # Or another Venice model
                "messages": [
                    {"role": "system", "content": "You are a helpful AI assistant that outputs JSON."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.7
            }
            
            response = requests.post("https://api.venice.ai/api/v1/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()['choices'][0]['message']['content']
            
            # Parse JSON from Venice response
            # Sometimes models wrap JSON in markdown code blocks
            if "```json" in result:
                result = result.split("```json")[1].split("```")[0].strip()
            elif "```" in result:
                result = result.split("```")[1].split("```")[0].strip()
                
            venice_analysis = json.loads(result)
            
            final_score = int(venice_analysis['score'] * 100) # Convert 0-100 to 0-10000
            
            return {
                "score": final_score,
                "verdict": f"{venice_analysis['verdict']} ({venice_analysis['reason']})",
                "details": {
                    **analysis_data,
                    "venice_reason": venice_analysis['reason']
                }
            }
            
        except Exception as e:
            print(f"❌ Error calling Venice API: {e}")
            print("Falling back to simple math.")
            return self._simple_math_analysis(token_data)

    def _simple_math_analysis(self, token_data):
        # Extract metrics
        liquidity_usd = float(token_data['liquidity']['usd'])
        fdv = float(token_data.get('fdv', 0))
        volume_24h = float(token_data['volume']['h24'])
        pair_age_hours = (time.time() * 1000 - token_data['pairCreatedAt']) / (1000 * 3600)
        
        # --- SCORING LOGIC ---
        score = 0
        
        # 1. Liquidity Score (Max 4000)
        if liquidity_usd > 500000: score += 4000
        elif liquidity_usd > 100000: score += 3000
        elif liquidity_usd > 10000: score += 1000
        else: score += 0
        
        # 2. Volume Score (Max 2000)
        if volume_24h > 100000: score += 2000
        elif volume_24h > 10000: score += 1000
        else: score += 500
        
        # 3. Age Score (Max 2000)
        if pair_age_hours > 720: score += 2000 # > 30 days
        elif pair_age_hours > 168: score += 1000 # > 7 days
        elif pair_age_hours > 24: score += 500 # > 1 day
        else: score -= 1000 # Brand new = risky
        
        # 4. FDV/Liquidity Ratio (Max 2000)
        # Healthy ratio is usually FDV < 10x Liquidity
        if liquidity_usd > 0:
            ratio = fdv / liquidity_usd
            if ratio < 5: score += 2000
            elif ratio < 20: score += 1000
            else: score -= 1000 # Overvalued or low liquidity
            
        final_score = max(0, min(10000, score))
        
        return {
            "score": final_score,
            "verdict": self._interpret_score(final_score),
            "details": {
                "liquidity_usd": liquidity_usd,
                "volume_24h": volume_24h,
                "pair_age_hours": round(pair_age_hours, 1),
                "fdv_liquidity_ratio": round(fdv/liquidity_usd, 2) if liquidity_usd > 0 else "N/A",
                "symbol": token_data['baseToken']['symbol'],
                "name": token_data['baseToken']['name']
            }
        }

    def _interpret_score(self, score):
        if score > 8000: return "BLESSED (Safe)"
        if score > 5000: return "MORTAL (Risky)"
        return "CURSED (Rug imminent)"

    def generate_attestation(self, token_address, analysis_result):
        """
        Formats the analysis into an ERC-8004 Reputation Registry payload.
        """
        attestation = {
            "agentId": self.agent_id,
            "target": token_address, # In reality, we attest to the Agent who created it, or the Token Contract Identity
            "value": analysis_result['score'],
            "valueDecimals": 2,
            "tag1": "financial-prophecy",
            "tag2": "token-safety",
            "endpoint": self.endpoint,
            "metadata_ipfs": "ipfs://QmPlaceholderForFullReport",
            "timestamp": int(time.time())
        }
        
        return attestation

def main():
    # Our Agent ID from registration
    MY_AGENT_ID = "13fc4b5f265242c9a91da155017226fd" 
    
    oracle = FinancialProphet(MY_AGENT_ID, "private_key_placeholder")
    
    # Test Tokens
    tokens = {
        "DEGEN": "0x4ed4E862860beD51a9570b96d89aF5E1B0Efefed",
        "BRETT": "0x532f27101965dd16442E59d40670FaF5eBB142E4"
    }
    
    for name, address in tokens.items():
        print(f"\n--- ANALYZING {name} ---")
        fate = oracle.consult_the_stars(address)
        
        if fate['score'] == 0:
            print(f"Could not analyze {name}")
            continue
            
        receipt = oracle.generate_attestation(address, fate)
        
        print(f"Target: {address}")
        print(f"Token: {fate['details']['name']} ({fate['details']['symbol']})")
        print(f"Verdict: {fate['verdict']}")
        print(f"Divine Score: {fate['score']/100}/100")
        print(f"Details: {json.dumps(fate['details'], indent=2)}")
        print("\n--- 🧾 ON-CHAIN RECEIPT (ERC-8004) ---")
        print(json.dumps(receipt, indent=2))
    
    print("\n--- END OF PROPHECY ---")

if __name__ == "__main__":
    main()
