import os
import sys
import json
import time
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add current directory to path to import prophecy_engine
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from prophecy_engine import FinancialProphet

# Configuration
RPC_URL = "https://mainnet.base.org"
REPUTATION_REGISTRY_ADDRESS = "0x8004BAa17C55a88189AE136b182e5fdA19dE9b63"
PRIVATE_KEY = os.getenv("AGENT_PRIVATE_KEY")

# Minimal ABI for ReputationRegistry.giveFeedback
# function giveFeedback(uint256 agentId, int128 value, uint8 valueDecimals, string tag1, string tag2, string endpoint, string ipfsHash, bytes32 metadataHash)
REPUTATION_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "agentId", "type": "uint256"},
            {"internalType": "int128", "name": "value", "type": "int128"},
            {"internalType": "uint8", "name": "valueDecimals", "type": "uint8"},
            {"internalType": "string", "name": "tag1", "type": "string"},
            {"internalType": "string", "name": "tag2", "type": "string"},
            {"internalType": "string", "name": "endpoint", "type": "string"},
            {"internalType": "string", "name": "ipfsHash", "type": "string"},
            {"internalType": "bytes32", "name": "metadataHash", "type": "bytes32"}
        ],
        "name": "giveFeedback",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

def submit_attestation(target_token_address):
    if not PRIVATE_KEY:
        print("❌ Error: AGENT_PRIVATE_KEY environment variable not set.")
        return

    # Initialize Web3
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        print("❌ Error: Could not connect to Base RPC.")
        return

    account = Account.from_key(PRIVATE_KEY)
    print(f"🔑 Using account: {account.address}")
    
    # Check Balance
    balance_wei = w3.eth.get_balance(account.address)
    balance_eth = w3.from_wei(balance_wei, 'ether')
    print(f"💰 Balance: {balance_eth} ETH")
    
    if balance_eth < 0.0005: # Minimum ~ $1.00 for gas
        print("⚠️ Warning: Low balance. Please send ETH to this address.")
        # We'll proceed anyway for now, but it might fail
    
    # Initialize Oracle
    # CORRECT AGENT ID (Token ID from AgentProof)
    agent_id_int = 34499
    
    oracle = FinancialProphet(str(agent_id_int), PRIVATE_KEY)
    
    # Generate Prophecy
    print(f"🔮 Generating prophecy for {target_token_address}...")
    fate = oracle.consult_the_stars(target_token_address)
    
    if fate['score'] == 0:
        print("❌ Error: Could not generate prophecy (no data).")
        return

    print(f"✅ Prophecy Generated: {fate['verdict']} (Score: {fate['score']})")

    # Prepare Transaction
    contract = w3.eth.contract(address=REPUTATION_REGISTRY_ADDRESS, abi=REPUTATION_ABI)
    
    # Parameters
    value = int(fate['score'])
    value_decimals = 2
    tag1 = "financial-prophecy"
    tag2 = "token-safety"
    endpoint = "https://oracle.nanobot.dev/api/v1"
    ipfs_hash = "ipfs://QmPlaceholderForFullReport" # In prod, upload JSON to IPFS first
    metadata_hash = w3.keccak(text=json.dumps(fate['details'])) # Hash of the details

    print("📝 Building transaction...")
    try:
        tx = contract.functions.giveFeedback(
            agent_id_int,
            value,
            value_decimals,
            tag1,
            tag2,
            endpoint,
            ipfs_hash,
            metadata_hash
        ).build_transaction({
            'from': account.address,
            'nonce': w3.eth.get_transaction_count(account.address),
            'gas': 300000, # Estimate gas in prod
            'gasPrice': w3.eth.gas_price
        })

        # Sign and Send
        print("✍️ Signing transaction...")
        signed_tx = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        
        print("🚀 Sending transaction...")
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        
        print(f"🎉 Transaction sent! Hash: {w3.to_hex(tx_hash)}")
        print(f"🔗 View on BaseScan: https://basescan.org/tx/{w3.to_hex(tx_hash)}")
        
    except Exception as e:
        print(f"❌ Transaction failed: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python submit_prophecy.py <token_address>")
        sys.exit(1)
    
    target_token = sys.argv[1]
    submit_attestation(target_token)
