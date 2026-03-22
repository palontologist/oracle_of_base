"""
fund_manager.py
---------------
Autonomous fund management for Oracle of Base.

The Oracle backs its own predictions with real capital:
  - Watches for BLESSED tokens it has scored >= ENTRY_MIN_SCORE
  - Buys a small fixed position via Uniswap V3 on Base
  - Tracks every position in Postgres
  - Exits after EXIT_HOURS or if price drops below STOP_LOSS_PCT
  - Records P&L on every close
  - Never risks more than MAX_POSITION_USDC per trade
  - Never holds more than MAX_OPEN_POSITIONS at once

This is proof that the Oracle puts its capital behind its predictions.
Live P&L is queryable at GET /fund/positions and GET /fund/pnl
"""

import os
import json
import time
import logging
import requests
from decimal import Decimal

log = logging.getLogger("fund_manager")

# ── Config (all overridable via env) ─────────────────────────────────────────
BASE_RPC_URL        = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")
PRIVATE_KEY         = os.getenv("AGENT_PRIVATE_KEY", "")
WALLET_ADDRESS      = "0x1EA37E2Fb76Aa396072204C90fcEF88093CEb920"

MAX_POSITION_USDC   = float(os.getenv("MAX_POSITION_USDC",   "2.0"))   # max $ per trade
MAX_OPEN_POSITIONS  = int(os.getenv("MAX_OPEN_POSITIONS",    "3"))     # max concurrent holds
ENTRY_MIN_SCORE     = int(os.getenv("ENTRY_MIN_SCORE",       "75"))    # min Oracle score to buy
EXIT_HOURS          = float(os.getenv("EXIT_HOURS",          "24.0"))  # hold duration
STOP_LOSS_PCT       = float(os.getenv("STOP_LOSS_PCT",       "0.35"))  # exit if down 35%
TAKE_PROFIT_PCT     = float(os.getenv("TAKE_PROFIT_PCT",     "2.0"))   # exit if up 200%
SLIPPAGE_BPS        = int(os.getenv("SLIPPAGE_BPS",          "100"))   # 1% slippage tolerance
FUND_ENABLED        = os.getenv("FUND_ENABLED", "false").lower() == "true"

# Base token addresses
USDC_ADDRESS   = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WETH_ADDRESS   = "0x4200000000000000000000000000000000000006"

# Uniswap V3 SwapRouter02 on Base
UNISWAP_ROUTER = "0x2626664c2603336E57B271c5C0b26F421741e481"

# Uniswap V3 Quoter on Base
UNISWAP_QUOTER = "0x3d4e44Eb1374240CE5F1B136e1fCA3F4d394E8a4"

# ── ABI fragments ─────────────────────────────────────────────────────────────
ERC20_ABI = [
    {"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf",
     "outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],
     "name":"approve","outputs":[{"type":"bool"}],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[],"name":"decimals","outputs":[{"type":"uint8"}],"stateMutability":"view","type":"function"},
]

ROUTER_ABI = [{
    "inputs":[{
        "components":[
            {"name":"tokenIn","type":"address"},
            {"name":"tokenOut","type":"address"},
            {"name":"fee","type":"uint24"},
            {"name":"recipient","type":"address"},
            {"name":"amountIn","type":"uint256"},
            {"name":"amountOutMinimum","type":"uint256"},
            {"name":"sqrtPriceLimitX96","type":"uint160"},
        ],
        "name":"params","type":"tuple"
    }],
    "name":"exactInputSingle",
    "outputs":[{"name":"amountOut","type":"uint256"}],
    "stateMutability":"payable","type":"function"
}]


class FundManager:

    def __init__(self):
        self.enabled = FUND_ENABLED and bool(PRIVATE_KEY)
        if not self.enabled:
            log.info("Fund manager disabled (set FUND_ENABLED=true and AGENT_PRIVATE_KEY to enable)")
            return
        try:
            from web3 import Web3
            from eth_account import Account
            self.w3      = Web3(Web3.HTTPProvider(BASE_RPC_URL))
            self.account = Account.from_key(PRIVATE_KEY)
            self.usdc    = self.w3.eth.contract(
                address=Web3.to_checksum_address(USDC_ADDRESS), abi=ERC20_ABI
            )
            self.router  = self.w3.eth.contract(
                address=Web3.to_checksum_address(UNISWAP_ROUTER), abi=ROUTER_ABI
            )
            log.info(f"Fund manager ready | wallet={WALLET_ADDRESS[:10]}... | max_pos=${MAX_POSITION_USDC}")
        except Exception as e:
            log.error(f"Fund manager init failed: {e}")
            self.enabled = False

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _get_conn(self):
        from prediction_store import get_conn
        return get_conn()

    def _init_tables(self):
        """Create fund tables if they don't exist."""
        try:
            conn = self._get_conn()
            cur  = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS fund_positions (
                    id            SERIAL PRIMARY KEY,
                    token_address TEXT NOT NULL,
                    token_symbol  TEXT,
                    oracle_score  INTEGER,
                    entry_price   NUMERIC(30,10),
                    entry_usdc    NUMERIC(10,4),
                    tokens_bought NUMERIC(30,10),
                    entry_tx      TEXT,
                    status        TEXT DEFAULT 'OPEN',
                    exit_price    NUMERIC(30,10),
                    exit_usdc     NUMERIC(10,4),
                    exit_reason   TEXT,
                    exit_tx       TEXT,
                    pnl_usdc      NUMERIC(10,4),
                    pnl_pct       NUMERIC(8,4),
                    opened_at     TIMESTAMPTZ DEFAULT NOW(),
                    closed_at     TIMESTAMPTZ
                );
                CREATE TABLE IF NOT EXISTS fund_stats (
                    id            SERIAL PRIMARY KEY,
                    total_trades  INTEGER DEFAULT 0,
                    winning_trades INTEGER DEFAULT 0,
                    total_invested NUMERIC(10,4) DEFAULT 0,
                    total_returned NUMERIC(10,4) DEFAULT 0,
                    total_pnl     NUMERIC(10,4) DEFAULT 0,
                    updated_at    TIMESTAMPTZ DEFAULT NOW()
                );
                INSERT INTO fund_stats (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
            """)
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            log.error(f"Table init failed: {e}")

    def _open_positions_count(self) -> int:
        try:
            conn = self._get_conn()
            cur  = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM fund_positions WHERE status = 'OPEN'")
            count = cur.fetchone()[0]
            cur.close(); conn.close()
            return count
        except Exception:
            return 99  # fail safe — don't open more

    def _already_holding(self, token_address: str) -> bool:
        try:
            conn = self._get_conn()
            cur  = conn.cursor()
            cur.execute(
                "SELECT id FROM fund_positions WHERE token_address = %s AND status = 'OPEN'",
                (token_address.lower(),)
            )
            exists = cur.fetchone() is not None
            cur.close(); conn.close()
            return exists
        except Exception:
            return True  # fail safe

    def _save_position(self, token_address: str, symbol: str, oracle_score: int,
                       entry_price: float, entry_usdc: float, tokens_bought: float,
                       entry_tx: str) -> int:
        try:
            conn = self._get_conn()
            cur  = conn.cursor()
            cur.execute("""
                INSERT INTO fund_positions
                    (token_address, token_symbol, oracle_score, entry_price,
                     entry_usdc, tokens_bought, entry_tx)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """, (token_address.lower(), symbol, oracle_score,
                  entry_price, entry_usdc, tokens_bought, entry_tx))
            pos_id = cur.fetchone()[0]
            conn.commit(); cur.close(); conn.close()
            return pos_id
        except Exception as e:
            log.error(f"Save position failed: {e}")
            return -1

    def _close_position(self, pos_id: int, exit_price: float, exit_usdc: float,
                        exit_reason: str, exit_tx: str, pnl: float, pnl_pct: float):
        try:
            conn = self._get_conn()
            cur  = conn.cursor()
            cur.execute("""
                UPDATE fund_positions SET
                    status='CLOSED', exit_price=%s, exit_usdc=%s,
                    exit_reason=%s, exit_tx=%s, pnl_usdc=%s,
                    pnl_pct=%s, closed_at=NOW()
                WHERE id=%s
            """, (exit_price, exit_usdc, exit_reason, exit_tx, pnl, pnl_pct, pos_id))
            cur.execute("""
                UPDATE fund_stats SET
                    total_trades = total_trades + 1,
                    winning_trades = winning_trades + CASE WHEN %s > 0 THEN 1 ELSE 0 END,
                    total_invested = total_invested + (
                        SELECT entry_usdc FROM fund_positions WHERE id=%s
                    ),
                    total_returned = total_returned + %s,
                    total_pnl = total_pnl + %s,
                    updated_at = NOW()
                WHERE id = 1
            """, (pnl, pos_id, exit_usdc, pnl))
            conn.commit(); cur.close(); conn.close()
        except Exception as e:
            log.error(f"Close position failed: {e}")

    # ── Price fetching ─────────────────────────────────────────────────────────

    def _get_token_price_usd(self, token_address: str) -> float | None:
        """Get current USD price from DexScreener."""
        try:
            r = requests.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
                timeout=6
            )
            if not r.ok:
                return None
            pairs = r.json().get("pairs") or []
            if not pairs:
                return None
            # Use most liquid pair
            best = sorted(pairs, key=lambda p: float(
                p.get("liquidity",{}).get("usd", 0) or 0), reverse=True)[0]
            price = best.get("priceUsd")
            return float(price) if price else None
        except Exception:
            return None

    def _get_usdc_balance(self) -> float:
        """USDC balance in the Oracle wallet."""
        if not self.enabled:
            return 0.0
        try:
            from web3 import Web3
            raw = self.usdc.functions.balanceOf(
                Web3.to_checksum_address(WALLET_ADDRESS)
            ).call()
            return raw / 1e6  # USDC has 6 decimals
        except Exception as e:
            log.error(f"USDC balance check failed: {e}")
            return 0.0

    # ── Trade execution ────────────────────────────────────────────────────────

    def _buy_token(self, token_address: str, usdc_amount: float) -> dict:
        """
        Swap USDC → token via Uniswap V3 on Base.
        Returns {success, tx_hash, tokens_out, error}.
        """
        if not self.enabled:
            return {"success": False, "error": "fund manager disabled"}

        try:
            from web3 import Web3

            w3      = self.w3
            account = self.account

            token_addr   = Web3.to_checksum_address(token_address)
            usdc_addr    = Web3.to_checksum_address(USDC_ADDRESS)
            router_addr  = Web3.to_checksum_address(UNISWAP_ROUTER)

            usdc_in_raw  = int(usdc_amount * 1e6)  # USDC has 6 decimals
            min_out      = 0  # set via slippage after quote (simplified)
            deadline     = int(time.time()) + 300  # 5 min

            # Approve USDC spend
            approve_tx = self.usdc.functions.approve(
                router_addr, usdc_in_raw
            ).build_transaction({
                "from":     account.address,
                "nonce":    w3.eth.get_transaction_count(account.address),
                "gas":      100_000,
                "gasPrice": w3.eth.gas_price,
            })
            signed = account.sign_transaction(approve_tx)
            approve_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            w3.eth.wait_for_transaction_receipt(approve_hash, timeout=60)
            log.info(f"USDC approved | tx={approve_hash.hex()[:16]}...")

            # Swap USDC → token (fee tier 3000 = 0.3%)
            swap_tx = self.router.functions.exactInputSingle({
                "tokenIn":            usdc_addr,
                "tokenOut":           token_addr,
                "fee":                3000,
                "recipient":          account.address,
                "amountIn":           usdc_in_raw,
                "amountOutMinimum":   min_out,
                "sqrtPriceLimitX96":  0,
            }).build_transaction({
                "from":     account.address,
                "nonce":    w3.eth.get_transaction_count(account.address),
                "gas":      300_000,
                "gasPrice": w3.eth.gas_price,
                "value":    0,
            })
            signed_swap = account.sign_transaction(swap_tx)
            swap_hash   = w3.eth.send_raw_transaction(signed_swap.raw_transaction)
            receipt     = w3.eth.wait_for_transaction_receipt(swap_hash, timeout=120)

            if receipt.status != 1:
                return {"success": False, "error": "swap reverted", "tx": swap_hash.hex()}

            log.info(f"Buy executed | token={token_address[:10]}... | usdc=${usdc_amount} | tx={swap_hash.hex()[:16]}...")
            return {"success": True, "tx_hash": swap_hash.hex(), "tokens_out": None}

        except Exception as e:
            log.error(f"Buy failed: {e}")
            return {"success": False, "error": str(e)}

    def _sell_token(self, token_address: str, token_amount: float) -> dict:
        """Swap token → USDC via Uniswap V3."""
        if not self.enabled:
            return {"success": False, "error": "fund manager disabled"}

        try:
            from web3 import Web3
            from eth_account import Account

            w3      = self.w3
            account = self.account

            token_addr  = Web3.to_checksum_address(token_address)
            usdc_addr   = Web3.to_checksum_address(USDC_ADDRESS)
            router_addr = Web3.to_checksum_address(UNISWAP_ROUTER)

            # Get token decimals
            token_contract = w3.eth.contract(address=token_addr, abi=ERC20_ABI)
            decimals       = token_contract.functions.decimals().call()
            amount_in_raw  = int(token_amount * (10 ** decimals))

            # Approve token spend
            approve_tx = token_contract.functions.approve(
                router_addr, amount_in_raw
            ).build_transaction({
                "from":     account.address,
                "nonce":    w3.eth.get_transaction_count(account.address),
                "gas":      100_000,
                "gasPrice": w3.eth.gas_price,
            })
            signed = account.sign_transaction(approve_tx)
            ah     = w3.eth.send_raw_transaction(signed.raw_transaction)
            w3.eth.wait_for_transaction_receipt(ah, timeout=60)

            # Swap token → USDC
            swap_tx = self.router.functions.exactInputSingle({
                "tokenIn":           token_addr,
                "tokenOut":          usdc_addr,
                "fee":               3000,
                "recipient":         account.address,
                "amountIn":          amount_in_raw,
                "amountOutMinimum":  0,
                "sqrtPriceLimitX96": 0,
            }).build_transaction({
                "from":     account.address,
                "nonce":    w3.eth.get_transaction_count(account.address),
                "gas":      300_000,
                "gasPrice": w3.eth.gas_price,
                "value":    0,
            })
            signed_swap = account.sign_transaction(swap_tx)
            swap_hash   = w3.eth.send_raw_transaction(signed_swap.raw_transaction)
            receipt     = w3.eth.wait_for_transaction_receipt(swap_hash, timeout=120)

            if receipt.status != 1:
                return {"success": False, "error": "sell reverted", "tx": swap_hash.hex()}

            # Get new USDC balance to compute actual return
            new_usdc = self._get_usdc_balance()
            log.info(f"Sell executed | token={token_address[:10]}... | tx={swap_hash.hex()[:16]}...")
            return {"success": True, "tx_hash": swap_hash.hex(), "usdc_received": None}

        except Exception as e:
            log.error(f"Sell failed: {e}")
            return {"success": False, "error": str(e)}

    # ── Main decision loop ────────────────────────────────────────────────────

    def consider_entry(self, token_address: str, oracle_score: int,
                       verdict: str, symbol: str = "") -> dict:
        """
        Called after each Oracle prediction.
        Decides whether to open a position based on score + risk limits.
        """
        if not self.enabled:
            return {"action": "skipped", "reason": "fund_disabled"}

        self._init_tables()

        # Only buy BLESSED tokens above score threshold
        verdict_clean = verdict.split(" ")[0].upper()
        if verdict_clean != "BLESSED":
            return {"action": "skip", "reason": f"verdict={verdict_clean} not BLESSED"}

        if oracle_score < ENTRY_MIN_SCORE:
            return {"action": "skip", "reason": f"score={oracle_score} below min={ENTRY_MIN_SCORE}"}

        if self._open_positions_count() >= MAX_OPEN_POSITIONS:
            return {"action": "skip", "reason": f"max_positions={MAX_OPEN_POSITIONS} reached"}

        if self._already_holding(token_address):
            return {"action": "skip", "reason": "already_holding"}

        # Check USDC balance
        usdc_bal = self._get_usdc_balance()
        if usdc_bal < MAX_POSITION_USDC:
            return {"action": "skip", "reason": f"insufficient_usdc=${usdc_bal:.2f}"}

        # Get entry price
        entry_price = self._get_token_price_usd(token_address)
        if not entry_price or entry_price <= 0:
            return {"action": "skip", "reason": "price_unavailable"}

        # Execute buy
        log.info(f"Opening position | {symbol} | score={oracle_score} | ${MAX_POSITION_USDC} USDC")
        result = self._buy_token(token_address, MAX_POSITION_USDC)

        if not result["success"]:
            return {"action": "failed", "error": result.get("error")}

        tokens_bought = MAX_POSITION_USDC / entry_price
        pos_id = self._save_position(
            token_address, symbol, oracle_score,
            entry_price, MAX_POSITION_USDC,
            tokens_bought, result["tx_hash"]
        )

        return {
            "action":        "bought",
            "position_id":   pos_id,
            "token":         token_address,
            "symbol":        symbol,
            "oracle_score":  oracle_score,
            "entry_price":   entry_price,
            "usdc_spent":    MAX_POSITION_USDC,
            "tokens_bought": tokens_bought,
            "tx":            result["tx_hash"],
        }

    def run_exit_checks(self):
        """
        Check all open positions for exit conditions:
          - Held longer than EXIT_HOURS → time exit
          - Price dropped below STOP_LOSS_PCT → stop loss
          - Price above TAKE_PROFIT_PCT → take profit
        Run this from the resolution scheduler loop.
        """
        if not self.enabled:
            return

        self._init_tables()

        try:
            conn = self._get_conn()
            cur  = conn.cursor()
            cur.execute("""
                SELECT id, token_address, token_symbol, entry_price,
                       entry_usdc, tokens_bought,
                       EXTRACT(EPOCH FROM (NOW() - opened_at))/3600 AS hours_held
                FROM fund_positions
                WHERE status = 'OPEN'
            """)
            positions = cur.fetchall()
            cur.close(); conn.close()
        except Exception as e:
            log.error(f"Exit check query failed: {e}")
            return

        for pos in positions:
            pos_id, token_addr, symbol, entry_price, entry_usdc, tokens_bought, hours_held = pos
            entry_price  = float(entry_price)
            entry_usdc   = float(entry_usdc)
            tokens_bought = float(tokens_bought)
            hours_held   = float(hours_held)

            current_price = self._get_token_price_usd(token_addr)
            if not current_price:
                continue

            price_chg = (current_price - entry_price) / entry_price
            current_val = tokens_bought * current_price

            exit_reason = None
            if hours_held >= EXIT_HOURS:
                exit_reason = f"time_exit_{EXIT_HOURS}h"
            elif price_chg <= -STOP_LOSS_PCT:
                exit_reason = f"stop_loss_{price_chg*100:.1f}pct"
            elif price_chg >= TAKE_PROFIT_PCT:
                exit_reason = f"take_profit_{price_chg*100:.1f}pct"

            if exit_reason:
                log.info(f"Exiting {symbol} | reason={exit_reason} | pnl=${current_val-entry_usdc:.4f}")
                result = self._sell_token(token_addr, tokens_bought)

                pnl     = current_val - entry_usdc
                pnl_pct = price_chg * 100
                self._close_position(
                    pos_id, current_price, current_val,
                    exit_reason,
                    result.get("tx_hash", "pending"),
                    pnl, pnl_pct
                )

    # ── Stats ─────────────────────────────────────────────────────────────────

    def get_positions(self, status: str = "all") -> list:
        """Return positions for the API."""
        self._init_tables()
        try:
            conn = self._get_conn()
            cur  = conn.cursor()
            if status == "open":
                cur.execute("""
                    SELECT id, token_address, token_symbol, oracle_score,
                           entry_price, entry_usdc, tokens_bought, entry_tx,
                           status, opened_at
                    FROM fund_positions WHERE status='OPEN'
                    ORDER BY opened_at DESC
                """)
            else:
                cur.execute("""
                    SELECT id, token_address, token_symbol, oracle_score,
                           entry_price, entry_usdc, tokens_bought, entry_tx,
                           status, exit_price, exit_usdc, exit_reason,
                           exit_tx, pnl_usdc, pnl_pct, opened_at, closed_at
                    FROM fund_positions ORDER BY opened_at DESC LIMIT 50
                """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.close(); conn.close()
            # Enrich open positions with current price
            for row in rows:
                if row.get("status") == "OPEN":
                    cp = self._get_token_price_usd(row["token_address"])
                    if cp and row.get("tokens_bought"):
                        row["current_price"]   = cp
                        row["current_value"]   = float(row["tokens_bought"]) * cp
                        row["unrealised_pnl"]  = row["current_value"] - float(row["entry_usdc"])
                        row["unrealised_pct"]  = (
                            (cp - float(row["entry_price"])) / float(row["entry_price"]) * 100
                            if row.get("entry_price") else 0
                        )
            return rows
        except Exception as e:
            log.error(f"get_positions: {e}")
            return []

    def get_pnl_summary(self) -> dict:
        """Overall fund P&L summary."""
        self._init_tables()
        try:
            conn = self._get_conn()
            cur  = conn.cursor()
            cur.execute("SELECT * FROM fund_stats WHERE id=1")
            row = cur.fetchone()
            cols = [d[0] for d in cur.description]
            stats = dict(zip(cols, row)) if row else {}
            # Add open unrealised P&L
            cur.execute("""
                SELECT COUNT(*) as open_count FROM fund_positions WHERE status='OPEN'
            """)
            stats["open_positions"] = cur.fetchone()[0]
            cur.close(); conn.close()
            # Current USDC balance
            stats["usdc_balance"]  = self._get_usdc_balance() if self.enabled else 0
            stats["fund_enabled"]  = self.enabled
            stats["max_position"]  = MAX_POSITION_USDC
            stats["entry_min_score"] = ENTRY_MIN_SCORE
            return stats
        except Exception as e:
            log.error(f"get_pnl_summary: {e}")
            return {"error": str(e), "fund_enabled": self.enabled}


# Singleton
_fund_manager = None

def get_fund_manager() -> FundManager:
    global _fund_manager
    if _fund_manager is None:
        _fund_manager = FundManager()
    return _fund_manager
