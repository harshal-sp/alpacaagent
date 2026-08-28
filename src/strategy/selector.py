"""Strategy selector — maps LLM regime+strategy to concrete options legs with net Greeks and defined risk."""
from typing import Dict, List, Any, Tuple
from src.utils.logger import log_event, logger
from src.features.greeks import describe_chain_greeks, option_mid_price, bs_greeks, calculate_portfolio_greeks
from src.utils.fees import estimate_fees, net_credit_for_spread, is_profitable_after_fees

def _nearest_strikes(chain: List[Dict], spot: float, count: int = 7) -> List[Dict]:
    return sorted(chain, key=lambda c: abs(c["strike"] - spot))[:count]

def _find(chain: List[Dict], strike: float, typ: str) -> Dict | None:
    cands = [c for c in chain if c["type"] == typ]
    if not cands:
        return None
    return min(cands, key=lambda c: abs(c["strike"] - strike))

def _liquid_mid(c: Dict) -> float:
    """Return realistic mid price — fallback to BS price if bid/ask illiquid."""
    bid, ask, last = c.get("bid", 0) or 0, c.get("ask", 0) or 0, c.get("last", 0) or 0
    mid = (bid + ask) / 2.0 if bid and ask and ask > bid and bid > 0.05 else 0.0
    if mid < 0.15:
        if c.get("price"):
            mid = float(c["price"])
        elif last and last > 0.15:
            mid = float(last)
        else:
            mid = max(0.40, abs(c.get("strike", c.get("spot", 500)) - c.get("spot", 500)) * 0.05 + 0.80 if "spot" in c else 0.80)
    return round(max(0.15, mid), 2)

def _get_min_width(spot: float) -> float:
    """Dynamic minimum spread width based on underlying spot tier."""
    if spot > 300.0:
        return 2.0
    elif spot > 100.0:
        return 1.0
    return 0.5

def build_legs(decision: Dict[str, Any], spot: float, chain: List[Dict[str, Any]], buying_power: float) -> Dict[str, Any]:
    """Given decision['strategy'], build aggressive defined-risk legs for 0-3 DTE."""
    strat = decision.get("strategy", "NO_TRADE")
    symbol = decision.get("symbol", "SPY")
    for cc in chain:
        cc["spot"] = spot

    # Enrich chain with greeks (T=2 days default for 0-3 DTE sprint)
    enriched = describe_chain_greeks(spot, chain, T_days=2)
    for c in enriched:
        if c.get("mid", 0) < 0.15:
            c["mid"] = _liquid_mid(c)

    def otm_call(delta_target=0.15):
        calls = [c for c in enriched if c["type"] == "call"]
        if not calls:
            return None
        return min(calls, key=lambda c: abs(c.get("delta", 0.20) - delta_target))

    def otm_put(delta_target=-0.15):
        puts = [c for c in enriched if c["type"] == "put"]
        if not puts:
            return None
        return min(puts, key=lambda c: abs(c.get("delta", -0.20) - delta_target))

    rationale = decision.get("rationale", "")
    min_req_width = _get_min_width(spot)

    # Quantity sizing logic: size to ~15-20% buying power, capped at 5 contracts and Delta limit
    def qty_for_budget(est_cost_per_spread: float, legs_preview: List[Dict] | None = None, net_delta_per_unit: float = 0.0) -> int:
        if est_cost_per_spread <= 0:
            return 1
        fee_buffer = 0
        if legs_preview:
            try:
                fees = estimate_fees(legs_preview)
                fee_buffer = fees["total_one_way"] / max(len(legs_preview), 1)
            except Exception:
                pass
        adj_cost = est_cost_per_spread + (fee_buffer / 100.0)
        max_by_bp = max(1, int((buying_power * 0.18) // (max(1.0, adj_cost) * 100.0)))
        max_qty = max(1, min(5, max_by_bp))
        if abs(net_delta_per_unit) > 0.01:
            # Gate 5 limit is 60 delta; size safely within 55
            max_by_delta = max(1, int(55.0 // (abs(net_delta_per_unit) * 100.0)))
            max_qty = max(1, min(max_qty, max_by_delta))
        return max_qty

    proposal: Dict[str, Any] = {}

    if strat == "IRON_CONDOR":
        short_put = otm_put(-0.18)
        long_put = otm_put(-0.05)
        short_call = otm_call(0.18)
        long_call = otm_call(0.05)

        need_fallback = False
        if not all([short_put, long_put, short_call, long_call]):
            need_fallback = True
        elif short_put["symbol"] == long_put["symbol"] or short_call["symbol"] == long_call["symbol"]:
            need_fallback = True
        elif abs(short_put["strike"] - long_put["strike"]) < min_req_width or abs(short_call["strike"] - long_call["strike"]) < min_req_width:
            need_fallback = True

        if need_fallback:
            puts = sorted([c for c in enriched if c["type"] == "put"], key=lambda x: x["strike"])
            calls = sorted([c for c in enriched if c["type"] == "call"], key=lambda x: x["strike"])
            if not puts or not calls:
                return {"strategy": strat, "legs": [], "error": "no puts/calls for condor fallback"}
            short_put = _find(enriched, spot * 0.985, "put") or puts[len(puts)//2]
            long_put = _find(enriched, spot * 0.965, "put") or puts[0]
            short_call = _find(enriched, spot * 1.015, "call") or calls[len(calls)//2]
            long_call = _find(enriched, spot * 1.035, "call") or calls[-1]

            if short_put and long_put and short_put["strike"] <= long_put["strike"]:
                lower_puts = [p for p in puts if p["strike"] < short_put["strike"]]
                if lower_puts:
                    long_put = max(lower_puts, key=lambda x: x["strike"])
            if short_call and long_call and short_call["strike"] >= long_call["strike"]:
                higher_calls = [c for c in calls if c["strike"] > short_call["strike"]]
                if higher_calls:
                    long_call = min(higher_calls, key=lambda x: x["strike"])

        if not all([short_put, long_put, short_call, long_call]):
            return {"strategy": strat, "legs": [], "error": "no valid condor wings"}
        if short_put["symbol"] == long_put["symbol"] or short_call["symbol"] == long_call["symbol"]:
            return {"strategy": strat, "legs": [], "error": "duplicate wings — invalid width"}

        credit = max(0.15, (option_mid_price(short_put) + option_mid_price(short_call) - option_mid_price(long_put) - option_mid_price(long_call)))
        width_put = abs(short_put["strike"] - long_put["strike"])
        width_call = abs(short_call["strike"] - long_call["strike"])
        min_w = min(width_put, width_call)
        if min_w < min_req_width:
            return {"strategy": strat, "legs": [], "error": f"width {min_w} < min required {min_req_width}"}

        qty = qty_for_budget(min_w - credit)
        legs = [
            {"symbol": short_put["symbol"], "side": "sell", "qty": qty, "type": "limit", "limit_price": short_put["mid"], "strike": short_put["strike"], "position_intent": "sell_to_open", "role": "short_put", "delta": short_put.get("delta"), "theta": short_put.get("theta")},
            {"symbol": long_put["symbol"], "side": "buy", "qty": qty, "type": "limit", "limit_price": long_put["mid"], "strike": long_put["strike"], "position_intent": "buy_to_open", "role": "long_put", "delta": long_put.get("delta"), "theta": long_put.get("theta")},
            {"symbol": short_call["symbol"], "side": "sell", "qty": qty, "type": "limit", "limit_price": short_call["mid"], "strike": short_call["strike"], "position_intent": "sell_to_open", "role": "short_call", "delta": short_call.get("delta"), "theta": short_call.get("theta")},
            {"symbol": long_call["symbol"], "side": "buy", "qty": qty, "type": "limit", "limit_price": long_call["mid"], "strike": long_call["strike"], "position_intent": "buy_to_open", "role": "long_call", "delta": long_call.get("delta"), "theta": long_call.get("theta")},
        ]
        max_loss = max(0.0, (min_w - credit) * 100.0 * qty)
        max_profit = credit * 100.0 * qty
        proposal = {
            "strategy": strat,
            "legs": legs,
            "qty": qty,
            "est_credit": round(credit, 2),
            "max_profit": round(max_profit, 2),
            "max_loss": round(max_loss, 2),
            "width": round(min_w, 2),
            "lower_breakeven": round(short_put["strike"] - credit, 2),
            "upper_breakeven": round(short_call["strike"] + credit, 2),
        }

    elif strat == "BULL_PUT_SPREAD":
        short_put = otm_put(-0.20) or _find(enriched, spot * 0.985, "put")
        long_put = otm_put(-0.07) or _find(enriched, spot * 0.965, "put")
        if not short_put or not long_put:
            return {"strategy": strat, "legs": [], "error": "no puts found"}
        if short_put["symbol"] == long_put["symbol"] or short_put["strike"] <= long_put["strike"]:
            puts_sorted = sorted([c for c in enriched if c["type"] == "put"], key=lambda x: x["strike"])
            short_put = _find(enriched, spot * 0.985, "put") or puts_sorted[len(puts_sorted)//2]
            lower = [p for p in puts_sorted if p["strike"] < short_put["strike"]]
            long_put = max(lower, key=lambda x: x["strike"]) if lower else puts_sorted[0]

        if short_put["strike"] <= long_put["strike"]:
            return {"strategy": strat, "legs": [], "error": "invalid strikes for bull put"}

        width = short_put["strike"] - long_put["strike"]
        credit = max(0.10, option_mid_price(short_put) - option_mid_price(long_put))
        net_delta_unit = abs(float(short_put.get("delta", -0.20) or -0.20) - float(long_put.get("delta", -0.07) or -0.07))
        qty = qty_for_budget(width - credit, net_delta_per_unit=net_delta_unit)
        legs = [
            {"symbol": short_put["symbol"], "side": "sell", "qty": qty, "type": "limit", "limit_price": short_put["mid"], "strike": short_put["strike"], "position_intent": "sell_to_open", "role": "short_put", "delta": short_put.get("delta"), "theta": short_put.get("theta")},
            {"symbol": long_put["symbol"], "side": "buy", "qty": qty, "type": "limit", "limit_price": long_put["mid"], "strike": long_put["strike"], "position_intent": "buy_to_open", "role": "long_put", "delta": long_put.get("delta"), "theta": long_put.get("theta")},
        ]
        max_loss = max(0.0, (width - credit) * 100.0 * qty)
        max_profit = credit * 100.0 * qty
        proposal = {
            "strategy": strat,
            "legs": legs,
            "qty": qty,
            "est_credit": round(credit, 2),
            "max_profit": round(max_profit, 2),
            "max_loss": round(max_loss, 2),
            "width": round(width, 2),
            "breakeven": round(short_put["strike"] - credit, 2),
        }

    elif strat == "BEAR_CALL_SPREAD":
        short_call = otm_call(0.20) or _find(enriched, spot * 1.015, "call")
        long_call = otm_call(0.07) or _find(enriched, spot * 1.035, "call")
        if not short_call or not long_call:
            return {"strategy": strat, "legs": [], "error": "no calls found"}
        if short_call["symbol"] == long_call["symbol"] or short_call["strike"] >= long_call["strike"]:
            calls_sorted = sorted([c for c in enriched if c["type"] == "call"], key=lambda x: x["strike"])
            short_call = _find(enriched, spot * 1.015, "call") or calls_sorted[len(calls_sorted)//2]
            higher = [c for c in calls_sorted if c["strike"] > short_call["strike"]]
            long_call = min(higher, key=lambda x: x["strike"]) if higher else calls_sorted[-1]

        if short_call["strike"] >= long_call["strike"]:
            return {"strategy": strat, "legs": [], "error": "invalid strikes for bear call"}

        width = long_call["strike"] - short_call["strike"]
        credit = max(0.10, option_mid_price(short_call) - option_mid_price(long_call))
        net_delta_unit = abs(float(short_call.get("delta", 0.20) or 0.20) - float(long_call.get("delta", 0.07) or 0.07))
        qty = qty_for_budget(width - credit, net_delta_per_unit=net_delta_unit)
        legs = [
            {"symbol": short_call["symbol"], "side": "sell", "qty": qty, "type": "limit", "limit_price": short_call["mid"], "strike": short_call["strike"], "position_intent": "sell_to_open", "role": "short_call", "delta": short_call.get("delta"), "theta": short_call.get("theta")},
            {"symbol": long_call["symbol"], "side": "buy", "qty": qty, "type": "limit", "limit_price": long_call["mid"], "strike": long_call["strike"], "position_intent": "buy_to_open", "role": "long_call", "delta": long_call.get("delta"), "theta": long_call.get("theta")},
        ]
        max_loss = max(0.0, (width - credit) * 100.0 * qty)
        max_profit = credit * 100.0 * qty
        proposal = {
            "strategy": strat,
            "legs": legs,
            "qty": qty,
            "est_credit": round(credit, 2),
            "max_profit": round(max_profit, 2),
            "max_loss": round(max_loss, 2),
            "width": round(width, 2),
            "breakeven": round(short_call["strike"] + credit, 2),
        }

    elif strat == "LONG_STRADDLE":
        atm_call = min([c for c in enriched if c["type"] == "call"], key=lambda c: abs(c["strike"] - spot), default=None)
        atm_put = min([c for c in enriched if c["type"] == "put"], key=lambda c: abs(c["strike"] - spot), default=None)
        if not atm_call or not atm_put:
            return {"strategy": strat, "legs": [], "error": "no atm contracts"}
        debit = option_mid_price(atm_call) + option_mid_price(atm_put)
        qty = qty_for_budget(debit)
        legs = [
            {"symbol": atm_call["symbol"], "side": "buy", "qty": qty, "type": "limit", "limit_price": atm_call["mid"], "strike": atm_call["strike"], "position_intent": "buy_to_open", "role": "long_call", "delta": atm_call.get("delta"), "theta": atm_call.get("theta")},
            {"symbol": atm_put["symbol"], "side": "buy", "qty": qty, "type": "limit", "limit_price": atm_put["mid"], "strike": atm_put["strike"], "position_intent": "buy_to_open", "role": "long_put", "delta": atm_put.get("delta"), "theta": atm_put.get("theta")},
        ]
        proposal = {
            "strategy": strat,
            "legs": legs,
            "qty": qty,
            "est_debit": round(debit, 2),
            "max_loss": round(debit * 100.0 * qty, 2),
            "lower_breakeven": round(atm_put["strike"] - debit, 2),
            "upper_breakeven": round(atm_call["strike"] + debit, 2),
        }

    elif strat == "LONG_STRANGLE":
        otm_c = otm_call(0.25) or _find(enriched, spot * 1.02, "call")
        otm_p = otm_put(-0.25) or _find(enriched, spot * 0.98, "put")
        if not otm_c or not otm_p:
            return {"strategy": strat, "legs": [], "error": "no otm contracts"}
        debit = option_mid_price(otm_c) + option_mid_price(otm_p)
        qty = qty_for_budget(debit)
        legs = [
            {"symbol": otm_c["symbol"], "side": "buy", "qty": qty, "type": "limit", "limit_price": otm_c["mid"], "strike": otm_c["strike"], "position_intent": "buy_to_open", "role": "long_call_otm", "delta": otm_c.get("delta"), "theta": otm_c.get("theta")},
            {"symbol": otm_p["symbol"], "side": "buy", "qty": qty, "type": "limit", "limit_price": otm_p["mid"], "strike": otm_p["strike"], "position_intent": "buy_to_open", "role": "long_put_otm", "delta": otm_p.get("delta"), "theta": otm_p.get("theta")},
        ]
        proposal = {
            "strategy": strat,
            "legs": legs,
            "qty": qty,
            "est_debit": round(debit, 2),
            "max_loss": round(debit * 100.0 * qty, 2),
            "lower_breakeven": round(otm_p["strike"] - debit, 2),
            "upper_breakeven": round(otm_c["strike"] + debit, 2),
        }

    elif strat == "LONG_CALL":
        call = otm_call(0.35) or _find(enriched, spot * 1.01, "call")
        if not call:
            return {"strategy": strat, "legs": [], "error": "no call contract"}
        debit = option_mid_price(call)
        net_delta_unit = float(call.get("delta", 0.35) or 0.35)
        qty = qty_for_budget(debit, net_delta_per_unit=net_delta_unit)
        legs = [{"symbol": call["symbol"], "side": "buy", "qty": qty, "type": "limit", "limit_price": call["mid"], "strike": call["strike"], "position_intent": "buy_to_open", "role": "long_call", "delta": call.get("delta"), "theta": call.get("theta")}]
        proposal = {
            "strategy": strat,
            "legs": legs,
            "qty": qty,
            "est_debit": round(debit, 2),
            "max_loss": round(debit * 100.0 * qty, 2),
            "breakeven": round(call["strike"] + debit, 2),
        }

    elif strat == "LONG_PUT":
        put = otm_put(-0.35) or _find(enriched, spot * 0.99, "put")
        if not put:
            return {"strategy": strat, "legs": [], "error": "no put contract"}
        debit = option_mid_price(put)
        net_delta_unit = abs(float(put.get("delta", -0.35) or -0.35))
        qty = qty_for_budget(debit, net_delta_per_unit=net_delta_unit)
        legs = [{"symbol": put["symbol"], "side": "buy", "qty": qty, "type": "limit", "limit_price": put["mid"], "strike": put["strike"], "position_intent": "buy_to_open", "role": "long_put", "delta": put.get("delta"), "theta": put.get("theta")}]
        proposal = {
            "strategy": strat,
            "legs": legs,
            "qty": qty,
            "est_debit": round(debit, 2),
            "max_loss": round(debit * 100.0 * qty, 2),
            "breakeven": round(put["strike"] - debit, 2),
        }

    elif strat == "NO_TRADE":
        return {"strategy": "NO_TRADE", "legs": [], "qty": 0, "reason": rationale}

    else:
        return {"strategy": strat, "legs": [], "error": f"unknown strategy {strat}"}

    # Attach computed net portfolio Greeks to proposal
    if proposal.get("legs"):
        proposal["greeks"] = calculate_portfolio_greeks(proposal["legs"], spot=spot, T_days=2)
    return proposal

