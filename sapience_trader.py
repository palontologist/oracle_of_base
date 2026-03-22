"""
sapience_trader.py
------------------
Oracle of Base × Sapience prediction markets.

The Oracle uses its token analysis track record as edge to trade
prediction markets on Sapience (Ethereal chain).

Flow:
  1. After each token prophecy, search Sapience for related markets
  2. Compare Oracle probability estimate to Polymarket price
  3. If edge >= MIN_EDGE_PCT, submit EAS forecast (free, builds track record)
  4. If edge >= MIN_EDGE_PCT * 2 AND actionable, place a paid position
  5. Track all positions and update calibration on resolution

The Oracle starts with free EAS forecasting to build a Sapience
leaderboard track record before committing capital.
"""

import os
import json
import time
import logging
import requests
import asyncio
import websockets

log = logging.getLogger("sapience_trader")

SAPIENCE_API    = "https://api.sapience.xyz"
SAPIENCE_WS     = "wss://relayer.sapience.xyz/auction"
POLYMARKET_API  = "https://gamma-api.polymarket.com"
CLOB_API        = "https://clob.polymarket.com"

WALLET_ADDRESS  = "0x1EA37E2Fb76Aa396072204C90fcEF88093CEb920"
PRIVATE_KEY     = os.getenv("AGENT_PRIVATE_KEY", "")
SAPIENCE_ENABLED = os.getenv("SAPIENCE_ENABLED", "false").lower() == "true"
FORECAST_ONLY   = os.getenv("SAPIENCE_FORECAST_ONLY", "true").lower() == "true"


class SapienceTrader:

    def __init__(self):
        self.enabled = SAPIENCE_ENABLED
        if not self.enabled:
            log.info("Sapience trader disabled — set SAPIENCE_ENABLED=true to activate")
        from edge_engine import get_edge_engine
        self.edge = get_edge_engine()

    # ── Market discovery ──────────────────────────────────────────────────────

    def search_markets(self, query: str, limit: int = 10) -> list:
        """
        Search Sapience GraphQL for markets related to a token or topic.
        Returns list of condition dicts.
        """
        gql = """
        query($search: String!, $take: Int) {
          questions(
            take: $take,
            search: $search,
            resolutionStatus: unresolved,
            sortField: openInterest,
            sortDirection: desc
          ) {
            condition {
              id
              question
              shortName
              endTime
              resolver
              openInterest
              similarMarkets
              categoryId
            }
          }
        }"""
        try:
            r = requests.post(
                f"{SAPIENCE_API}/graphql",
                json={"query": gql, "variables": {"search": query, "take": limit}},
                timeout=10
            )
            if not r.ok:
                return []
            questions = r.json().get("data", {}).get("questions", [])
            return [q["condition"] for q in questions if q.get("condition")]
        except Exception as e:
            log.warning(f"Sapience market search failed: {e}")
            return []

    def get_polymarket_price(self, similar_markets_url: str) -> float | None:
        """
        Extract YES price from Polymarket via Sapience's similarMarkets field.
        Returns probability 0.0-1.0 or None.
        """
        if not similar_markets_url:
            return None
        try:
            # Extract slug from URL
            parts = similar_markets_url.rstrip("/").rstrip("#outcome").split("/")
            slug  = parts[-1].split("#")[0]
            if not slug:
                return None

            r = requests.get(
                f"{POLYMARKET_API}/markets/slug/{slug}",
                timeout=8
            )
            if not r.ok:
                return None

            data  = r.json()
            # outcomePrices is a JSON string "[\"0.65\", \"0.35\"]" for YES/NO
            prices_raw = data.get("outcomePrices")
            if prices_raw:
                prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                if prices:
                    return float(prices[0])  # first = YES price

            return None
        except Exception as e:
            log.debug(f"Polymarket price fetch failed: {e}")
            return None

    # ── Forecast submission (free, EAS on Arbitrum) ────────────────────────────

    def submit_eas_forecast(
        self,
        condition_id:   str,
        resolver:       str,
        probability:    int,    # 0-100
        reasoning:      str,
    ) -> dict:
        """
        Submit a free EAS forecast on Arbitrum.
        Builds the Oracle's Sapience leaderboard track record.
        Only needs ~$0.01 ETH on Arbitrum for gas.

        Returns {success, tx_hash, error}
        """
        if not PRIVATE_KEY:
            return {"success": False, "error": "no private key"}

        try:
            from web3 import Web3
            from eth_account import Account

            # Arbitrum chain ID = 42161
            ARB_RPC   = os.getenv("ARB_RPC_URL", "https://arb1.arbitrum.io/rpc")
            EAS_ADDR  = "0xbD75f629A22Dc1ceD33dDA0b68c546A1c035c458"  # EAS on Arbitrum

            # EAS attest ABI (minimal)
            EAS_ABI = [{
                "inputs": [{
                    "components": [
                        {"name": "schema", "type": "bytes32"},
                        {"components": [
                            {"name": "recipient", "type": "address"},
                            {"name": "expirationTime", "type": "uint64"},
                            {"name": "revocable", "type": "bool"},
                            {"name": "refUID", "type": "bytes32"},
                            {"name": "data", "type": "bytes"},
                            {"name": "value", "type": "uint256"},
                        ], "name": "data", "type": "tuple"},
                    ],
                    "name": "attestationRequest", "type": "tuple"
                }],
                "name": "attest",
                "outputs": [{"type": "bytes32"}],
                "stateMutability": "payable",
                "type": "function"
            }]

            # Sapience forecast schema on Arbitrum
            FORECAST_SCHEMA = os.getenv(
                "SAPIENCE_FORECAST_SCHEMA",
                "0x0000000000000000000000000000000000000000000000000000000000000000"
            )

            w3      = Web3(Web3.HTTPProvider(ARB_RPC))
            account = Account.from_key(PRIVATE_KEY)
            eas     = w3.eth.contract(
                address=Web3.to_checksum_address(EAS_ADDR), abi=EAS_ABI
            )

            # Encode forecast data: resolver (address), conditionId (bytes32),
            # probability (uint8 0-100), comment (string)
            reasoning_trimmed = reasoning[:180]
            data = Web3.solidity_keccak(
                ["address", "bytes32", "uint8", "string"],
                [
                    Web3.to_checksum_address(resolver),
                    bytes.fromhex(condition_id.lstrip("0x")),
                    probability,
                    reasoning_trimmed,
                ]
            )
            # Actually encode as ABI for EAS
            data_encoded = Web3.to_hex(
                Web3.solidity_packed(
                    ["address", "bytes32", "uint8", "string"],
                    [Web3.to_checksum_address(resolver),
                     bytes.fromhex(condition_id.lstrip("0x").zfill(64)),
                     probability,
                     reasoning_trimmed]
                )
            )

            tx = eas.functions.attest({
                "schema": bytes.fromhex(FORECAST_SCHEMA.lstrip("0x").zfill(64)),
                "data": {
                    "recipient":     "0x0000000000000000000000000000000000000000",
                    "expirationTime": 0,
                    "revocable":     True,
                    "refUID":        b"\x00" * 32,
                    "data":          bytes.fromhex(data_encoded.lstrip("0x")),
                    "value":         0,
                }
            }).build_transaction({
                "from":     account.address,
                "nonce":    w3.eth.get_transaction_count(account.address),
                "gas":      200_000,
                "gasPrice": w3.eth.gas_price,
            })
            signed  = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            log.info(f"EAS forecast submitted | prob={probability}% | tx={tx_hash.hex()[:16]}...")
            return {"success": True, "tx_hash": tx_hash.hex()}

        except Exception as e:
            log.warning(f"EAS forecast failed: {e}")
            return {"success": False, "error": str(e)}

    # ── Core analysis → forecast pipeline ────────────────────────────────────

    def process_prophecy(
        self,
        prophecy:      dict,
        token_address: str,
        symbol:        str,
        bankroll:      float = 10.0,
        emotion_read:  dict = None,
    ) -> dict:
        """
        Main entry point. Given an Oracle prophecy:
          1. Search Sapience for relevant markets
          2. Check Polymarket prices for edge
          3. Submit EAS forecast if edge found
          4. Record opportunity for future paid trading

        Returns summary of actions taken.
        """
        if not self.enabled:
            return {"status": "disabled"}

        verdict = str(prophecy.get("verdict", "")).split(" ")[0].upper()
        score   = prophecy.get("score", 5000) // 100

        results = {
            "token":      token_address,
            "symbol":     symbol,
            "verdict":    verdict,
            "score":      score,
            "markets_found":    0,
            "forecasts_submitted": 0,
            "edge_opportunities":  [],
        }

        # Search for related markets using token symbol + common crypto terms
        search_queries = [
            symbol,
            f"{symbol} price",
            f"{symbol} token",
        ]

        all_markets = []
        for q in search_queries:
            markets = self.search_markets(q, limit=5)
            all_markets.extend(markets)

        # Deduplicate by condition ID
        seen = set()
        unique_markets = []
        for m in all_markets:
            if m.get("id") not in seen:
                seen.add(m.get("id"))
                unique_markets.append(m)

        results["markets_found"] = len(unique_markets)
        if not unique_markets:
            log.debug(f"No Sapience markets found for {symbol}")
            return results

        for market in unique_markets[:5]:
            try:
                cid       = market.get("id", "")
                question  = market.get("question", "")
                resolver  = market.get("resolver", "")
                sim_mkts  = (market.get("similarMarkets") or [""])[0] \
                            if isinstance(market.get("similarMarkets"), list) \
                            else market.get("similarMarkets", "")

                # Get Polymarket price
                pm_price = self.get_polymarket_price(sim_mkts)
                if pm_price is None:
                    pm_price = 0.50  # no price data — use 50/50

                # Detect edge — pass emotion read for multiplier
                opp = self.edge.detect_edge(
                    prophecy         = prophecy,
                    market_id        = cid,
                    market_question  = question,
                    market_yes_price = pm_price,
                    token_address    = token_address,
                    bankroll         = bankroll,
                    emotion_read     = emotion_read,
                )

                if not opp or abs(opp.edge) < 0.05:
                    continue

                results["edge_opportunities"].append({
                    "market":           question[:80],
                    "edge":             round(opp.edge, 4),
                    "side":             opp.predicted_outcome,
                    "oracle_p":         opp.oracle_probability,
                    "market_p":         opp.market_price,
                    "kelly":            opp.kelly_size,
                    "actionable":       opp.is_actionable,
                    # Emotional edge layer
                    "market_emotion":   opp.market_emotion,
                    "trade_intuition":  opp.trade_intuition,
                    "conviction":       opp.emotion_conviction,
                    "edge_thesis":      opp.edge_thesis,
                    "key_signal":       opp.key_signal,
                    "emotional_mult":   round(opp.emotional_multiplier, 2),
                })

                # Always submit free EAS forecast
                if opp.is_actionable and resolver and cid:
                    prob_int = int(opp.oracle_probability * 100)
                    if opp.predicted_outcome == "NO":
                        prob_int = 100 - prob_int

                    forecast_result = self.submit_eas_forecast(
                        condition_id = cid,
                        resolver     = resolver,
                        probability  = prob_int,
                        reasoning    = opp.reasoning,
                    )

                    if forecast_result.get("success"):
                        results["forecasts_submitted"] += 1
                        self.edge.record_forecast(opp, forecast_result.get("tx_hash", ""))
                        self.edge.record_revenue("sapience", 0)  # forecast is free
                        log.info(
                            f"Sapience forecast | {symbol} | {opp.predicted_outcome} "
                            f"| edge={opp.edge:+.0%} | market={question[:40]}"
                        )

            except Exception as e:
                log.warning(f"Market processing failed for {market.get('id','?')}: {e}")

        return results

    # ── Leaderboard tracking ──────────────────────────────────────────────────

    def get_leaderboard_rank(self) -> dict:
        """Check Oracle's current Sapience accuracy rank."""
        try:
            gql = """
            query($address: String!) {
                accountAccuracyRank(address: $address) {
                    accuracyScore
                    rank
                    totalForecasters
                }
            }"""
            r = requests.post(
                f"{SAPIENCE_API}/graphql",
                json={"query": gql, "variables": {"address": WALLET_ADDRESS}},
                timeout=8
            )
            if r.ok:
                return r.json().get("data", {}).get("accountAccuracyRank", {})
        except Exception as e:
            log.debug(f"Leaderboard rank: {e}")
        return {}

    def get_open_markets(self, limit: int = 20) -> list:
        """Get current open markets for the dashboard."""
        try:
            gql = """
            query {
                questions(
                    take: 20,
                    resolutionStatus: unresolved,
                    sortField: openInterest,
                    sortDirection: desc
                ) {
                    condition {
                        id question shortName endTime openInterest categoryId
                    }
                }
            }"""
            r = requests.post(f"{SAPIENCE_API}/graphql", json={"query": gql}, timeout=8)
            if r.ok:
                qs = r.json().get("data", {}).get("questions", [])
                return [q["condition"] for q in qs if q.get("condition")]
        except Exception as e:
            log.debug(f"Open markets: {e}")
        return []


_trader = None

def get_sapience_trader() -> SapienceTrader:
    global _trader
    if _trader is None:
        _trader = SapienceTrader()
    return _trader
