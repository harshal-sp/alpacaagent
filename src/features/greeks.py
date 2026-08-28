"""Black-Scholes greeks — for spread selection, pricing, and portfolio risk."""
import math
from typing import Dict, List, Any
from scipy.stats import norm

def bs_greeks(S: float, K: float, T: float, r: float = 0.045, sigma: float = 0.25, option_type: str = "call", q: float = 0.0) -> Dict[str, float]:
    """Return delta, gamma, theta (per day), vega, price. Sigma clamped to [0.08, 1.20]."""
    try:
        import pandas as pd
        if sigma is None or pd.isna(sigma):
            sigma = 0.25
    except Exception:
        if not sigma:
            sigma = 0.25
    try:
        sigma = float(sigma)
    except Exception:
        sigma = 0.25
    sigma = max(0.08, min(1.20, sigma))

    if T <= 0.0001 or sigma <= 0 or S <= 0 or K <= 0:
        # Expiration immediate — intrinsic value
        intrinsic = max(0.0, S - K) if option_type == "call" else max(0.0, K - S)
        delta = 1.0 if option_type == "call" and S > K else 0.0 if option_type == "call" else (-1.0 if S < K else 0.0)
        return {"delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "price": round(intrinsic, 2)}

    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    nd1 = float(norm.cdf(d1))
    nd2 = float(norm.cdf(d2))
    pdf_d1 = float(norm.pdf(d1))
    disc_r = math.exp(-r * T)
    disc_q = math.exp(-q * T)

    if option_type == "call":
        price = S * disc_q * nd1 - K * disc_r * nd2
        delta = disc_q * nd1
        theta = -(S * disc_q * pdf_d1 * sigma) / (2 * sqrt_T) - r * K * disc_r * nd2 + q * S * disc_q * nd1
    else:
        price = K * disc_r * (1.0 - nd2) - S * disc_q * (1.0 - nd1)
        delta = disc_q * (nd1 - 1.0)
        theta = -(S * disc_q * pdf_d1 * sigma) / (2 * sqrt_T) + r * K * disc_r * (1.0 - nd2) - q * S * disc_q * (1.0 - nd1)

    gamma = (disc_q * pdf_d1) / (S * sigma * sqrt_T) if S * sigma * sqrt_T > 0 else 0.0
    vega = S * disc_q * pdf_d1 * sqrt_T / 100.0  # dollar change per 1% vol

    return {
        "delta": round(float(delta), 3),
        "gamma": round(float(gamma), 4),
        "theta": round(float(theta / 365.0), 4),  # theta per calendar day
        "vega": round(float(vega), 4),
        "price": round(max(0.01, float(price)), 2),
    }

def option_mid_price(contract: dict) -> float:
    bid, ask = contract.get("bid", 0) or 0, contract.get("ask", 0) or 0
    last = contract.get("last", 0) or 0
    if bid and ask and ask > bid and bid > 0.01:
        return (bid + ask) / 2.0
    return float(last) if last else float(bid or ask or 0.0)

def describe_chain_greeks(spot: float, chain: List[Dict[str, Any]], T_days: int = 3, r: float = 0.045) -> List[Dict[str, Any]]:
    T = max(0.005, T_days / 365.0)
    out = []
    for c in chain:
        K = float(c["strike"])
        sigma = float(c.get("iv", 0.25) or 0.25)
        greeks = bs_greeks(spot, K, T, r, sigma, c["type"])
        out.append({**c, **greeks, "mid": round(option_mid_price(c), 2)})
    return out

def calculate_portfolio_greeks(legs: List[Dict[str, Any]], spot: float = 500.0, T_days: int = 2) -> Dict[str, float]:
    """Calculate aggregated position/portfolio Greeks across multiple legs.
    Returns: net_delta ($ delta), net_gamma, daily_theta ($/day), net_vega ($/1% IV).
    """
    total_delta = 0.0
    total_gamma = 0.0
    total_theta = 0.0
    total_vega = 0.0

    for leg in legs:
        qty = int(leg.get("qty", 1))
        side_sign = 1.0 if leg.get("side", "").lower() == "buy" else -1.0
        delta = leg.get("delta")
        gamma = leg.get("gamma")
        theta = leg.get("theta")
        vega = leg.get("vega")

        if delta is None or gamma is None or theta is None or vega is None:
            strike = float(leg.get("strike", spot) or spot)
            opt_type = "call" if "C" in str(leg.get("symbol", "")) else "put"
            calc = bs_greeks(spot, strike, max(0.005, T_days / 365.0), option_type=opt_type)
            delta = delta if delta is not None else calc["delta"]
            gamma = gamma if gamma is not None else calc["gamma"]
            theta = theta if theta is not None else calc["theta"]
            vega = vega if vega is not None else calc["vega"]

        mult = 100.0 * qty * side_sign
        total_delta += float(delta or 0.0) * mult
        total_gamma += float(gamma or 0.0) * mult
        total_theta += float(theta or 0.0) * mult
        total_vega += float(vega or 0.0) * mult

    return {
        "net_delta": round(total_delta, 2),
        "net_gamma": round(total_gamma, 4),
        "daily_theta": round(total_theta, 2),
        "net_vega": round(total_vega, 2),
    }

