"""
utils/ens.py — ENS resolution for Oracle of Base
"""
import logging
import requests

log = logging.getLogger("ens")
_IDEAS_BASE = "https://api.ensideas.com/ens/resolve"
_SUBGRAPH   = "https://api.thegraph.com/subgraphs/name/ensdomains/ens"
_TIMEOUT    = 6

def _is_address(val: str) -> bool:
    return bool(val) and val.startswith("0x") and len(val) == 42

def resolve_ens(name_or_address: str) -> str:
    """Resolve ENS name → address. Returns input unchanged if already address or resolution fails."""
    val = (name_or_address or "").strip()
    if not val or _is_address(val) or "." not in val:
        return val
    try:
        r = requests.get(f"{_IDEAS_BASE}/{val}", timeout=_TIMEOUT)
        if r.ok:
            addr = r.json().get("address")
            if addr and _is_address(addr):
                return addr
    except Exception as e:
        log.debug(f"ENS Ideas failed for {val}: {e}")
    try:
        query = '{ domains(where:{name:"%s"}) { resolvedAddress { id } } }' % val
        r = requests.post(_SUBGRAPH, json={"query": query}, timeout=_TIMEOUT)
        if r.ok:
            domains = r.json().get("data", {}).get("domains", [])
            if domains:
                addr = domains[0].get("resolvedAddress", {}).get("id")
                if addr and _is_address(addr):
                    return addr
    except Exception as e:
        log.debug(f"ENS subgraph failed for {val}: {e}")
    log.warning(f"ENS resolution failed for {val}")
    return val

def reverse_lookup(address: str) -> str | None:
    """Get primary ENS name for address. Returns None if not set."""
    if not _is_address(address):
        return None
    try:
        r = requests.get(f"{_IDEAS_BASE}/{address}", timeout=_TIMEOUT)
        if r.ok:
            name = r.json().get("name")
            if name and "." in name:
                return name
    except Exception as e:
        log.debug(f"ENS reverse failed for {address}: {e}")
    return None

def enrich_address(address: str) -> dict:
    """Return enriched address dict with ENS name if available."""
    addr  = (address or "").strip()
    short = addr[:6] + "..." + addr[-4:] if len(addr) >= 10 else addr
    name  = reverse_lookup(addr)
    return {
        "address":    addr,
        "ens_name":   name,
        "display":    name if name else short,
        "has_ens":    name is not None,
        "ens_signal": "registered_identity" if name else "anonymous",
    }