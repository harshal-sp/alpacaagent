"""Strategy selector — maps LLM regime+strategy to concrete options legs."""
from typing import Dict, List, Any, Tuple
from src.utils.logger import log_event, logger
from src.features.greeks import describe_chain_greeks, option_mid_price, bs_greeks
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
    mid = (bid + ask) / 2 if bid and ask and ask > bid and bid > 0.05 else 0
    if mid < 0.15:
        # Use BS price if available, else last, else estimated
        if c.get("price"):
            mid = float(c["price"])
        elif last and last > 0.15:
            mid = float(last)
        else:
            # rough: OTM options ~1.0-2.5
            mid = max(0.4, abs(c.get("strike", c.get("spot", 500)) - c.get("spot", 500))*0.05 + 0.8 if "spot" in c else 0.8)
    return round(max(0.15, mid), 2)

def build_legs(decision: Dict[str, Any], spot: float, chain: List[Dict[str, Any]], buying_power: float) -> Dict[str, Any]:
    """Given decision['strategy'], build aggressive legs for 0-3 DTE."""
    strat = decision["strategy"]
    symbol = decision.get("symbol", "SPY")
    # attach spot to chain for fallback pricing
    for cc in chain:
        cc["spot"] = spot
    # enrich chain with greeks
    enriched = describe_chain_greeks(spot, chain, T_days=2)
    # Fix mids that are illiquid
    for c in enriched:
        # if original mid illiquid, replace
        if c.get("mid", 0) < 0.15:
            c["mid"] = _liquid_mid(c)
    # helpers
    def otm_call(delta_target=0.15):
        # sort calls by delta descending, pick nearest to target
        calls = [c for c in enriched if c["type"] == "call"]
        if not calls:
            return None
        return min(calls, key=lambda c: abs(c.get("delta", 0.2) - delta_target))
    def otm_put(delta_target=-0.15):
        puts = [c for c in enriched if c["type"] == "put"]
        if not puts:
            return None
        return min(puts, key=lambda c: abs(c.get("delta", -0.2) - delta_target))

    legs: List[Dict] = []
    rationale = decision.get("rationale", "")
    # Determine quantity — aggressive: size to ~15-20% BP but max 5 contracts, fee-aware (cost includes one-way fees)
    def qty_for_budget(est_cost_per_spread: float, legs_preview: List[Dict] | None = None) -> int:
        if est_cost_per_spread <= 0:
            return 1
        # Add estimated fees to cost per spread to avoid over-sizing when fees eat edge
        fee_buffer = 0
        if legs_preview:
            try:
                fees = estimate_fees(legs_preview)
                fee_buffer = fees["total_one_way"] / max(len(legs_preview), 1)  # spread evenly
            except Exception:
                pass
        adj_cost = est_cost_per_spread + fee_buffer / 100  # fee_buffer is total dollars, convert to per-share
        max_by_bp = max(1, int((buying_power * 0.18) // (max(1, adj_cost) * 100)))
        return max(1, min(5, max_by_bp, 5))

    if strat == "IRON_CONDOR":
        # 4-leg: Sell OTM put spread + Sell OTM call spread (credit)
        # Put spread: Sell ~15 delta put, Buy ~5 delta put
        # Call spread: Sell ~15 delta call, Buy ~5 delta call
        short_put = otm_put(-0.18)
        long_put = otm_put(-0.05)
        short_call = otm_call(0.18)
        long_call = otm_call(0.05)
        # Validate distinct strikes — if same symbol picked twice, fix via fallback
        need_fallback = False
        if not all([short_put, long_put, short_call, long_call]):
            need_fallback = True
        elif short_put["symbol"] == long_put["symbol"] or short_call["symbol"] == long_call["symbol"]:
            need_fallback = True
        elif abs(short_put["strike"] - long_put["strike"]) < 2 or abs(short_call["strike"] - long_call["strike"]) < 2:
            need_fallback = True
        if need_fallback:
            # fallback: nearest strikes method with enforced separation
            puts = sorted([c for c in enriched if c["type"]=="put"], key=lambda x: x["strike"])
            calls = sorted([c for c in enriched if c["type"]=="call"], key=lambda x: x["strike"])
            # pick around spot +-1.5% and +-3%
            short_put = _find(enriched, spot * 0.985, "put") or puts[len(puts)//2]
            long_put = _find(enriched, spot * 0.965, "put") or puts[0]
            short_call = _find(enriched, spot * 1.015, "call") or calls[len(calls)//2]
            long_call = _find(enriched, spot * 1.035, "call") or calls[-1]
            # ensure distinct and properly ordered (OTM wings further)
            if short_put and long_put and short_put["strike"] <= long_put["strike"]:
                # long put should be lower strike (more OTM)
                # ensure long_put strike < short_put strike
                if short_put["strike"] == long_put["strike"]:
                    # find next lower put
                    lower_puts = [p for p in puts if p["strike"] < short_put["strike"]]
                    if lower_puts:
                        long_put = max(lower_puts, key=lambda x: x["strike"])
            if short_call and long_call and short_call["strike"] >= long_call["strike"]:
                if short_call["strike"] == long_call["strike"]:
                    higher_calls = [c for c in calls if c["strike"] > short_call["strike"]]
                    if higher_calls:
                        long_call = min(higher_calls, key=lambda x: x["strike"])
        # final distinct check — if still same, abort to avoid zero width
        if not all([short_put, long_put, short_call, long_call]):
            return {"strategy": strat, "legs": [], "error": "no valid condor wings"}
        if short_put["symbol"] == long_put["symbol"] or short_call["symbol"] == long_call["symbol"]:
            return {"strategy": strat, "legs": [], "error": "duplicate wings — no valid width"}
        # credit estimate
        credit = (option_mid_price(short_put) + option_mid_price(short_call) - option_mid_price(long_put) - option_mid_price(long_call))
        credit = max(0.15, credit)
        width_put = abs(short_put["strike"]-long_put["strike"])
        width_call = abs(short_call["strike"]-long_call["strike"])
        min_width = min(width_put, width_call)
        if min_width < 2:
            return {"strategy": strat, "legs": [], "error": f"width too small {min_width}"}
        qty = qty_for_budget(min(width_put, width_call) - credit)
        legs = [
            {"symbol": short_put["symbol"], "side": "sell", "qty": qty, "type": "limit", "limit_price": short_put["mid"], "position_intent": "sell_to_open", "role": "short_put"},
            {"symbol": long_put["symbol"], "side": "buy", "qty": qty, "type": "limit", "limit_price": long_put["mid"], "position_intent": "buy_to_open", "role": "long_put"},
            {"symbol": short_call["symbol"], "side": "sell", "qty": qty, "type": "limit", "limit_price": short_call["mid"], "position_intent": "sell_to_open", "role": "short_call"},
            {"symbol": long_call["symbol"], "side": "buy", "qty": qty, "type": "limit", "limit_price": long_call["mid"], "position_intent": "buy_to_open", "role": "long_call"},
        ]
        max_loss = (width_put + width_call)/2 * 100 * qty - credit*100*qty
        return {"strategy": strat, "legs": legs, "qty": qty, "est_credit": round(credit,2), "max_loss": round(max_loss,2), "width": round(width_put,2)}

    elif strat == "BULL_PUT_SPREAD":
        short_put = otm_put(-0.20) or _find(enriched, spot * 0.98, "put")
        long_put = otm_put(-0.07) or _find(enriched, spot * 0.96, "put")
        if not short_put or not long_put:
            return {"strategy": strat, "legs": [], "error": "no puts found"}
        if short_put["symbol"] == long_put["symbol"] or short_put["strike"] == long_put["strike"]:
            # force separation
            puts_sorted = sorted([c for c in enriched if c["type"]=="put"], key=lambda x: x["strike"])
            # short at 98% spot, long at 96% spot with distinct
            short_put = _find(enriched, spot * 0.985, "put") or puts_sorted[len(puts_sorted)//2]
            long_put = _find(enriched, spot * 0.965, "put") or puts_sorted[0]
            if short_put["symbol"] == long_put["symbol"]:
                lower = [p for p in puts_sorted if p["strike"] < short_put["strike"]]
                if lower:
                    long_put = max(lower, key=lambda x: x["strike"])
        # ensure short strike > long strike
        if short_put["strike"] < long_put["strike"]:
            short_put, long_put = long_put, short_put
        if short_put["strike"] == long_put["strike"]:
            return {"strategy": strat, "legs": [], "error": "duplicate strikes bull put"}
        credit = max(0.10, option_mid_price(short_put) - option_mid_price(long_put))
        if credit <= 0.05:
            return {"strategy": strat, "legs": [], "error": "no credit bull put"}
        qty = qty_for_budget((short_put["strike"]-long_put["strike"]) - credit)
        legs = [
            {"symbol": short_put["symbol"], "side": "sell", "qty": qty, "type": "limit", "limit_price": short_put["mid"], "position_intent": "sell_to_open", "role": "short_put"},
            {"symbol": long_put["symbol"], "side": "buy", "qty": qty, "type": "limit", "limit_price": long_put["mid"], "position_intent": "buy_to_open", "role": "long_put"},
        ]
        return {"strategy": strat, "legs": legs, "qty": qty, "est_credit": round(credit,2), "max_loss": round((short_put["strike"]-long_put["strike"])*100*qty - credit*100*qty,2)}

    elif strat == "BEAR_CALL_SPREAD":
        short_call = otm_call(0.20) or _find(enriched, spot * 1.02, "call")
        long_call = otm_call(0.07) or _find(enriched, spot * 1.04, "call")
        if not short_call or not long_call:
            return {"strategy": strat, "legs": [], "error": "no calls found"}
        if short_call["symbol"] == long_call["symbol"] or short_call["strike"] == long_call["strike"]:
            calls_sorted = sorted([c for c in enriched if c["type"]=="call"], key=lambda x: x["strike"])
            short_call = _find(enriched, spot * 1.015, "call") or calls_sorted[len(calls_sorted)//2]
            long_call = _find(enriched, spot * 1.035, "call") or calls_sorted[-1]
            if short_call["symbol"] == long_call["symbol"]:
                higher = [c for c in calls_sorted if c["strike"] > short_call["strike"]]
                if higher:
                    long_call = min(higher, key=lambda x: x["strike"])
        if short_call["strike"] > long_call["strike"]:
            short_call, long_call = long_call, short_call
        if short_call["strike"] == long_call["strike"]:
            return {"strategy": strat, "legs": [], "error": "duplicate strikes bear call"}
        credit = max(0.10, option_mid_price(short_call) - option_mid_price(long_call))
        if credit <= 0.05:
            return {"strategy": strat, "legs": [], "error": "no credit bear call"}
        qty = qty_for_budget((long_call["strike"]-short_call["strike"]) - credit)
        legs = [
            {"symbol": short_call["symbol"], "side": "sell", "qty": qty, "type": "limit", "limit_price": short_call["mid"], "position_intent": "sell_to_open", "role": "short_call"},
            {"symbol": long_call["symbol"], "side": "buy", "qty": qty, "type": "limit", "limit_price": long_call["mid"], "position_intent": "buy_to_open", "role": "long_call"},
        ]
        return {"strategy": strat, "legs": legs, "qty": qty, "est_credit": round(credit,2), "max_loss": round((long_call["strike"]-short_call["strike"])*100*qty - credit*100*qty,2)}

    elif strat == "LONG_STRADDLE":
        # ATM call + put
        atm_call = min([c for c in enriched if c["type"]=="call"], key=lambda c: abs(c["strike"]-spot), default=None)
        atm_put = min([c for c in enriched if c["type"]=="put"], key=lambda c: abs(c["strike"]-spot), default=None)
        if not atm_call or not atm_put:
            return {"strategy": strat, "legs": [], "error": "no atm"}
        debit = option_mid_price(atm_call) + option_mid_price(atm_put)
        qty = qty_for_budget(debit)
        legs = [
            {"symbol": atm_call["symbol"], "side": "buy", "qty": qty, "type": "limit", "limit_price": atm_call["mid"], "position_intent": "buy_to_open", "role": "long_call"},
            {"symbol": atm_put["symbol"], "side": "buy", "qty": qty, "type": "limit", "limit_price": atm_put["mid"], "position_intent": "buy_to_open", "role": "long_put"},
        ]
        return {"strategy": strat, "legs": legs, "qty": qty, "est_debit": round(debit,2), "max_loss": round(debit*100*qty,2)}

    elif strat == "LONG_STRANGLE":
        otm_c = otm_call(0.25) or _find(enriched, spot*1.02, "call")
        otm_p = otm_put(-0.25) or _find(enriched, spot*0.98, "put")
        if not otm_c or not otm_p:
            return {"strategy": strat, "legs": [], "error": "no otm"}
        debit = option_mid_price(otm_c) + option_mid_price(otm_p)
        qty = qty_for_budget(debit)
        legs = [
            {"symbol": otm_c["symbol"], "side": "buy", "qty": qty, "type": "limit", "limit_price": otm_c["mid"], "position_intent": "buy_to_open", "role": "long_call_otm"},
            {"symbol": otm_p["symbol"], "side": "buy", "qty": qty, "type": "limit", "limit_price": otm_p["mid"], "position_intent": "buy_to_open", "role": "long_put_otm"},
        ]
        return {"strategy": strat, "legs": legs, "qty": qty, "est_debit": round(debit,2), "max_loss": round(debit*100*qty,2)}

    elif strat == "LONG_CALL":
        call = otm_call(0.35) or _find(enriched, spot*1.01, "call")
        if not call:
            return {"strategy": strat, "legs": [], "error": "no call"}
        debit = option_mid_price(call)
        qty = qty_for_budget(debit)
        legs = [{"symbol": call["symbol"], "side": "buy", "qty": qty, "type": "limit", "limit_price": call["mid"], "position_intent": "buy_to_open", "role": "long_call"}]
        return {"strategy": strat, "legs": legs, "qty": qty, "est_debit": round(debit,2), "max_loss": round(debit*100*qty,2)}

    elif strat == "LONG_PUT":
        put = otm_put(-0.35) or _find(enriched, spot*0.99, "put")
        if not put:
            return {"strategy": strat, "legs": [], "error": "no put"}
        debit = option_mid_price(put)
        qty = qty_for_budget(debit)
        legs = [{"symbol": put["symbol"], "side": "buy", "qty": qty, "type": "limit", "limit_price": put["mid"], "position_intent": "buy_to_open", "role": "long_put"}]
        return {"strategy": strat, "legs": legs, "qty": qty, "est_debit": round(debit,2), "max_loss": round(debit*100*qty,2)}

    elif strat == "NO_TRADE":
        return {"strategy": "NO_TRADE", "legs": [], "qty": 0, "reason": rationale}

    else:
        return {"strategy": strat, "legs": [], "error": f"unknown strat {strat}"}
