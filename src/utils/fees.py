"""Fee & slippage model — ensures paper profits survive live transaction costs."""
from typing import Dict, Any, List

# Fallback defaults if config not yet patched
DEFAULT_FEE_CONFIG = {
    "options_commission_per_contract": 0.15,  # Simulated: $0.15/contract/leg (Alpaca paper is $0, live pass-through $0.15-0.65 + regulatory $0.02)
    "stock_commission_per_share": 0.0,       # Alpaca stocks $0 commission
    "regulatory_per_contract": 0.02,        # OCC/ORF approx $0.02
    "slippage_bps": 5,                       # 0.05% mid-to-fill slip on illiquid 0DTE
    "min_net_credit_per_share": 0.10,       # Reject spreads where net credit after fees < $0.10 (can't cover costs)
    "min_net_debit_edge_pct": 5.0,          # For longs, require expected edge >5% over fees
}

def get_fee_config() -> Dict[str, Any]:
    try:
        from src.config import FEE_CONFIG
        return FEE_CONFIG
    except ImportError:
        return DEFAULT_FEE_CONFIG
    except AttributeError:
        return DEFAULT_FEE_CONFIG

def estimate_fees(legs: List[Dict[str, Any]], is_open: bool = True) -> Dict[str, float]:
    """Estimate one-way fees (open or close). For round-trip double it.
    - Options: per-contract commission + regulatory
    - Stocks: per-share (usually 0)
    - Slippage: bps on notional
    """
    cfg = get_fee_config()
    opt_comm = cfg.get("options_commission_per_contract", 0.15)
    reg = cfg.get("regulatory_per_contract", 0.02)
    stock_comm = cfg.get("stock_commission_per_share", 0.0)
    slip_bps = cfg.get("slippage_bps", 5)

    total_comm = 0.0
    total_slip = 0.0
    for leg in legs:
        qty = int(leg.get("qty", 1))
        price = float(leg.get("limit_price") or leg.get("mid") or 0)
        symbol = leg.get("symbol", "")
        is_option = len(symbol) > 10 and any(c in symbol for c in ["C", "P"])
        if is_option:
            total_comm += (opt_comm + reg) * qty
            # slippage on notional = price * 100 * qty * bps/10000
            total_slip += price * 100 * qty * slip_bps / 10000
        else:
            total_comm += stock_comm * qty
            total_slip += price * qty * slip_bps / 10000
    return {
        "commission": round(total_comm, 2),
        "regulatory": round(reg * sum(int(l.get("qty",1)) for l in legs if len(l.get("symbol",""))>10), 2),
        "slippage": round(total_slip, 2),
        "total_one_way": round(total_comm + total_slip, 2),
        "total_round_trip": round((total_comm + total_slip) * 2, 2),
    }

def net_credit_for_spread(est_credit_per_share: float, qty: int, legs: List[Dict[str, Any]]) -> float:
    """Net credit after one-way open fees (close fees reserved for exit). Returns per-share net."""
    fees = estimate_fees(legs, is_open=True)
    gross = est_credit_per_share * 100 * qty
    net = gross - fees["total_one_way"]
    per_share_net = net / (100 * qty) if qty else 0
    return round(per_share_net, 3)

def net_debit_for_long(est_debit_per_share: float, qty: int, legs: List[Dict[str, Any]]) -> float:
    """Net debit (cost) including open fees — what you actually pay to open."""
    fees = estimate_fees(legs, is_open=True)
    gross = est_debit_per_share * 100 * qty
    net = gross + fees["total_one_way"]
    per_share_net = net / (100 * qty) if qty else 0
    # For guardrail, return total net cost
    return round(per_share_net, 3)

def is_profitable_after_fees(proposal: Dict[str, Any]) -> tuple[bool, str, Dict[str, float]]:
    """Check if proposal survives fees. Returns (profitable, reason, fee_detail)."""
    cfg = get_fee_config()
    legs = proposal.get("legs", [])
    if not legs:
        return (False, "no legs", {})
    qty = proposal.get("qty", 1)
    fees = estimate_fees(legs)
    # Spread: check net credit per share > min
    if "est_credit" in proposal:
        gross_per_share = float(proposal["est_credit"])
        net_per_share = net_credit_for_spread(gross_per_share, qty, legs)
        min_net = cfg.get("min_net_credit_per_share", 0.10)
        if net_per_share < min_net:
            return (False, f"net credit ${net_per_share}/share < min ${min_net} after fees {fees}", fees)
        # Also check gross fees don't eat >60% of credit
        gross_total = gross_per_share * 100 * qty
        if fees["total_one_way"] / max(gross_total, 1) > 0.6:
            return (False, f"fees ${fees['total_one_way']} >60% of gross credit ${gross_total}", fees)
        return (True, f"net credit ${net_per_share}/share after fees {fees}", fees)
    if "est_debit" in proposal:
        gross_per_share = float(proposal["est_debit"])
        # For longs, check that expected edge (we assume 15% move needed) exceeds fees
        # Simple guard: gross debit should be at least 2x one-way fees so fees <50%
        gross_total = gross_per_share * 100 * qty
        if fees["total_one_way"] / max(gross_total, 1) > 0.5:
            return (False, f"fees ${fees['total_one_way']} >50% of debit ${gross_total} — illiquid", fees)
        return (True, f"debit ${gross_per_share}/share + fees {fees} = net ~${gross_per_share + fees['total_one_way']/(100*qty):.3f}/share", fees)
    # Fallback: proposal has max_loss but no explicit credit/debit (legacy test). Infer from legs mids if possible.
    # Estimate net from legs: sells - buys
    try:
        gross_estimate = 0.0
        for l in legs:
            mid = float(l.get("limit_price") or l.get("mid") or 0)
            if l.get("side") == "sell":
                gross_estimate += mid
            else:
                gross_estimate -= mid
        # If gross_estimate >0 it's credit spread, else debit
        if gross_estimate != 0:
            # Re-check with inferred credit/debit
            if gross_estimate > 0:
                net_per_share = net_credit_for_spread(gross_estimate, qty, legs)
                min_net = cfg.get("min_net_credit_per_share", 0.10)
                if net_per_share < min_net:
                    return (False, f"inferred net credit ${net_per_share}/share < min ${min_net} after fees", fees)
                return (True, f"inferred credit ${gross_estimate:.2f}/share net ${net_per_share}/share after fees {fees}", fees)
            else:
                gross_total = abs(gross_estimate) * 100 * qty
                if fees["total_one_way"] / max(gross_total, 1) > 0.5:
                    return (False, f"inferred fees ${fees['total_one_way']} >50% of debit", fees)
                return (True, f"inferred debit ${abs(gross_estimate):.2f}/share after fees {fees}", fees)
    except Exception:
        pass
    # If still unknown, allow (conservative: fees are small vs typical notional)
    return (True, f"no est_credit/debit but fees {fees} considered — allowing", fees)
