"""
mandate_integration.py
---------------------
Transaction intelligence layer for autonomous agents.
Intercepts fund transfers and evaluates them against mandate policies.
"""

import os
import logging
import requests
from typing import Optional

log = logging.getLogger("mandate")

MANDATE_API_URL = os.getenv("MANDATE_API_URL", "https://api.mandate.md")
MANDATE_API_KEY = os.getenv("MANDATE_API_KEY", "")
MANDATE_AGENT_ID = os.getenv("MANDATE_AGENT_ID", "")


class MandateClient:
    """
    Client for Mandate transaction intelligence.
    Evaluates transactions before execution against policies.
    """

    def __init__(self, api_url: str = MANDATE_API_URL, api_key: str = MANDATE_API_KEY):
        self.api_url = api_url
        self.api_key = api_key
        self.enabled = bool(api_key)
        if not self.enabled:
            log.info("Mandate disabled (no MANDATE_API_KEY set)")
        else:
            log.info(f"Mandate enabled | agent_id={MANDATE_AGENT_ID[:10] if MANDATE_AGENT_ID else 'N/A'}...")

    def evaluate_transaction(
        self,
        from_address: str,
        to_address: str,
        amount: float,
        token: str = "USDC",
        network: str = "eip155:8453",
        reason: str = "",
    ) -> dict:
        """
        Evaluate a transaction against Mandate policies.
        
        Returns: {
            "approved": bool,
            "action": "approve" | "block" | "review",
            "reason": str,
            "policy_matched": str,
        }
        """
        if not self.enabled:
            return {"approved": True, "action": "approve", "reason": "Mandate disabled", "policy_matched": "none"}

        try:
            response = requests.post(
                f"{self.api_url}/v1/evaluate",
                json={
                    "from": from_address,
                    "to": to_address,
                    "amount": str(amount),
                    "token": token,
                    "network": network,
                    "reason": reason,
                    "agent_id": MANDATE_AGENT_ID,
                },
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=10,
            )

            if response.status_code == 200:
                result = response.json()
                log.info(
                    f"Mandate eval | to={to_address[:10]}... | amount={amount} | "
                    f"action={result.get('action')} | reason={result.get('reason', '')[:50]}"
                )
                return {
                    "approved": result.get("action") == "approve",
                    "action": result.get("action", "review"),
                    "reason": result.get("reason", ""),
                    "policy_matched": result.get("policy", "unknown"),
                }
            else:
                log.warning(f"Mandate API error: {response.status_code} - {response.text}")
                return {"approved": False, "action": "review", "reason": "Mandate API error", "policy_matched": "error"}

        except requests.RequestException as e:
            log.error(f"Mandate request failed: {e}")
            return {"approved": False, "action": "review", "reason": str(e), "policy_matched": "error"}


_mandate_client = None


def get_mandate_client() -> MandateClient:
    global _mandate_client
    if _mandate_client is None:
        _mandate_client = MandateClient()
    return _mandate_client