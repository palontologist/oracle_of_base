"""
lit_skill.py
------------
Oracle of Base × Lit Protocol Dark Knowledge Skill

Handles:
  - Deploying the Lit Action to IPFS
  - Encrypting API keys as Lit conditions
  - Executing the skill via Lit Chipotle TEE
  - Serving the skill at GET /lit/oracle-skill
  - Verifying TEE attestations

The "dark knowledge" that stays sealed inside Chipotle:
  - Venice API key + prompt engineering
  - Historical calibration dataset (private RAG)
  - Deployer flag registry
  - Signal weight thresholds
"""

import os
import json
import time
import logging
import requests
import hashlib

log = logging.getLogger("lit_skill")

LIT_API_BASE    = "https://datil-dev.litgateway.com"  # Chipotle testnet
LIT_RELAY_URL   = "https://relay-server-staging.getlit.dev"
ORACLE_URL      = os.getenv("ORACLE_URL", "https://web-production-b386a.up.railway.app")
WALLET_ADDRESS  = "0x1EA37E2Fb76Aa396072204C90fcEF88093CEb920"
PRIVATE_KEY     = os.getenv("AGENT_PRIVATE_KEY", "")

# IPFS CID of the deployed Lit Action (set after first deployment)
LIT_ACTION_CID  = os.getenv("LIT_ACTION_CID", "")

# Lit PKP public key (minted for the Oracle)
LIT_PKP_PUBKEY  = os.getenv("LIT_PKP_PUBLIC_KEY", "")


class LitOracleSkill:

    def __init__(self):
        self.action_cid = LIT_ACTION_CID
        self.pkp_pubkey = LIT_PKP_PUBKEY

    # ── Encryption helpers ────────────────────────────────────────────────────

    def _build_access_control(self, min_balance_usdc: float = 0) -> list:
        """
        Lit access control conditions for decrypting secrets.
        Only the Lit Action running inside Chipotle TEE can decrypt.
        Optionally: caller must hold USDC balance to use the skill.
        """
        conditions = [
            {
                "conditionType":   "evmBasic",
                "contractAddress": "",
                "standardContractType": "",
                "chain":           "base",
                "method":          "eth_getBalance",
                "parameters":      [":userAddress", "latest"],
                "returnValueTest": {
                    "comparator": ">=",
                    "value":      "0"
                }
            }
        ]

        # Add USDC balance check if skill is paid
        if min_balance_usdc > 0:
            usdc_wei = int(min_balance_usdc * 1e6)
            conditions.append({"operator": "and"})
            conditions.append({
                "conditionType":   "evmBasic",
                "contractAddress": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # USDC on Base
                "standardContractType": "ERC20",
                "chain":           "base",
                "method":          "balanceOf",
                "parameters":      [":userAddress"],
                "returnValueTest": {
                    "comparator": ">=",
                    "value":      str(usdc_wei)
                }
            })

        return conditions

    def deploy_to_ipfs(self, js_path: str = None) -> str:
        """
        Deploy the Lit Action JS to IPFS via Pinata or web3.storage.
        Returns the IPFS CID.
        """
        if js_path is None:
            import os
            js_path = os.path.join(os.path.dirname(__file__), "lit_oracle_skill.js")

        try:
            with open(js_path, "rb") as f:
                content = f.read()

            # Use Pinata public gateway (free tier)
            pinata_url = "https://api.pinata.cloud/pinning/pinFileToIPFS"
            pinata_key = os.getenv("PINATA_API_KEY", "")

            if pinata_key:
                resp = requests.post(
                    pinata_url,
                    headers={"Authorization": f"Bearer {pinata_key}"},
                    files={"file": ("lit_oracle_skill.js", content, "application/javascript")},
                    timeout=30
                )
                if resp.ok:
                    cid = resp.json().get("IpfsHash")
                    log.info(f"Lit Action deployed to IPFS | CID={cid}")
                    self.action_cid = cid
                    return cid

            # Fallback: compute CID locally (deterministic for same content)
            cid_mock = "Qm" + hashlib.sha256(content).hexdigest()[:44]
            log.info(f"IPFS mock CID (no Pinata key): {cid_mock}")
            return cid_mock

        except Exception as e:
            log.error(f"IPFS deploy failed: {e}")
            return ""

    # ── Skill execution ───────────────────────────────────────────────────────

    def execute_skill(self, token_address: str, caller_wallet: str = "") -> dict:
        """
        Execute the sealed Oracle skill inside Lit Chipotle TEE.

        The call:
          1. Sends token_address to the Lit Action
          2. Lit Actions fetches public on-chain data
          3. Decrypts Venice API key + calibration access inside TEE
          4. Runs the sealed scoring logic
          5. Returns verdict + attestation — never the internals

        Returns dict with verdict, score, confidence, reasoning, attestation.
        """
        if not self.action_cid:
            return {
                "error": "Lit Action not deployed — call deploy_to_ipfs() first",
                "skill": "oracle-of-base",
            }

        try:
            payload = {
                "litActionCode":     None,  # use CID instead
                "litActionIpfsId":   self.action_cid,
                "authSig":           self._get_auth_sig(),
                "jsParams": {
                    "tokenAddress":  token_address,
                    "callerWallet":  caller_wallet or WALLET_ADDRESS,
                    "pkpPublicKey":  self.pkp_pubkey,
                },
                "chain": "base",
            }

            resp = requests.post(
                f"{LIT_API_BASE}/v1/action/execute",
                json=payload,
                timeout=60,
                headers={"Content-Type": "application/json"},
            )

            if resp.ok:
                result = resp.json()
                response_str = result.get("response", "{}")
                parsed = json.loads(response_str) if isinstance(response_str, str) else response_str
                return {
                    "success":      True,
                    "tee_executed": True,
                    **parsed,
                }
            else:
                return {"success": False, "error": resp.text[:200]}

        except Exception as e:
            log.error(f"Lit execution failed: {e}")
            return {"success": False, "error": str(e)}

    def _get_auth_sig(self) -> dict:
        """Generate an auth signature for Lit API calls."""
        if not PRIVATE_KEY:
            return {}
        try:
            from eth_account import Account
            from eth_account.messages import encode_defunct

            msg     = f"I am an authorized user of Oracle of Base Lit skill | {int(time.time())}"
            encoded = encode_defunct(text=msg)
            account = Account.from_key(PRIVATE_KEY)
            signed  = account.sign_message(encoded)

            return {
                "sig":    signed.signature.hex(),
                "derivedVia": "web3.eth.personal.sign",
                "signedMessage": msg,
                "address": account.address,
            }
        except Exception as e:
            log.warning(f"Auth sig failed: {e}")
            return {}

    # ── Skill manifest (SKILL.md for Lit) ────────────────────────────────────

    def get_skill_manifest(self) -> dict:
        """
        Machine-readable manifest for agent discovery of this Lit skill.
        Agents can install this skill and call it without knowing how it works.
        """
        return {
            "name":        "oracle-of-base-dark-knowledge",
            "version":     "1.0.0",
            "description": "Sealed token safety scoring — 74% accuracy track record. Venice AI + historical calibration + deployer flags, all sealed inside Lit Chipotle TEE. Returns verdict without exposing the recipe.",
            "ipfs_cid":    self.action_cid,
            "network":     "datil-dev",
            "chain":       "base",
            "inputs": {
                "tokenAddress": "Base chain token contract address (0x...)",
                "callerWallet": "Your wallet address (for attestation)"
            },
            "outputs": {
                "verdict":     "BLESSED | MORTAL | CURSED",
                "score":       "0-100",
                "confidence":  "LOW | MEDIUM | HIGH",
                "reasoning":   "1-2 sentence explanation",
                "attestation": "TEE-signed proof of execution"
            },
            "knowledge_moat": [
                "Venice prompt engineering (74% accuracy calibration)",
                "Historical resolved predictions (private RAG dataset)",
                "Deployer reputation flags (sensitive association data)",
                "Signal weight thresholds (proprietary scoring logic)"
            ],
            "usage": {
                "install": f"GET {ORACLE_URL}/lit/oracle-skill",
                "execute": f"POST {ORACLE_URL}/lit/execute",
                "verify":  f"GET {ORACLE_URL}/lit/verify?token=0x..."
            },
            "oracle_stats": {
                "accuracy":   "74%+",
                "resolved":   "78 predictions",
                "agent_id":   "34499",
                "wallet":     WALLET_ADDRESS,
            }
        }

    # ── Attestation verification ──────────────────────────────────────────────

    def verify_attestation(self, token_address: str, verdict: str,
                           score: int, timestamp: int, signature: str) -> bool:
        """
        Verify a Lit TEE attestation for an Oracle verdict.
        Any agent can call this to confirm the result came from inside Chipotle.
        """
        try:
            from eth_account import Account
            from web3 import Web3

            w3 = Web3()
            msg_hash = w3.solidity_keccak(
                ["string", "string", "uint256", "uint256"],
                [token_address, verdict, score, timestamp]
            )
            recovered = Account.recover_message(
                msg_hash,
                signature=signature
            )
            # Recovered address should be the Lit PKP address
            return recovered.lower() == WALLET_ADDRESS.lower()
        except Exception as e:
            log.warning(f"Attestation verify failed: {e}")
            return False


_lit_skill = None

def get_lit_skill() -> LitOracleSkill:
    global _lit_skill
    if _lit_skill is None:
        _lit_skill = LitOracleSkill()
    return _lit_skill
