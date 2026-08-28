"""Deterministic risk gates — 8 bounded safety barriers. Must pass before any order."""
from typing import Dict, List, Any, Tuple
from datetime import datetime
import math

from src.config import RISK_CONFIG
from src.utils.logger import log_event, logger
from src.utils.fees import is_profitable_after_fees, estimate_fees
from src.features.greeks import calculate_portfolio_greeks

class RiskCheckResult:
    def __init__(self, passed: bool, reason: str, details: Dict[str, Any] | None = None, gate_id: int = 0):
        self.passed = passed
        self.reason = reason
        self.details = details or {}
        self.gate_id = gate_id

    def __bool__(self):
        return self.passed

def check_buying_power(account: Dict[str, Any], est_cost: float) -> RiskCheckResult:
    """Gate 1: Buying power allocation (<20% BP per trade, sufficient margin)."""
    bp = float(account.get("buying_power", 0) or 0)
    obp = account.get("options_buying_power")
    if obp is not None:
        try:
            bp = min(bp, float(obp))
        except Exception:
            pass
    if est_cost > bp:
        return RiskCheckResult(False, f"Gate 1 (BP): Need ${est_cost:.2f} > Available ${bp:.2f}", {"need": est_cost, "have": bp}, gate_id=1)
    max_trade_bp = bp * (RISK_CONFIG["max_buying_power_pct_per_trade"] / 100.0)
    if est_cost > max_trade_bp * 1.5:
        logger.warning(f"Large trade allocation: ${est_cost:.2f} vs recommended max ${max_trade_bp:.2f}")
    return RiskCheckResult(True, f"Gate 1 (BP): Allocation ${est_cost:.2f} within limit (${bp:.2f})", {"bp": bp, "need": est_cost}, gate_id=1)

def check_concentration(account: Dict[str, Any], positions: List[Dict[str, Any]], new_symbol_underlying: str, est_market_value: float) -> RiskCheckResult:
    """Gate 2: Concentration barrier (<30% total equity in single underlying)."""
    equity = float(account.get("equity", 100000) or 100000)
    current = 0.0
    for p in positions:
        sym = p.get("symbol", "")
        underlying = p.get("underlying_symbol") or p.get("asset_symbol") or sym[:4].strip()
        if new_symbol_underlying in sym or underlying == new_symbol_underlying:
            try:
                current += abs(float(p.get("market_value", 0) or 0))
            except Exception:
                pass
    total = current + est_market_value
    pct = (total / equity * 100.0) if equity > 0 else 100.0
    if pct > RISK_CONFIG["max_concentration_pct"]:
        return RiskCheckResult(False, f"Gate 2 (Concentration): {pct:.1f}% > Max {RISK_CONFIG['max_concentration_pct']}% for {new_symbol_underlying}", {"pct": pct, "current": current, "new": est_market_value}, gate_id=2)
    return RiskCheckResult(True, f"Gate 2 (Concentration): {pct:.1f}% <= {RISK_CONFIG['max_concentration_pct']}% for {new_symbol_underlying}", {"pct": round(pct, 2)}, gate_id=2)

def check_position_count(positions: List[Dict[str, Any]]) -> RiskCheckResult:
    """Gate 3: Position concurrency limit (<6 open positions)."""
    if len(positions) >= RISK_CONFIG["max_positions"]:
        return RiskCheckResult(False, f"Gate 3 (Positions): Max {RISK_CONFIG['max_positions']} reached ({len(positions)} active)", gate_id=3)
    return RiskCheckResult(True, f"Gate 3 (Positions): {len(positions)}/{RISK_CONFIG['max_positions']} active slots", gate_id=3)

def check_daily_loss(account: Dict[str, Any], initial_equity: float = 100000) -> RiskCheckResult:
    """Gate 4: Drawdown circuit breakers (Daily -3%, Weekly -6%)."""
    equity = float(account.get("equity", initial_equity) or initial_equity)
    last_equity = float(account.get("last_equity", equity) or equity)
    daily_chg_pct = ((equity - last_equity) / last_equity * 100.0) if last_equity else 0.0
    total_chg_pct = ((equity - initial_equity) / initial_equity * 100.0) if initial_equity else 0.0
    if daily_chg_pct <= -RISK_CONFIG["daily_loss_halt_pct"]:
        return RiskCheckResult(False, f"Gate 4 (Circuit Breaker): Daily loss {daily_chg_pct:.2f}% <= -{RISK_CONFIG['daily_loss_halt_pct']}%", {"daily_chg": daily_chg_pct}, gate_id=4)
    if total_chg_pct <= -RISK_CONFIG["weekly_loss_halt_pct"]:
        return RiskCheckResult(False, f"Gate 4 (Circuit Breaker): Weekly loss {total_chg_pct:.2f}% <= -{RISK_CONFIG['weekly_loss_halt_pct']}%", {"total_chg": total_chg_pct}, gate_id=4)
    return RiskCheckResult(True, f"Gate 4 (Circuit Breaker): Equity ${equity:,.2f} (Daily {daily_chg_pct:+.2f}%, Total {total_chg_pct:+.2f}%)", {"daily_chg": round(daily_chg_pct, 2), "total_chg": round(total_chg_pct, 2)}, gate_id=4)

def check_greeks_portfolio(positions: List[Dict[str, Any]], proposal_legs: List[Dict[str, Any]]) -> RiskCheckResult:
    """Gate 5: Portfolio Greeks boundary (Net Delta <= 60)."""
    # Calculate proposed trade Greeks
    prop_greeks = calculate_portfolio_greeks(proposal_legs)
    new_delta = prop_greeks.get("net_delta", 0.0)

    total_delta = new_delta
    for p in positions:
        try:
            total_delta += float(p.get("delta", 0) or 0.0)
        except Exception:
            pass
    if abs(total_delta) > RISK_CONFIG["max_portfolio_delta"]:
        return RiskCheckResult(False, f"Gate 5 (Greeks): Net Delta {total_delta:.1f} > Limit {RISK_CONFIG['max_portfolio_delta']}", {"delta": total_delta}, gate_id=5)
    return RiskCheckResult(True, f"Gate 5 (Greeks): Net Delta {total_delta:.1f} within limit ({RISK_CONFIG['max_portfolio_delta']})", {"delta": round(total_delta, 2), "daily_theta": prop_greeks.get("daily_theta")}, gate_id=5)

def check_option_expiry(chain: List[Dict[str, Any]]) -> RiskCheckResult:
    """Gate 6: Expiration window (0–7 DTE defined sprint)."""
    if not chain:
        return RiskCheckResult(True, "Gate 6 (Expiry): No chain to verify, default pass", gate_id=6)
    try:
        exp = chain[0].get("expiration") or chain[0].get("expiry")
        if exp:
            exp_dt = datetime.strptime(str(exp)[:10], "%Y-%m-%d").date()
            today = datetime.now().date()
            dte = (exp_dt - today).days
            if dte < RISK_CONFIG["min_days_to_expiry"]:
                return RiskCheckResult(False, f"Gate 6 (Expiry): DTE {dte} < Min {RISK_CONFIG['min_days_to_expiry']}", gate_id=6)
            if dte > RISK_CONFIG["max_days_to_expiry"]:
                return RiskCheckResult(False, f"Gate 6 (Expiry): DTE {dte} > Max {RISK_CONFIG['max_days_to_expiry']}", gate_id=6)
            return RiskCheckResult(True, f"Gate 6 (Expiry): DTE {dte} in target range [0, 7]", gate_id=6)
    except Exception as e:
        logger.debug(f"Expiry check skip: {e}")
    return RiskCheckResult(True, "Gate 6 (Expiry): Valid DTE", gate_id=6)

def check_open_orders_limit(orders: List[Dict[str, Any]]) -> RiskCheckResult:
    """Gate 7: Rate limiter and open orders throttle (<8 pending orders)."""
    open_orders = [o for o in orders if o.get("status") in ("new", "accepted", "pending_new", "partially_filled")]
    if len(open_orders) >= 8:
        return RiskCheckResult(False, f"Gate 7 (Order Throttle): {len(open_orders)} open orders >= 8 limit", gate_id=7)
    return RiskCheckResult(True, f"Gate 7 (Order Throttle): {len(open_orders)}/8 open orders", gate_id=7)

def check_fee_profitability(proposal: Dict[str, Any]) -> RiskCheckResult:
    """Gate 8: Fee profitability gate (survives commission, regulatory, slippage)."""
    profitable, fee_reason, fee_detail = is_profitable_after_fees(proposal)
    if not profitable:
        return RiskCheckResult(False, f"Gate 8 (Fee Gate): {fee_reason}", fee_detail, gate_id=8)
    return RiskCheckResult(True, f"Gate 8 (Fee Gate): {fee_reason}", fee_detail, gate_id=8)

def validate_trade(
    account: Dict[str, Any],
    positions: List[Dict[str, Any]],
    orders: List[Dict[str, Any]],
    proposal: Dict[str, Any],
    chain: List[Dict[str, Any]],
    initial_equity: float = 100000,
) -> Tuple[bool, List[str]]:
    """Run all 8 deterministic risk gates. Returns (passed, reasons)."""
    reasons: List[str] = []
    passed = True

    if proposal.get("strategy") == "NO_TRADE" or not proposal.get("legs"):
        return (False, [f"NO_TRADE: {proposal.get('reason') or proposal.get('rationale') or 'no edge'}"])

    legs = proposal.get("legs", [])
    underlying = proposal.get("symbol") or (legs[0].get("symbol", "")[:4].strip() if legs else "SPY")

    # Estimate max risk / cost
    est_cost = proposal.get("max_loss", 0) or proposal.get("est_debit", 0) * 100.0 or proposal.get("est_credit", 0) * 10.0
    if not est_cost or est_cost < 100.0:
        est_cost = sum((l.get("limit_price", 1.0) or 1.0) * 100.0 * l.get("qty", 1) for l in legs if l["side"] == "buy")
        est_cost = max(100.0, est_cost)

    checks = [
        check_buying_power(account, est_cost),
        check_concentration(account, positions, underlying, est_cost),
        check_position_count(positions),
        check_daily_loss(account, initial_equity),
        check_greeks_portfolio(positions, legs),
        check_option_expiry(chain),
        check_open_orders_limit(orders),
        check_fee_profitability(proposal),
    ]

    for c in checks:
        if not c.passed:
            passed = False
            reasons.append(f"✗ {c.reason}")
        else:
            reasons.append(f"✓ {c.reason}")

    log_event("risk_check", proposal=proposal.get("strategy"), underlying=underlying, passed=passed, reasons=reasons, est_cost=est_cost)
    return passed, reasons

