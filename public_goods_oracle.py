"""
public_goods_oracle.py
-----------------------
Legitimacy analysis for public goods project teams.
Built for Octant-style evaluation rounds.

Collects signals across four dimensions:
  1. On-chain wallet history (Base + Ethereum)
  2. GitHub activity (commit history, contributors, recency)
  3. Grant history (Gitcoin rounds, delivery record)
  4. Social presence (Farcaster profile + network quality)

Feeds everything to Venice AI with no scoring rubric.
Venice reasons freely about team legitimacy, Sybil risk, and delivery credibility.

Endpoints:
  GET /public-goods-check?wallet=0x...&github=org/repo&handle=farcaster_handle
  GET /public-goods-feed     (latest evaluations, free)
"""

import os
import json
import time
from utils.ens import enrich_address, resolve_ens
import hashlib
import logging
import requests
from datetime import datetime, timezone

log = logging.getLogger("public_goods_oracle")

VENICE_API_KEY = os.getenv("VENICE_API_KEY", "")
VENICE_MODEL   = os.getenv("VENICE_MODEL", "qwen3-5-9b")
BASE_RPC_URL   = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")
ETH_RPC_URL    = os.getenv("ETH_RPC_URL",  "https://eth.llamarpc.com")
GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "")   # optional — increases rate limit


class PublicGoodsOracle:

    def __init__(self, agent_id: str):
        self.agent_id = agent_id

    # ── On-chain signals ──────────────────────────────────────────────────────

    def _detect_address_type(self, address: str, base_code: str) -> str:
        """
        Classify an address into one of four types:
          eoa_wallet     — externally owned account (person/team wallet)
          token_contract — ERC-20 or ERC-721 token
          defi_contract  — protocol, vault, pool, bridge, precompile
          generic_contract — deployed contract, purpose unclear
        """
        if base_code in ("0x", "0x0", None, ""):
            return "eoa_wallet"

        # Known Base precompiles (0x4200...xxxx)
        if address.lower().startswith("0x4200000000000000000000000000000000"):
            return "defi_contract"

        # Try DexScreener — if it has pairs it's a token
        try:
            r = requests.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{address}",
                timeout=6
            )
            if r.ok:
                pairs = r.json().get("pairs") or []
                if pairs:
                    return "token_contract"
        except Exception:
            pass

        # ERC-20 detection via name() / symbol() / totalSupply() function selectors
        def eth_call(selector: str) -> str:
            try:
                r = requests.post(BASE_RPC_URL, json={
                    "jsonrpc": "2.0", "method": "eth_call",
                    "params": [{"to": address, "data": selector}, "latest"],
                    "id": 1
                }, timeout=5)
                return r.json().get("result", "0x") if r.ok else "0x"
            except Exception:
                return "0x"

        name_result   = eth_call("0x06fdde03")  # name()
        symbol_result = eth_call("0x95d89b41")  # symbol()
        supply_result = eth_call("0x18160ddd")  # totalSupply()

        has_name   = len(name_result) > 66
        has_symbol = len(symbol_result) > 66
        has_supply = supply_result not in ("0x", "0x0", "0x" + "0"*64, None)

        if (has_name or has_symbol) and has_supply:
            return "token_contract"

        return "generic_contract"

    def _collect_token_signals(self, address: str) -> dict:
        """
        Rich token/ICO signals.
        Covers market health, holder distribution, contract structure,
        treasury behaviour, and tokenomics — the signals that tell you
        whether this is a real project or an exit vehicle.
        """
        signals = {"address_type": "token_contract", "address": address}

        # ── DexScreener: market data ──────────────────────────────────────────
        try:
            r = requests.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{address}",
                timeout=8
            )
            if r.ok:
                pairs = r.json().get("pairs") or []
                if pairs:
                    total_liq     = sum(float(p.get("liquidity",{}).get("usd",0) or 0) for p in pairs)
                    total_vol_24h = sum(float(p.get("volume",{}).get("h24",0) or 0) for p in pairs)
                    total_buys    = sum(p.get("txns",{}).get("h24",{}).get("buys",0) or 0 for p in pairs)
                    total_sells   = sum(p.get("txns",{}).get("h24",{}).get("sells",0) or 0 for p in pairs)
                    total_txns    = total_buys + total_sells

                    main  = sorted(pairs, key=lambda p: float(p.get("liquidity",{}).get("usd",0) or 0), reverse=True)[0]
                    token = main.get("baseToken", {})
                    name  = token.get("name", "")
                    sym   = token.get("symbol", "")
                    fdv   = float(main.get("fdv", 0) or 0)
                    mcap  = float(main.get("marketCap", 0) or 0)
                    pc    = main.get("priceChange", {})
                    age_ms = main.get("pairCreatedAt", 0) or 0
                    age_days = round((time.time()*1000 - age_ms) / (1000*86400), 1) if age_ms else None
                    price_usd = main.get("priceUsd")

                    # Liquidity / FDV ratio — key tokenomics health signal
                    liq_fdv_ratio = round(total_liq / fdv, 4) if fdv > 0 else None

                    # Buy pressure
                    buy_pressure = round(total_buys / max(total_txns, 1), 3)

                    signals.update({
                        "token_name":          name,
                        "token_symbol":        sym,
                        "price_usd":           price_usd,
                        "market_cap_usd":      round(mcap, 0) if mcap else None,
                        "fdv_usd":             round(fdv, 0),
                        "total_liquidity_usd": round(total_liq, 2),
                        "volume_24h_usd":      round(total_vol_24h, 2),
                        "buys_24h":            total_buys,
                        "sells_24h":           total_sells,
                        "buy_pressure":        buy_pressure,
                        "pair_count":          len(pairs),
                        "age_days":            age_days,
                        "price_change_1h_pct": pc.get("h1"),
                        "price_change_24h_pct": pc.get("h24"),
                        "price_change_7d_pct": pc.get("d7") if hasattr(pc, "get") else None,
                        "liq_to_fdv_ratio":    liq_fdv_ratio,
                        # Interpretation signals for Venice
                        "liquidity_health": (
                            "deep ($1M+)"         if total_liq > 1_000_000
                            else "good ($100k+)"  if total_liq > 100_000
                            else "thin ($10k+)"   if total_liq > 10_000
                            else "very thin (<$10k)"
                        ),
                        "maturity": (
                            "established (6m+)" if age_days and age_days > 180
                            else "maturing (1-6m)" if age_days and age_days > 30
                            else "new (<30d)"   if age_days and age_days > 3
                            else "brand new (<3d)"
                        ),
                        "tokenomics_signal": (
                            "healthy — liquidity well-backed relative to FDV"
                                if liq_fdv_ratio and liq_fdv_ratio > 0.05
                            else "stretched — low liquidity vs FDV, exit risk"
                                if liq_fdv_ratio and liq_fdv_ratio < 0.01
                            else "moderate"
                        ),
                    })
        except Exception as e:
            signals["dexscreener_error"] = str(e)

        # ── Basescan: holder count + top holders ─────────────────────────────
        try:
            r = requests.get(
                f"https://api.basescan.org/api"
                f"?module=token&action=tokenholderlist"
                f"&contractaddress={address}&page=1&offset=10",
                timeout=8
            )
            if r.ok:
                result = r.json().get("result", [])
                if isinstance(result, list) and result:
                    # Top holder concentration
                    quantities = []
                    for h in result[:10]:
                        try:
                            quantities.append(int(h.get("TokenHolderQuantity", 0)))
                        except Exception:
                            pass

                    if quantities:
                        total_supply_in_top10 = sum(quantities)
                        top1_pct  = round(quantities[0] / max(total_supply_in_top10, 1) * 100, 1)
                        top3_pct  = round(sum(quantities[:3]) / max(total_supply_in_top10, 1) * 100, 1)
                        signals["top_holders"] = {
                            "top1_concentration_pct":  top1_pct,
                            "top3_concentration_pct":  top3_pct,
                            "concentration_signal": (
                                "highly concentrated — top holder controls majority"
                                    if top1_pct > 50
                                else "concentrated — top 3 control majority"
                                    if top3_pct > 60
                                else "moderately distributed"
                                    if top3_pct > 30
                                else "well distributed"
                            ),
                        }
        except Exception as e:
            log.debug(f"Holder list: {e}")

        # ── Basescan: token transfer activity ────────────────────────────────
        try:
            r = requests.get(
                f"https://api.basescan.org/api"
                f"?module=account&action=tokentx"
                f"&contractaddress={address}&page=1&offset=20&sort=desc",
                timeout=8
            )
            if r.ok:
                txs = r.json().get("result", [])
                if isinstance(txs, list) and txs:
                    unique_wallets = len(set([t.get("to","") for t in txs] + [t.get("from","") for t in txs]))
                    signals["transfer_activity"] = {
                        "recent_transfers":    len(txs),
                        "unique_wallets":      unique_wallets,
                        "last_transfer_age":   txs[0].get("timeStamp") if txs else None,
                        "activity_note": (
                            "active trading — many wallets transacting"
                                if unique_wallets > 15
                            else "moderate activity"
                                if unique_wallets > 5
                            else "low transfer activity — few wallets"
                        ),
                    }
        except Exception as e:
            log.debug(f"Token transfers: {e}")

        # ── Basescan: verified contract source ───────────────────────────────
        try:
            r = requests.get(
                f"https://api.basescan.org/api"
                f"?module=contract&action=getsourcecode"
                f"&address={address}",
                timeout=8
            )
            if r.ok:
                res = r.json().get("result", [{}])
                if res and isinstance(res, list):
                    src = res[0]
                    cname     = src.get("ContractName","")
                    verified  = bool(src.get("SourceCode",""))
                    proxy     = src.get("Proxy","0") == "1"
                    compiler  = src.get("CompilerVersion","")
                    abi_str   = src.get("ABI","")

                    # Check for common ICO/token patterns in contract name
                    cname_lower = cname.lower()
                    is_governance = any(w in cname_lower for w in ["govern","dao","vote","proposal"])
                    is_vesting    = any(w in cname_lower for w in ["vest","lock","timelock","cliff"])
                    is_multisig   = any(w in cname_lower for w in ["multisig","gnosis","safe"])
                    is_standard   = any(w in cname_lower for w in ["erc20","token","coin"])

                    if cname or verified:
                        signals["contract_info"] = {
                            "name":            cname,
                            "source_verified": verified,
                            "is_proxy":        proxy,
                            "compiler":        compiler,
                            "is_governance":   is_governance,
                            "is_vesting":      is_vesting,
                            "is_multisig":     is_multisig,
                            "is_standard_token": is_standard,
                            "transparency_note": (
                                "source verified on Basescan — full transparency"
                                    if verified
                                else "source NOT verified — cannot inspect code"
                            ),
                        }
        except Exception as e:
            log.debug(f"Contract source: {e}")

        # ── CoinGecko: broader market data + project metadata ────────────────
        try:
            r = requests.get(
                f"https://api.coingecko.com/api/v3/coins/base/contract/{address}",
                timeout=8
            )
            if r.ok:
                cg = r.json()
                desc = cg.get("description", {}).get("en", "")
                links = cg.get("links", {})
                community = cg.get("community_data", {})
                dev_data  = cg.get("developer_data", {})

                signals["coingecko"] = {
                    "name":            cg.get("name"),
                    "symbol":          cg.get("symbol"),
                    "description":     (desc[:300] + "...") if len(desc) > 300 else desc,
                    "homepage":        (links.get("homepage") or [""])[0],
                    "github_repos":    links.get("repos_url", {}).get("github", [])[:2],
                    "twitter_handle":  links.get("twitter_screen_name"),
                    "telegram":        links.get("telegram_channel_identifier"),
                    "coingecko_score": cg.get("coingecko_score"),
                    "developer_score": cg.get("developer_score"),
                    "community_score": cg.get("community_score"),
                    "twitter_followers": community.get("twitter_followers"),
                    "github_stars":    dev_data.get("stars"),
                    "github_forks":    dev_data.get("forks"),
                    "github_commits_4w": dev_data.get("commit_count_4_weeks"),
                    "categories":      cg.get("categories", [])[:5],
                    "genesis_date":    cg.get("genesis_date"),
                    "ico_data":        cg.get("ico_data"),  # ICO raise info if available
                    "market_cap_rank": cg.get("market_cap_rank"),
                    "sentiment_up_pct": cg.get("sentiment_votes_up_percentage"),
                }
        except Exception as e:
            log.debug(f"CoinGecko: {e}")

        # ── DeFiLlama: TVL if it's a DeFi protocol token ─────────────────────
        try:
            sym = signals.get("token_symbol","").lower()
            if sym:
                r = requests.get(
                    f"https://api.llama.fi/search?query={sym}",
                    timeout=6
                )
                if r.ok:
                    results = r.json()
                    if results:
                        match = results[0]
                        signals["defillama"] = {
                            "protocol_name": match.get("name"),
                            "tvl_usd":       match.get("tvl"),
                            "chains":        match.get("chains",[])[0:3],
                            "category":      match.get("category"),
                        }
        except Exception:
            pass

        # ── ENS reverse lookup for token contract ─────────────────────────────
        try:
            ens = enrich_address(address)
            if ens.get("has_ens"):
                signals["ens"] = ens
        except Exception:
            pass

        return signals

    def _collect_wallet_signals(self, address: str) -> dict:
        """
        On-chain signals for any address type.

        Detects address type first:
          - eoa_wallet: use tx count (nonce) — valid for EOAs
          - token_contract: use DexScreener for liquidity/volume/age
          - defi_contract: use bytecode size + balance + mainnet presence
          - generic_contract: use bytecode size + balance

        NEVER use eth_getTransactionCount on contracts — it always returns 0
        (contracts don't have a nonce) and will cause incorrect low scoring.
        """
        if not address or not address.startswith("0x"):
            return {"error": "invalid address", "address": address}

        def rpc_call(rpc_url: str, method: str, params: list) -> str:
            try:
                r = requests.post(rpc_url, json={
                    "jsonrpc": "2.0", "method": method, "params": params, "id": 1
                }, timeout=8)
                return r.json().get("result", "0x0") if r.ok else "0x0"
            except Exception:
                return "0x0"

        ens          = enrich_address(address)
        base_code    = rpc_call(BASE_RPC_URL, "eth_getCode",    [address, "latest"])
        base_balance = int(rpc_call(BASE_RPC_URL, "eth_getBalance", [address, "latest"]), 16)
        base_eth     = round(base_balance / 1e18, 6)
        addr_type    = self._detect_address_type(address, base_code)

        base = {
            "address":      address,
            "address_type": addr_type,
            "ens_name":     ens["ens_name"],
            "ens_signal":   ens["ens_signal"],
        }

        # ── Token contract ────────────────────────────────────────────────────
        if addr_type == "token_contract":
            token_sigs = self._collect_token_signals(address)
            return {**base, **token_sigs}

        # ── DeFi protocol / precompile ────────────────────────────────────────
        if addr_type in ("defi_contract", "generic_contract"):
            code_bytes  = len(base_code) // 2 if base_code else 0
            eth_balance = int(rpc_call(ETH_RPC_URL, "eth_getBalance", [address, "latest"]), 16)
            eth_eth     = round(eth_balance / 1e18, 6)
            eth_code    = rpc_call(ETH_RPC_URL, "eth_getCode", [address, "latest"])
            on_mainnet  = eth_code not in ("0x", "0x0", None, "")

            # Basescan tx count (best effort, no API key needed for page 1)
            tx_count_signal = None
            try:
                r = requests.get(
                    f"https://api.basescan.org/api?module=account&action=txlist"
                    f"&address={address}&startblock=0&endblock=99999999"
                    f"&page=1&offset=5&sort=desc",
                    timeout=6
                )
                if r.ok and r.json().get("status") == "1":
                    tx_count_signal = f"active — {len(r.json().get('result',[]))} recent txns visible"
            except Exception:
                pass

            contract_data = {
                **base,
                "code_size_bytes":     code_bytes,
                "base_balance_eth":    base_eth,
                "eth_balance_eth":     eth_eth,
                "deployed_on_mainnet": on_mainnet,
                "tx_count_signal":     tx_count_signal,
                "activity_level": (
                    "large_protocol"   if code_bytes > 10000
                    else "mid_protocol"   if code_bytes > 3000
                    else "small_contract" if code_bytes > 500
                    else "minimal_contract"
                ),
                "note": (
                    f"Deployed contract ({addr_type}). Bytecode: {code_bytes} bytes. "
                    f"Balance: {base_eth} ETH on Base, {eth_eth} ETH on mainnet. "
                    f"{'Also deployed on Ethereum mainnet.' if on_mainnet else 'Base-only.'}"
                ),
            }
            try:
                rich = self._fetch_rich_address_metadata(address)
                contract_data.update({k: v for k, v in rich.items() if k != "address"})
            except Exception as e:
                log.debug(f"Rich contract metadata: {e}")
            return contract_data

        # ── EOA wallet ────────────────────────────────────────────────────────
        base_tx  = int(rpc_call(BASE_RPC_URL, "eth_getTransactionCount", [address, "latest"]), 16)
        eth_bal  = int(rpc_call(ETH_RPC_URL,  "eth_getBalance",          [address, "latest"]), 16)
        eth_tx   = int(rpc_call(ETH_RPC_URL,  "eth_getTransactionCount", [address, "latest"]), 16)
        total_tx = base_tx + eth_tx

        wallet = {
            **base,
            "base_tx_count":        base_tx,
            "eth_tx_count":         eth_tx,
            "total_tx_count":       total_tx,
            "base_balance_eth":     base_eth,
            "eth_balance_eth":      round(eth_bal / 1e18, 6),
            "activity_level": (
                "highly_active" if total_tx > 500
                else "active"   if total_tx > 100
                else "moderate" if total_tx > 20
                else "light"    if total_tx > 3
                else "minimal"
            ),
            "cross_chain_presence": eth_tx > 0,
            "has_mainnet_history":  eth_tx > 10,
        }

        # Enrich with rich on-chain metadata
        try:
            rich = self._fetch_rich_address_metadata(address)
            wallet.update({k: v for k, v in rich.items() if k != "address"})
        except Exception as e:
            log.debug(f"Rich metadata fetch failed: {e}")

        return wallet

    def _fetch_rich_address_metadata(self, address: str) -> dict:
        """
        Pull every useful signal about an address from free public APIs.
        Goal: give Venice enough to reason about this address as if it
        were a human analyst with 5 minutes and a browser.
        """
        meta = {"address": address}

        # ── Basescan: transaction history ─────────────────────────────────────
        try:
            # Normal transactions (last 10)
            r = requests.get(
                f"https://api.basescan.org/api"
                f"?module=account&action=txlist"
                f"&address={address}&startblock=0&endblock=99999999"
                f"&page=1&offset=10&sort=desc",
                timeout=8
            )
            if r.ok:
                data = r.json()
                txs = data.get("result", [])
                if isinstance(txs, list) and txs:
                    meta["recent_txs"] = len(txs)
                    meta["first_tx_at"] = txs[-1].get("timeStamp")
                    meta["last_tx_at"]  = txs[0].get("timeStamp")
                    meta["tx_types"] = list(set([
                        "contract_deploy" if tx.get("contractAddress") else
                        "contract_call"   if tx.get("input","0x") != "0x" else
                        "eth_transfer"
                        for tx in txs[:5]
                    ]))

                    # What contracts has this address interacted with?
                    contracts_called = list(set([
                        tx.get("to","")[:10]+"..."
                        for tx in txs
                        if tx.get("input","0x") != "0x" and tx.get("to")
                    ]))[:5]
                    if contracts_called:
                        meta["contracts_interacted"] = contracts_called

                    # Contracts deployed by this address
                    deployed = [tx.get("contractAddress") for tx in txs if tx.get("contractAddress")]
                    if deployed:
                        meta["contracts_deployed"] = deployed[:5]
                        meta["has_deployed_contracts"] = True
        except Exception as e:
            log.debug(f"Basescan tx fetch: {e}")

        # ── Basescan: ERC-20 token holdings ──────────────────────────────────
        try:
            r = requests.get(
                f"https://api.basescan.org/api"
                f"?module=account&action=tokentx"
                f"&address={address}&page=1&offset=10&sort=desc",
                timeout=8
            )
            if r.ok:
                data = r.json()
                txs  = data.get("result", [])
                if isinstance(txs, list) and txs:
                    tokens_held = list(set([
                        tx.get("tokenSymbol", "")
                        for tx in txs
                        if tx.get("tokenSymbol")
                    ]))[:10]
                    meta["token_interactions"] = tokens_held
                    meta["defi_active"] = any(
                        t in ["USDC","USDT","WETH","DAI","cbETH","cbBTC"]
                        for t in tokens_held
                    )
        except Exception as e:
            log.debug(f"Basescan token fetch: {e}")

        # ── If it's a contract: get verified source info ─────────────────────
        if meta.get("has_deployed_contracts") or meta.get("address_type") == "generic_contract":
            try:
                r = requests.get(
                    f"https://api.basescan.org/api"
                    f"?module=contract&action=getsourcecode"
                    f"&address={address}",
                    timeout=8
                )
                if r.ok:
                    result = r.json().get("result", [{}])
                    if result and isinstance(result, list):
                        src = result[0]
                        contract_name = src.get("ContractName","")
                        compiler      = src.get("CompilerVersion","")
                        verified      = bool(src.get("SourceCode",""))
                        if contract_name:
                            meta["contract_name"] = contract_name
                            meta["source_verified"] = verified
                            meta["compiler"] = compiler
            except Exception as e:
                log.debug(f"Contract source fetch: {e}")

        # ── DeFiLlama: protocol check ─────────────────────────────────────────
        try:
            r = requests.get(
                "https://api.llama.fi/protocols",
                timeout=8
            )
            if r.ok:
                protocols = r.json()
                # Check if this address appears in any known protocol
                addr_lower = address.lower()
                for p in protocols[:500]:  # check top 500
                    chains = p.get("chains", [])
                    addr_in_protocol = any(
                        addr_lower in str(p.get("address","")).lower() or
                        addr_lower in str(p.get("forkedFrom","")).lower()
                        for _ in [1]
                    )
                    if addr_in_protocol:
                        meta["defillama_protocol"] = {
                            "name":     p.get("name"),
                            "category": p.get("category"),
                            "tvl":      p.get("tvl"),
                            "chains":   chains[:3],
                        }
                        break
        except Exception as e:
            log.debug(f"DeFiLlama check: {e}")

        # ── NFT holdings (OpenSea Base) ───────────────────────────────────────
        try:
            r = requests.get(
                f"https://api.opensea.io/api/v2/chain/base/account/{address}/nfts"
                f"?limit=5",
                headers={"accept": "application/json"},
                timeout=6
            )
            if r.ok:
                nfts = r.json().get("nfts", [])
                if nfts:
                    collections = list(set([n.get("collection","") for n in nfts if n.get("collection")]))[:5]
                    meta["nft_collections"] = collections
                    meta["has_nfts"] = True
        except Exception:
            pass

        # ── Coinbase Verifications (on-chain identity) ────────────────────────
        try:
            # Coinbase Verified ID is an EAS attestation on Base
            # Check via Base name service (basename)
            r = requests.get(
                f"https://api.basename.app/v1/address/{address}",
                timeout=6
            )
            if r.ok:
                data = r.json()
                basename = data.get("name")
                if basename:
                    meta["basename"] = basename
                    meta["has_basename"] = True
        except Exception:
            pass

        # ── Gitcoin Passport score ────────────────────────────────────────────
        try:
            r = requests.get(
                f"https://api.scorer.gitcoin.co/ceramic-cache/stamp/{address}",
                timeout=6
            )
            if r.ok:
                stamps = r.json()
                if stamps:
                    meta["gitcoin_stamps"] = len(stamps) if isinstance(stamps, list) else 1
        except Exception:
            pass

        return meta

    def _collect_contributor_signals(self, addresses: list[str]) -> dict:
        """
        Check multiple contributor wallets for Sybil cluster patterns.
        Red flags: all wallets new, all appeared at same time, no independent history.
        """
        if not addresses:
            return {"contributors_checked": 0}

        results = []
        for addr in addresses[:5]:   # cap at 5 to keep it fast
            try:
                tx = int(requests.post(BASE_RPC_URL, json={
                    "jsonrpc": "2.0", "method": "eth_getTransactionCount",
                    "params": [addr, "latest"], "id": 1
                }, timeout=6).json().get("result", "0x0"), 16)
                results.append({"address": addr[:10] + "...", "tx_count": tx, "is_new": tx < 5})
            except Exception:
                results.append({"address": addr[:10] + "...", "tx_count": 0, "is_new": True})

        new_count    = sum(1 for r in results if r["is_new"])
        sybil_signal = new_count >= max(2, len(results) * 0.6)

        return {
            "contributors_checked": len(results),
            "new_wallet_count":     new_count,
            "sybil_cluster_signal": sybil_signal,
            "details":              results,
            "note": (
                "Multiple contributors have minimal on-chain history — possible Sybil cluster"
                if sybil_signal else
                "Contributors show independent on-chain histories"
            ),
        }

    # ── GitHub signals ────────────────────────────────────────────────────────

    def _collect_github_signals(self, github_handle: str) -> dict:
        """
        GitHub activity signals for a org or username.
        Checks recent commit activity, contributor count, repo age, issue engagement.
        """
        if not github_handle:
            return {"provided": False}

        handle  = github_handle.lstrip("@").strip("/")
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "OracleOfBase/1.0"}
        if GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

        signals = {"handle": handle, "provided": True}

        try:
            # Is this a user or org?
            r = requests.get(f"https://api.github.com/users/{handle}", headers=headers, timeout=8)
            if r.status_code == 404:
                return {"provided": True, "exists": False, "handle": handle}

            user_data = r.json()
            signals.update({
                "exists":        True,
                "type":          user_data.get("type", "User"),
                "public_repos":  user_data.get("public_repos", 0),
                "followers":     user_data.get("followers", 0),
                "created_at":    user_data.get("created_at", ""),
                "bio":           user_data.get("bio", ""),
                "location":      user_data.get("location", ""),
                "blog":          user_data.get("blog", ""),
            })

            # Account age
            if user_data.get("created_at"):
                created = datetime.fromisoformat(user_data["created_at"].replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - created).days
                signals["account_age_days"] = age_days
                signals["account_age_signal"] = (
                    "established" if age_days > 365
                    else "growing"  if age_days > 90
                    else "new"
                )

            # Recent repos
            repos_r = requests.get(
                f"https://api.github.com/users/{handle}/repos?sort=updated&per_page=5",
                headers=headers, timeout=8
            )
            if repos_r.ok:
                repos = repos_r.json()
                signals["recent_repos"] = [{
                    "name":       r.get("name"),
                    "stars":      r.get("stargazers_count", 0),
                    "forks":      r.get("forks_count", 0),
                    "updated_at": r.get("updated_at", "")[:10],
                    "language":   r.get("language"),
                    "description": (r.get("description") or "")[:80],
                } for r in repos[:5]]

                total_stars = sum(r.get("stargazers_count", 0) for r in repos)
                signals["total_stars_recent_repos"] = total_stars

                # Check recency of activity
                if repos:
                    latest = repos[0].get("updated_at", "")[:10]
                    signals["last_repo_update"] = latest
                    try:
                        days_since = (datetime.now(timezone.utc) -
                                      datetime.fromisoformat(latest + "T00:00:00+00:00")).days
                        signals["days_since_last_commit"] = days_since
                        signals["recently_active"] = days_since < 30
                    except Exception:
                        pass

            # Commit activity (last 90 days via contributions endpoint)
            events_r = requests.get(
                f"https://api.github.com/users/{handle}/events/public?per_page=30",
                headers=headers, timeout=8
            )
            if events_r.ok:
                events = events_r.json()
                push_events = [e for e in events if e.get("type") == "PushEvent"]
                commit_count = sum(
                    len(e.get("payload", {}).get("commits", []))
                    for e in push_events
                )
                signals["recent_push_events"]  = len(push_events)
                signals["recent_commit_count"]  = commit_count
                signals["active_contributor"]   = commit_count > 5

        except Exception as e:
            signals["error"] = str(e)

        return signals

    # ── Gitcoin signals ───────────────────────────────────────────────────────

    def _collect_gitcoin_signals(self, address: str) -> dict:
        """
        Check Gitcoin Grants history for this wallet.
        Checks if they've received grants before and if projects are still active.
        """
        if not address:
            return {"checked": False}

        try:
            # Gitcoin Grants Stack API — public, no auth required
            r = requests.get(
                f"https://grants-stack-indexer-v2.gitcoin.co/data/1/projects.json",
                timeout=10
            )
            # This is a heavy endpoint — use address search instead
            # Try the project search by address
            r2 = requests.get(
                f"https://indexer-production.fly.dev/api/v1/results/passport/score/{address}",
                headers={"Content-Type": "application/json"},
                timeout=8
            )
            passport_score = None
            if r2.ok:
                data = r2.json()
                passport_score = data.get("score")

            # Check Gitcoin Passport score as legitimacy proxy
            return {
                "checked":                True,
                "address":                address[:10] + "...",
                "gitcoin_passport_score": passport_score,
                "passport_signal": (
                    "strong_identity"   if passport_score and float(passport_score) > 20
                    else "weak_identity" if passport_score and float(passport_score) > 0
                    else "no_passport"
                ),
                "note": "Gitcoin Passport score reflects on-chain identity verification breadth",
            }
        except Exception as e:
            return {"checked": True, "error": str(e)}

    # ── Farcaster signals ─────────────────────────────────────────────────────

    def _collect_farcaster_signals(self, handle: str) -> dict:
        """Farcaster presence check for the team or project."""
        if not handle:
            return {"provided": False}

        username = handle.replace("@", "").strip().lower()
        try:
            r = requests.get(
                f"https://client.warpcast.com/v2/user-by-username?username={username}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=8
            )
            if r.status_code != 200:
                return {"provided": True, "exists": False, "handle": username}

            user = r.json().get("result", {}).get("user", {})
            profile = user.get("profile", {})
            bio     = profile.get("bio", {}).get("text", "")

            fc = int(user.get("followerCount", 0))
            fw = int(user.get("followingCount", 0))

            return {
                "provided":       True,
                "exists":         True,
                "handle":         username,
                "bio":            bio,
                "follower_count": fc,
                "following_count": fw,
                "follow_ratio":   round(fc / max(fw, 1), 2),
                "cast_count":     user.get("castCount", 0),
                "reach_signal": (
                    "established_presence" if fc > 1000
                    else "growing_presence" if fc > 100
                    else "small_presence"
                ),
            }
        except Exception as e:
            return {"provided": True, "error": str(e)}

    # ── Venice reasoning ──────────────────────────────────────────────────────

    def _consult_venice(self, signals: dict) -> dict:
        """
        Send signals to Venice for legitimacy assessment.
        Only sends what was actually provided — strips empty fields so
        Venice reasons about presence of signals, not absence.
        """
        # ── Determine data richness ───────────────────────────────────────────
        w   = signals.get("wallet", {})
        gh  = signals.get("github", {})
        fc  = signals.get("farcaster", {})
        gk  = signals.get("gitcoin", {})
        ens = signals.get("ens", {})

        richness_score = 0
        provided_labels = []
        missing_labels  = []

        # Wallet signals
        addr_type = w.get("address_type", "eoa_wallet")
        if addr_type == "smart_contract":
            richness_score += 2
            provided_labels.append(f"smart contract ({w.get('code_size_bytes',0)} bytes bytecode)")
        else:
            tx = w.get("total_tx_count", 0)
            if tx > 100:
                richness_score += 2
                provided_labels.append(f"highly active EOA wallet ({tx} txs)")
            elif tx > 10:
                richness_score += 1
                provided_labels.append(f"active EOA wallet ({tx} txs)")

        if ens.get("has_ens"):
            richness_score += 1
            provided_labels.append(f"ENS name: {ens.get('ens_name')}")

        if gh.get("provided") and gh.get("exists"):
            richness_score += 2
            provided_labels.append(f"GitHub @{gh.get('handle')}")
        elif not gh.get("provided"):
            missing_labels.append("GitHub")

        if fc.get("provided") and fc.get("exists"):
            richness_score += 1
            provided_labels.append(f"Farcaster @{fc.get('handle')}")
        elif not fc.get("provided"):
            missing_labels.append("Farcaster")

        if gk.get("gitcoin_passport_score"):
            richness_score += 1
            provided_labels.append(f"Gitcoin Passport score {gk.get('gitcoin_passport_score')}")

        richness = (
            "RICH"     if richness_score >= 4 else
            "MODERATE" if richness_score >= 2 else
            "SPARSE"
        )

        # If only wallet address with no optional signals, skip Venice and return neutral
        wallet_only = not gh.get("provided") and not fc.get("provided") and not ens.get("has_ens")
        is_contract = addr_type == "smart_contract"

        if richness == "SPARSE" and wallet_only and not is_contract:
            return {
                "legitimacy_score":    50,
                "flags":               [],
                "strengths":           [],
                "assessment":          (
                    "Only a wallet address was provided — not enough signal to assess this team. "
                    "Add a GitHub handle, Farcaster profile, or ENS name to get a meaningful score. "
                    "A wallet address alone cannot confirm or deny legitimacy."
                ),
                "sybil_risk":          "LOW",
                "delivery_confidence": "MEDIUM",
                "data_richness":       "SPARSE",
            }

        missing_note = (
            f"IMPORTANT — the user did NOT provide: {', '.join(missing_labels)}. "
            f"These are optional fields. Their absence is NOT a red flag. "
            f"Do not mention or penalise missing optional inputs."
        ) if missing_labels else ""

        prompt = f"""You are a fair, experienced evaluator for public goods funding rounds (Gitcoin, Octant, Giveth).

Assess the legitimacy of this team or project based on the signals below.

SCORING RULES (follow precisely):
- 50 = neutral / unknown — not enough data to judge either way
- 60-70 = some positive signals, plausible team
- 70-85 = clear positive signals, credible track record  
- 85-100 = strong signals across multiple dimensions (active contract, ENS, GitHub, Passport)
- Below 45 = ONLY when you see active red flags: confirmed Sybil cluster, contradictory data, known bad actor patterns
- SPARSE data → return 50, NOT a low score. Sparse data is uncertainty, not suspicion.

CRITICAL INTERPRETATION RULES BY ADDRESS TYPE:

"eoa_wallet" — standard personal/team wallet:
- total_tx_count is valid — use it to assess activity level
- New wallet (low tx): neutral (50), not suspicious alone
- Active wallet (high tx): positive signal for established actor

"token_contract" — ERC-20 or ERC-721 token:
- eth_getTransactionCount is always 0 — ignore it completely
- Use: total_liquidity_usd, volume_24h_usd, age_days, fdv_usd, pair_count
- Deep liquidity ($100k+) + age >30 days = strong signal
- Brand new token (<3 days) + thin liquidity = caution, but not automatically bad
- This is a TOKEN, not a team wallet — score the token's market health

"defi_contract" or "generic_contract" — deployed protocol/precompile:
- eth_getTransactionCount is always 0 — ignore it completely
- Use: code_size_bytes, base_balance_eth, deployed_on_mainnet, tx_count_signal
- Large bytecode (>5000 bytes) + ETH balance + mainnet deployment = strong signal
- Base precompiles (0x4200...) are core infrastructure — automatically legitimate

ENS name: positive identity signal regardless of address type.
Active GitHub + recent commits: strong delivery credibility signal.
Gitcoin Passport score >20: strong multi-platform identity verification.

{missing_note}

Provided signals: {', '.join(provided_labels) if provided_labels else 'wallet only'}
Data richness: {richness}

════ FULL SIGNALS ════
{json.dumps(signals, indent=2, default=str)}
══════════════════════

Respond ONLY with valid JSON — no markdown, no preamble:
{{
  "legitimacy_score": <integer 0-100>,
  "flags": [<ONLY genuine red flags — empty array [] is correct and expected for most submissions>],
  "strengths": [<specific positive signals you found — be concrete>],
  "assessment": "<2-3 sentences. What the signals tell you. For contracts, comment on deployment and bytecode. For sparse data, describe what would increase confidence.>",
  "sybil_risk": "<LOW (default) | MEDIUM | HIGH (only if cluster evidence present)>",
  "delivery_confidence": "<LOW (evidence of inability) | MEDIUM (unknown/sparse) | HIGH (active contract or strong GitHub)>",
  "data_richness": "{richness}"
}}"""

        try:
            from threading import Semaphore
            try:
                from prophecy_engine import _venice_lock
                lock = _venice_lock
            except ImportError:
                lock = Semaphore(1)

            acquired = lock.acquire(timeout=120)
            if not acquired:
                raise TimeoutError("Venice lock wait timed out")
            try:
                r = requests.post(
                    "https://api.venice.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {VENICE_API_KEY}",
                        "Content-Type":  "application/json",
                    },
                    json={
                        "model":       VENICE_MODEL,
                        "messages":    [{"role": "user", "content": prompt}],
                        "temperature": 0.2,
                        "max_tokens":  400,
                    },
                    timeout=int(os.getenv("VENICE_TIMEOUT", "60")),
                )
                r.raise_for_status()
                raw = r.json()["choices"][0]["message"]["content"].strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                result = json.loads(raw)

                # ── Safety clamps ─────────────────────────────────────────────
                # Sparse data can never justify a score below 45
                if richness == "SPARSE":
                    result["legitimacy_score"] = max(45, int(result.get("legitimacy_score", 50)))
                    result["sybil_risk"] = result.get("sybil_risk", "LOW")
                    if result.get("delivery_confidence") == "LOW":
                        result["delivery_confidence"] = "MEDIUM"
                    # Strip flags that are just "absent data" complaints
                    bad_flag_keywords = [
                        "no_github", "no_farcaster", "no_ens", "missing",
                        "anonymous", "new_wallet", "minimal_transaction"
                    ]
                    result["flags"] = [
                        f for f in result.get("flags", [])
                        if not any(kw in f.lower() for kw in bad_flag_keywords)
                    ]

                return result

            finally:
                lock.release()

        except Exception as e:
            log.warning(f"Venice public goods analysis failed: {e}")
            return {
                "legitimacy_score":    50,
                "flags":               [],
                "strengths":           [],
                "assessment":          "Venice unavailable — manual review required.",
                "sybil_risk":          "LOW",
                "delivery_confidence": "MEDIUM",
                "data_richness":       richness,
            }

        try:
            from threading import Semaphore
            # reuse the global Venice lock from prophecy_engine if available
            try:
                from prophecy_engine import _venice_lock
                lock = _venice_lock
            except ImportError:
                lock = Semaphore(1)

            acquired = lock.acquire(timeout=120)
            if not acquired:
                raise TimeoutError("Venice lock wait timed out")
            try:
                r = requests.post(
                    "https://api.venice.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {VENICE_API_KEY}",
                        "Content-Type":  "application/json",
                    },
                    json={
                        "model":       VENICE_MODEL,
                        "messages":    [{"role": "user", "content": prompt}],
                        "temperature": 0.3,
                        "max_tokens":  400,
                    },
                    timeout=int(os.getenv("VENICE_TIMEOUT", "60")),
                )
                r.raise_for_status()
                raw = r.json()["choices"][0]["message"]["content"].strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                return json.loads(raw)
            finally:
                lock.release()

        except Exception as e:
            log.warning(f"Venice public goods analysis failed: {e}")
            return {
                "legitimacy_score":    50,
                "flags":               ["venice_unavailable"],
                "strengths":           [],
                "assessment":          "Venice unavailable — manual review required.",
                "sybil_risk":          "UNKNOWN",
                "delivery_confidence": "UNKNOWN",
                "data_richness":       "SPARSE",
            }

    # ── Main entrypoint ───────────────────────────────────────────────────────

    def evaluate(
        self,
        wallet:          str,
        github:          str  = "",
        farcaster_handle: str = "",
        contributor_wallets: list[str] = None,
        project_name:    str = "",
    ) -> dict:
        """
        Full public goods legitimacy evaluation.

        Args:
            wallet:               primary team/project wallet
            github:               GitHub username or org (optional)
            farcaster_handle:     Farcaster handle (optional)
            contributor_wallets:  list of up to 5 additional contributor wallets
            project_name:         human-readable project name for context

        Returns:
            legitimacy_score, flags, strengths, assessment, sybil_risk,
            delivery_confidence, raw_signals
        """
        # Resolve ENS name if provided instead of address
        resolved_wallet = resolve_ens(wallet)
        ens_info = enrich_address(resolved_wallet)
        display_name = ens_info["display"] if ens_info["has_ens"] else (resolved_wallet[:10] + "...")

        log.info(f"🔍 Evaluating public goods project | wallet={display_name} | ens={ens_info['ens_name']} | github={github} | handle={farcaster_handle}")

        signals = {
            "project_name":   project_name or "Unknown",
            "evaluation_for": "public_goods_funding_round",
            "ens":            ens_info,
        }

        # Collect all signal layers in parallel (simple sequential for now)
        signals["wallet"]      = self._collect_wallet_signals(resolved_wallet)
        signals["github"]      = self._collect_github_signals(github)
        signals["farcaster"]   = self._collect_farcaster_signals(farcaster_handle)
        signals["gitcoin"]     = self._collect_gitcoin_signals(wallet)

        if contributor_wallets:
            signals["contributors"] = self._collect_contributor_signals(contributor_wallets)

        # Venice assessment
        venice = self._consult_venice(signals)

        score = int(venice.get("legitimacy_score", 50))

        # Generate attestation UID
        uid = hashlib.sha256(
            f"{self.agent_id}{wallet}{int(time.time())}".encode()
        ).hexdigest()

        return {
            "wallet":               resolved_wallet,
            "ens_name":             ens_info["ens_name"],
            "display":              ens_info["display"],
            "project_name":         project_name,
            "legitimacy_score":     score,
            "flags":                venice.get("flags", []),
            "strengths":            venice.get("strengths", []),
            "assessment":           venice.get("assessment", ""),
            "sybil_risk":           venice.get("sybil_risk", "UNKNOWN"),
            "delivery_confidence":  venice.get("delivery_confidence", "UNKNOWN"),
            "data_richness":        venice.get("data_richness", "SPARSE"),
            "raw_signals":          signals,
            "attestation_uid":      uid,
            "evaluated_at":         int(time.time()),
        }


if __name__ == "__main__":
    oracle = PublicGoodsOracle("34499")
    result = oracle.evaluate(
        wallet            = "0x1EA37E2Fb76Aa396072204C90fcEF88093CEb920",
        github            = "gitcoinco",
        farcaster_handle  = "gitcoin",
        project_name      = "Test Project",
    )
    print(json.dumps({k: v for k, v in result.items() if k != "raw_signals"}, indent=2))