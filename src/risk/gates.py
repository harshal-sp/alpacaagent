"""Deterministic risk gates — aggressive but bounded. Must pass before any order."""
from typing import Dict, List, Any, Tuple
from datetime import datetime
import math

from src.config import RISK_CONFIG
from src.utils.logger import log_event, logger
from src.utils.fees import is_profitable_after_fees, estimate_fees

class RiskCheckResult:
    def __init__(self, passed: bool, reason: str, details: Dict[str, Any] | None = None):
        self.passed = passed
        self.reason = reason
        self.details = details or {}

    def __bool__(self):
        return self.passed

def check_buying_power(account: Dict[str, Any], est_cost: float) -> RiskCheckResult:
    bp = float(account.get("buying_power", 0) or 0)
    # options_buying_power is more accurate for options
    obp = account.get("options_buying_power")
    if obp is not None:
        try:
            bp = min(bp, float(obp))
        except:
            pass
    if est_cost > bp:
        return RiskCheckResult(False, f"Insufficient buying power: need ${est_cost:.2f} have ${bp:.2f}", {"need": est_cost, "have": bp})
    if est_cost > bp * (RISK_CONFIG["max_buying_power_pct_per_trade"] / 100) * 2:
        # aggressive allows up to 20% but warn if single trade > 40% BP
        logger.warning(f"Large trade {est_cost:.2f} vs BP {bp:.2f}")
    return RiskCheckResult(True, "BP ok", {"bp": bp, "need": est_cost})

def check_concentration(account: Dict[str, Any], positions: List[Dict[str, Any]], new_symbol_underlying: str, est_market_value: float) -> RiskCheckResult:
    equity = float(account.get("equity", 100000) or 100000)
    # calc current exposure to underlying
    current = 0.0
    for p in positions:
        sym = p.get("symbol", "")
        # option symbol contains underlying like SPY... ; position asset may have underlying
        underlying = p.get("underlying_symbol") or p.get("asset_symbol") or sym[:4].strip()
        if new_symbol_underlying in sym or underlying == new_symbol_underlying:
            try:
                current += abs(float(p.get("market_value", 0) or 0))
            except:
                pass
    total = current + est_market_value
    pct = total / equity * 100 if equity else 100
    if pct > RISK_CONFIG["max_concentration_pct"]:
        return RiskCheckResult(False, f"Concentration {pct:.1f}% > {RISK_CONFIG['max_concentration_pct']}% for {new_symbol_underlying}", {"pct": pct, "current": current, "new": est_market_value})
    return RiskCheckResult(True, "concentration ok", {"pct": round(pct,2)})

def check_position_count(positions: List[Dict[str, Any]]) -> RiskCheckResult:
    if len(positions) >= RISK_CONFIG["max_positions"]:
        return RiskCheckResult(False, f"Max positions {RISK_CONFIG['max_positions']} reached ({len(positions)} open)")
    return RiskCheckResult(True, "position count ok")

def check_daily_loss(account: Dict[str, Any], initial_equity: float = 100000) -> RiskCheckResult:
    equity = float(account.get("equity", initial_equity) or initial_equity)
    last_equity = float(account.get("last_equity", equity) or equity)
    # also track vs initial
    daily_chg_pct = (equity - last_equity) / last_equity * 100 if last_equity else 0
    total_chg_pct = (equity - initial_equity) / initial_equity * 100 if initial_equity else 0
    if daily_chg_pct <= -RISK_CONFIG["daily_loss_halt_pct"]:
        return RiskCheckResult(False, f"Daily loss halt {daily_chg_pct:.2f}% <= -{RISK_CONFIG['daily_loss_halt_pct']}%", {"daily_chg": daily_chg_pct})
    if total_chg_pct <= -RISK_CONFIG["weekly_loss_halt_pct"]:
        return RiskCheckResult(False, f"Weekly loss halt {total_chg_pct:.2f}% <= -{RISK_CONFIG['weekly_loss_halt_pct']}%")
    return RiskCheckResult(True, "daily loss ok", {"daily_chg": round(daily_chg_pct,2), "total_chg": round(total_chg_pct,2)})

def check_greeks_portfolio(positions: List[Dict[str, Any]], new_delta: float = 0) -> RiskCheckResult:
    # Estimate portfolio delta — sum position deltas if available
    total_delta = new_delta
    for p in positions:
        try:
            # Alpaca positions may not have delta; estimate from qty*delta if stored, else skip
            total_delta += float(p.get("delta", 0) or 0)
        except:
            pass
    if abs(total_delta) > RISK_CONFIG["max_portfolio_delta"]:
        return RiskCheckResult(False, f"Portfolio delta {total_delta:.1f} > {RISK_CONFIG['max_portfolio_delta']}", {"delta": total_delta})
    return RiskCheckResult(True, "delta ok", {"delta": round(total_delta,2)})

def check_option_expiry(chain: List[Dict[str, Any]]) -> RiskCheckResult:
    if not chain:
        return RiskCheckResult(True, "no chain to check expiry")
    # assume chain is for nearest expiry; verify DTE within bounds
    # expiry string like 2026-09-02
    try:
        exp = chain[0].get("expiration") or chain[0].get("expiry")
        if exp:
            from datetime import datetime
            exp_dt = datetime.strptime(str(exp)[:10], "%Y-%m-%d").date()
            today = datetime.now().date()
            dte = (exp_dt - today).days
            if dte < RISK_CONFIG["min_days_to_expiry"]:
                return RiskCheckResult(False, f"DTE {dte} < min {RISK_CONFIG['min_days_to_expiry']}")
            if dte > RISK_CONFIG["max_days_to_expiry"]:
                return RiskCheckResult(False, f"DTE {dte} > max {RISK_CONFIG['max_days_to_expiry']}")
    except Exception as e:
        logger.debug(f"expiry check skip: {e}")
    return RiskCheckResult(True, "expiry ok")

def validate_trade(
    account: Dict[str, Any],
    positions: List[Dict[str, Any]],
    orders: List[Dict[str, Any]],
    proposal: Dict[str, Any],
    chain: List[Dict[str, Any]],
    initial_equity: float = 100000,
) -> Tuple[bool, List[str]]:
    """Run all gates. Returns (passed, reasons)."""
    reasons: List[str] = []
    passed = True

    # quick no-trade
    if proposal.get("strategy") == "NO_TRADE" or not proposal.get("legs"):
        return (False, [f"NO_TRADE: {proposal.get('reason') or proposal.get('rationale') or 'no edge'}"])

    legs = proposal.get("legs", [])
    # aggregate cost
    est_cost = 0
    underlying = proposal.get("legs", [{}])[0].get("symbol", "")[:4].strip() if legs else "SPY"
    # try to get underlying from decision symbol
    if "symbol" in proposal:
        underlying = proposal["symbol"]
    # estimate max loss as cost proxy
    est_cost = proposal.get("max_loss", 0) or proposal.get("est_debit", 0) * 100 or proposal.get("est_credit", 0) * 10
    if not est_cost or est_cost < 100:
        # fallback: sum mid*100*qty for buy legs minus sell credits
        est_cost = sum((l.get("limit_price", 1) or 1) * 100 * l.get("qty", 1) for l in legs if l["side"] == "buy")
        # subtract credits approx
        est_cost = max(100, est_cost)

    checks = [
        check_buying_power(account, est_cost),
        check_position_count(positions),
        check_daily_loss(account, initial_equity),
        check_option_expiry(chain),
        check_concentration(account, positions, underlying, est_cost),
    ]
    # delta check: estimate net delta from legs (approx)
    try:
        net_delta = 0
        for l in legs:
            # rough: long call +0.5, long put -0.5, short opposite
            if "call" in l.get("role", ""):
                d = 0.3 if "short" in l["role"] else 0.4
                if l["side"] == "sell":
                    d = -d
                net_delta += d * l.get("qty", 1) * 100
            elif "put" in l.get("role", ""):
                d = -0.3 if "short" in l["role"] else -0.4
                if l["side"] == "sell":
                    d = -d
                net_delta += d * l.get("qty", 1) * 100
        checks.append(check_greeks_portfolio(positions, net_delta))
    except Exception:
        pass

    for c in checks:
        if not c.passed:
            passed = False
            reasons.append(c.reason)
        else:
            reasons.append(f"✓ {c.reason}")

    # check for too many open orders (rate limit)
    open_orders = [o for o in orders if o.get("status") in ("new", "accepted", "pending_new", "partially_filled")]
    if len(open_orders) >= 8:
        passed = False
        reasons.append(f"Too many open orders {len(open_orders)} >= 8")

    # fee profitability gate — NEW: ensure profit survives commissions + slippage + regulatory
    profitable, fee_reason, fee_detail = is_profitable_after_fees(proposal)
    if not profitable:
        passed = False
        reasons.append(f"✗ Fee gate: {fee_reason}")
    else:
        reasons.append(f"✓ Fee gate: {fee_reason}")

    # also ensure est_cost accounts for fees in buying-power buffer (add 2x one-way fees to be safe)
    try:
        fees = estimate_fees(legs)
        est_cost_with_fees = est_cost + fees["total_round_trip"]
        # If with fees exceeds BP, warn (but primary BP gate already checked gross)
        if est_cost_with_fees > float(account.get("buying_power", 1e9) or 1e9):
            reasons.append(f"⚠ With fees round-trip ${fees['total_round_trip']:.2f}, total ${est_cost_with_fees:.2f} near BP limit")
    except Exception:
        pass

    # log
    log_event("risk_check", proposal=proposal.get("strategy"), underlying=underlying, passed=passed, reasons=reasons, est_cost=est_cost, fees=fee_detail if 'fee_detail' in locals() else {})
    return passed, reasons
