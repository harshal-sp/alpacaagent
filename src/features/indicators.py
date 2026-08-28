"""Technical indicators + IV rank + regime features — v3 with VRP, IV Skew, Bollinger Squeeze, MACD, VWAP."""
import pandas as pd
import numpy as np
from typing import Dict, Any, List

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

def sma(series: pd.Series, period: int) -> float:
    return float(series.rolling(period).mean().iloc[-1]) if len(series) >= period else float(series.iloc[-1])

def bollinger(series: pd.Series, period: int = 20, std: int = 2) -> Dict[str, float]:
    if len(series) < period:
        last = float(series.iloc[-1])
        return {"mid": last, "upper": last*1.02, "lower": last*0.98, "width_pct": 4.0, "pct_b": 0.5, "is_squeeze": False}
    mid = series.rolling(period).mean().iloc[-1]
    sd = series.rolling(period).std().iloc[-1]
    upper = mid + std*sd
    lower = mid - std*sd
    width_pct = (upper - lower)/mid*100 if mid else 0
    last = float(series.iloc[-1])
    pct_b = (last - lower)/(upper - lower) if upper != lower else 0.5
    # Squeeze detected if BB width is under 3.5%
    is_squeeze = bool(width_pct < 3.5)
    return {
        "mid": round(float(mid), 2),
        "upper": round(float(upper), 2),
        "lower": round(float(lower), 2),
        "width_pct": round(float(width_pct), 2),
        "pct_b": round(float(pct_b), 3),
        "is_squeeze": is_squeeze,
    }

def macd(series: pd.Series) -> Dict[str, float]:
    if len(series) < 26:
        return {"macd": 0, "signal": 0, "hist": 0, "bullish": False}
    ema12 = series.ewm(span=12, min_periods=12).mean()
    ema26 = series.ewm(span=26, min_periods=26).mean()
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9, min_periods=9).mean()
    hist = macd_line - signal
    return {
        "macd": round(float(macd_line.iloc[-1]), 3),
        "signal": round(float(signal.iloc[-1]), 3),
        "hist": round(float(hist.iloc[-1]), 3),
        "bullish": bool(hist.iloc[-1] > 0)
    }

def vwap(df: pd.DataFrame) -> float:
    if "volume" not in df.columns or df["volume"].sum() == 0:
        return float(df["close"].iloc[-1])
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return float((tp * df["volume"]).sum() / df["volume"].sum())

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

def calculate_iv_skew(chain: List[Dict[str, Any]], spot: float) -> Dict[str, float]:
    """Calculate Put/Call IV skew ratio and 25-delta spread."""
    if not chain or spot <= 0:
        return {"put_call_iv_ratio": 1.0, "skew_spread_pct": 0.0}
    otm_puts = [c.get("iv", 0) for c in chain if c.get("type") == "put" and c.get("strike", 0) < spot * 0.98 and c.get("iv")]
    otm_calls = [c.get("iv", 0) for c in chain if c.get("type") == "call" and c.get("strike", 0) > spot * 1.02 and c.get("iv")]
    avg_put_iv = float(np.mean(otm_puts)) if otm_puts else 0.25
    avg_call_iv = float(np.mean(otm_calls)) if otm_calls else 0.25
    ratio = avg_put_iv / avg_call_iv if avg_call_iv > 0 else 1.0
    spread = (avg_put_iv - avg_call_iv) * 100
    return {
        "put_call_iv_ratio": round(ratio, 3),
        "skew_spread_pct": round(spread, 2),
    }

def support_resistance(df: pd.DataFrame) -> Dict[str, float]:
    if len(df) < 20:
        last = float(df["close"].iloc[-1])
        return {"resistance": last*1.01, "support": last*0.99, "dist_to_res_pct": 1.0, "dist_to_sup_pct": -1.0}
    high20 = df["high"].rolling(20).max().iloc[-1]
    low20 = df["low"].rolling(20).min().iloc[-1]
    last = float(df["close"].iloc[-1])
    return {
        "resistance": round(float(high20), 2),
        "support": round(float(low20), 2),
        "dist_to_res_pct": round((high20 - last)/last*100, 2) if last else 0,
        "dist_to_sup_pct": round((last - low20)/last*100, 2) if last else 0,
    }

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
    sma20 = sma(close, 20) if len(close) >= 20 else last
    trend = "uptrend" if ema20 > ema50 * 1.005 else "downtrend" if ema20 < ema50 * 0.995 else "sideways"
    # volatility
    atr_val = atr(bars, 14)
    atr_pct = atr_val / last * 100 if last else 0
    vol_20 = close.pct_change().rolling(20).std().iloc[-1] * np.sqrt(252) * 100 if len(close) >= 20 else 20.0
    if pd.isna(vol_20):
        vol_20 = 20.0
    # RSI
    rsi_val = rsi(close, 14)
    # Bollinger
    bb = bollinger(close, 20, 2)
    # MACD
    macd_val = macd(close)
    # VWAP & volume
    vwap_val = vwap(bars)
    vol_avg = bars["volume"].rolling(20).mean().iloc[-1] if len(bars) >= 20 else bars["volume"].mean()
    vol_ratio = float(bars["volume"].iloc[-1] / vol_avg) if vol_avg else 1.0
    # S/R
    sr = support_resistance(bars)
    # IV estimation & Skew from chain
    avg_iv = 0.25
    if chain:
        ivs = [c.get("iv", 0.25) for c in chain if c.get("iv")]
        if ivs:
            avg_iv = float(np.mean(ivs))
    ivr = iv_rank(avg_iv)
    skew = calculate_iv_skew(chain or [], last)

    # Volatility Risk Premium (VRP): Implied Vol (Annualized %) - Realized Vol (Annualized %)
    # Positive VRP indicates implied volatility is rich relative to historical move (edge for selling theta)
    vrp = (avg_iv * 100) - vol_20

    # Regime heuristic v3 — integrates VRP, Squeeze, Skew, MACD, Volume
    if (ivr > 28 or vrp > 4.0) and trend == "sideways" and bb["width_pct"] < 4.5:
        regime_hint = "range_high_iv"  # Theta income / Iron Condor
    elif (ivr < 20 or bb["is_squeeze"]) and atr_pct < 1.3:
        regime_hint = "low_vol_compression"  # Pre-event Gamma / Straddle / Strangle
    elif trend in ("uptrend", "downtrend") and (atr_pct > 1.2 or abs(macd_val["hist"]) > 0.4):
        regime_hint = "trending_high_vol"  # Directional Spreads (Bull Put / Bear Call)
    elif vol_ratio > 1.6 and ivr > 35:
        regime_hint = "volatile"  # Heightened risk — avoid naked/tight wings
    else:
        regime_hint = "mixed"

    return {
        "symbol": symbol,
        "last": round(last, 2),
        "change_pct": round(chg_pct, 2),
        "ema20": round(ema20, 2),
        "ema50": round(ema50, 2),
        "sma20": round(sma20, 2),
        "trend": trend,
        "atr": round(atr_val, 3),
        "atr_pct": round(atr_pct, 3),
        "realized_vol_20d_annual": round(float(vol_20), 2),
        "rsi": round(float(rsi_val), 1),
        "bb_mid": bb["mid"],
        "bb_upper": bb["upper"],
        "bb_lower": bb["lower"],
        "bb_width_pct": bb["width_pct"],
        "bb_pct_b": bb["pct_b"],
        "bb_squeeze": bb["is_squeeze"],
        "macd": macd_val["macd"],
        "macd_signal": macd_val["signal"],
        "macd_hist": macd_val["hist"],
        "macd_bullish": macd_val["bullish"],
        "vwap": round(float(vwap_val), 2),
        "vwap_dist_pct": round((last - vwap_val)/vwap_val*100, 2) if vwap_val else 0,
        "volume_ratio": round(float(vol_ratio), 2),
        "resistance": sr["resistance"],
        "support": sr["support"],
        "dist_to_res_pct": sr["dist_to_res_pct"],
        "dist_to_sup_pct": sr["dist_to_sup_pct"],
        "avg_iv": round(avg_iv, 3),
        "iv_rank": round(float(ivr), 1),
        "vrp": round(float(vrp), 2),
        "put_call_iv_ratio": skew["put_call_iv_ratio"],
        "skew_spread_pct": skew["skew_spread_pct"],
        "regime_hint": regime_hint,
        "bars": len(bars),
    }

