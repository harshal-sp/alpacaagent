"""Technical indicators + IV rank + regime features."""
import pandas as pd
import numpy as np
from typing import Dict, Any

def rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    rsi_val = 100 - (100 / (1 + rs))
    return float(rsi_val.iloc[-1]) if not rsi_val.empty else 50.0

def atr(df: pd.DataFrame, period: int = 14) -> float:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return float(tr.ewm(alpha=1/period, min_periods=period).mean().iloc[-1]) if not tr.empty else 0.0

def ema(series: pd.Series, period: int) -> float:
    return float(series.ewm(span=period, min_periods=period).mean().iloc[-1]) if len(series) >= period else float(series.iloc[-1])

def iv_rank(current_iv: float, iv_history: list[float] | None = None) -> float:
    """IV Rank 0-100. If no history, estimate from current IV level."""
    if iv_history and len(iv_history) >= 20:
        low, high = min(iv_history), max(iv_history)
        if high == low:
            return 50.0
        return max(0, min(100, (current_iv - low) / (high - low) * 100))
    # heuristic: 15% -> 10 rank, 25% -> 50, 40% -> 90
    if current_iv < 0.15:
        return 10 + (current_iv - 0.1) / 0.05 * 15
    elif current_iv < 0.25:
        return 25 + (current_iv - 0.15) / 0.10 * 40
    else:
        return 65 + min(35, (current_iv - 0.25) / 0.15 * 35)

def compute_features(symbol: str, bars: pd.DataFrame, chain: list[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    if bars.empty:
        return {"symbol": symbol, "error": "no bars"}
    close = bars["close"]
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2]) if len(close) > 1 else last
    chg_pct = (last - prev) / prev * 100 if prev else 0
    # momentum
    ema20 = ema(close, 20) if len(close) >= 20 else last
    ema50 = ema(close, 50) if len(close) >= 50 else last
    trend = "uptrend" if ema20 > ema50 * 1.005 else "downtrend" if ema20 < ema50 * 0.995 else "sideways"
    # volatility
    atr_val = atr(bars, 14)
    atr_pct = atr_val / last * 100 if last else 0
    vol_20 = close.pct_change().rolling(20).std().iloc[-1] * np.sqrt(252) * 100 if len(close) >= 20 else 20.0
    if pd.isna(vol_20):
        vol_20 = 20.0
    # RSI
    rsi_val = rsi(close, 14)
    # IV estimation from chain
    avg_iv = 0.25
    if chain:
        ivs = [c.get("iv", 0.25) for c in chain if c.get("iv")]
        if ivs:
            avg_iv = float(np.mean(ivs))
    ivr = iv_rank(avg_iv)
    # regime heuristic
    if ivr > 30 and trend == "sideways" and atr_pct < 1.2:
        regime_hint = "range_high_iv"  # favors iron condor
    elif avg_iv < 0.18 and atr_pct < 1.0:
        regime_hint = "low_vol_compression"  # favors long straddle
    elif trend in ("uptrend", "downtrend") and atr_pct > 1.5:
        regime_hint = "trending_high_vol"  # favors directional
    else:
        regime_hint = "mixed"

    return {
        "symbol": symbol,
        "last": round(last, 2),
        "change_pct": round(chg_pct, 2),
        "ema20": round(ema20, 2),
        "ema50": round(ema50, 2),
        "trend": trend,
        "atr": round(atr_val, 3),
        "atr_pct": round(atr_pct, 3),
        "realized_vol_20d_annual": round(float(vol_20), 2),
        "rsi": round(float(rsi_val), 1),
        "avg_iv": round(avg_iv, 3),
        "iv_rank": round(float(ivr), 1),
        "regime_hint": regime_hint,
        "bars": len(bars),
    }
