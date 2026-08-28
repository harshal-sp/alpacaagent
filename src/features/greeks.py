"""Black-Scholes greeks — for spread selection and risk."""
import math
from typing import Dict
from scipy.stats import norm

def bs_greeks(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "call") -> Dict[str, float]:
    """Return delta, gamma, theta (per day), vega, price. Sigma clamped to [0.1, 0.8]."""
    try:
        import pandas as pd
        if sigma is None or pd.isna(sigma):
            sigma = 0.25
    except:
        if not sigma:
            sigma = 0.25
    try:
        sigma = float(sigma)
    except:
        sigma = 0.25
    sigma = max(0.10, min(0.80, sigma))
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        # expiry today — intrinsic
        intrinsic = max(0, S - K) if option_type == "call" else max(0, K - S)
        delta = 1.0 if option_type == "call" and S > K else 0.0 if option_type=="call" else (-1.0 if S < K else 0.0)
        return {"delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "price": intrinsic}
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    nd1 = norm.cdf(d1)
    nd2 = norm.cdf(d2)
    pdf_d1 = norm.pdf(d1)
    disc = math.exp(-r * T)
    if option_type == "call":
        price = S * nd1 - K * disc * nd2
        delta = nd1
        theta = -(S * pdf_d1 * sigma) / (2 * math.sqrt(T)) - r * K * disc * nd2
    else:
        price = K * disc * (1 - nd2) - S * (1 - nd1)
        delta = nd1 - 1
        theta = -(S * pdf_d1 * sigma) / (2 * math.sqrt(T)) + r * K * disc * (1 - nd2)
    gamma = pdf_d1 / (S * sigma * math.sqrt(T))
    vega = S * pdf_d1 * math.sqrt(T) / 100  # per 1% vol

    # theta per day
    return {
        "delta": round(float(delta), 3),
        "gamma": round(float(gamma), 4),
        "theta": round(float(theta / 365), 4),
        "vega": round(float(vega), 4),
        "price": round(float(price), 2),
    }

def option_mid_price(contract: dict) -> float:
    bid, ask = contract.get("bid", 0), contract.get("ask", 0)
    last = contract.get("last", 0)
    if bid and ask and ask > bid:
        return (bid + ask) / 2
    return last if last else (bid or ask or 0)

def describe_chain_greeks(spot: float, chain: list[dict], T_days: int = 3, r: float = 0.045) -> list[dict]:
    T = max(0.01, T_days / 365)
    out = []
    for c in chain:
        K = c["strike"]
        sigma = c.get("iv", 0.25) or 0.25
        greeks = bs_greeks(spot, K, T, r, sigma, c["type"])
        out.append({**c, **greeks, "mid": round(option_mid_price(c), 2)})
    return out
