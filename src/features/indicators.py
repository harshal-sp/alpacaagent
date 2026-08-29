"""Technical indicators + IV rank + regime features — v4 Fireworks: VRP, Skew, Squeeze, MACD, VWAP, EM, RV term, OI, soft regime."""
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

def _rv(close: pd.Series, period: int) -> float:
    if len(close) < period + 1:
        return 20.0
    v = close.pct_change().rolling(period).std().iloc[-1] * np.sqrt(252) * 100
    return float(v) if not pd.isna(v) else 20.0

def _expected_move_pct(chain: List[Dict[str, Any]], spot: float) -> float:
    if not chain or spot <= 0:
        return 0.0
    calls = [c for c in chain if c.get("type") == "call"]
    puts = [c for c in chain if c.get("type") == "put"]
    if not calls or not puts:
        return 0.0
    atm_call = min(calls, key=lambda c: abs(c.get("strike", spot) - spot))
    atm_put = min(puts, key=lambda c: abs(c.get("strike", spot) - spot))
    # mid price helper inline
    def mid(c):
        bid, ask, last = c.get("bid", 0) or 0, c.get("ask", 0) or 0, c.get("last", 0) or 0
        if bid and ask and ask > bid and bid > 0.05:
            return (bid + ask) / 2.0
        return float(last or bid or ask or 0)
    straddle_mid = mid(atm_call) + mid(atm_put)
    return round(straddle_mid / spot * 100, 2) if spot else 0.0

def _oi_concentration(chain: List[Dict[str, Any]], spot: float) -> Dict[str, float]:
    if not chain or spot <= 0:
        return {"oi_concentration": 0.0, "max_oi_strike": 0.0}
    # find strike with max OI within 5% band
    band = [c for c in chain if abs(c.get("strike", spot) - spot) / spot < 0.05 and c.get("openInterest")]
    if not band:
        return {"oi_concentration": 0.0, "max_oi_strike": 0.0}
    best = max(band, key=lambda c: c.get("openInterest", 0) or 0)
    max_oi = float(best.get("openInterest", 0) or 0)
    total_oi = sum(float(c.get("openInterest", 0) or 0) for c in band)
    conc = max_oi / total_oi if total_oi else 0
    return {"oi_concentration": round(conc, 3), "max_oi_strike": float(best.get("strike", 0))}

def _soft_regime_scores(ivr: float, vrp: float, trend: str, bb_width: float, bb_squeeze: bool, atr_pct: float, macd_hist: float, vol_ratio: float) -> Dict[str, float]:
    """Return soft scores [0,1] for each regime instead of hard threshold."""
    def clamp(x): return max(0.0, min(1.0, x))
    # Range high IV: high VRP/IVR + sideways + compressed width
    s_range = 0.0
    s_range += clamp((ivr - 15) / 25) * 0.35
    s_range += clamp(vrp / 6.0) * 0.35
    s_range += (0.2 if trend == "sideways" else 0.0)
    s_range += clamp((5.5 - bb_width) / 5.5) * 0.1
    # Low vol compression
    s_low = 0.0
    s_low += (0.4 if bb_squeeze else clamp((3.5 - bb_width) / 3.5) * 0.3)
    s_low += clamp((25 - ivr) / 25) * 0.3
    s_low += clamp((1.5 - atr_pct) / 1.5) * 0.3
    # Trending high vol
    s_trend = 0.0
    s_trend += (0.3 if trend in ("uptrend", "downtrend") else 0.0)
    s_trend += clamp(atr_pct / 2.5) * 0.35
    s_trend += clamp(abs(macd_hist) / 1.0) * 0.35
    # Volatile
    s_vol = 0.0
    s_vol += clamp((vol_ratio - 1.0) / 1.0) * 0.5
    s_vol += clamp((ivr - 25) / 40) * 0.5
    return {"range_high_iv": round(clamp(s_range), 3), "low_vol_compression": round(clamp(s_low), 3), "trending_high_vol": round(clamp(s_trend), 3), "volatile": round(clamp(s_vol), 3), "mixed": 0.3}


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
    vrp = (avg_iv * 100) - vol_20
    # RV term structure
    rv_5 = _rv(close, 5)
    rv_60 = _rv(close, 60)
    rv_term = "contango" if rv_5 < vol_20 < rv_60 else "backwardation" if rv_5 > vol_20 else "flat"
    # Expected move from ATM straddle
    exp_move_pct = _expected_move_pct(chain or [], last)
    oi_stats = _oi_concentration(chain or [], last)
    # Soft regime scoring (v4) + hard hint for backward compat
    scores = _soft_regime_scores(ivr, vrp, trend, bb["width_pct"], bb["is_squeeze"], atr_pct, macd_val["hist"], vol_ratio)
    # Pick regime by max soft score, apply hysteresis-friendly thresholds
    regime_hint = max(scores, key=lambda k: scores[k])
    # Hard override to keep legacy behavior familiar to LLM prompt when scores weak
    if (ivr > 28 or vrp > 4.0) and trend == "sideways" and bb["width_pct"] < 4.5:
        if scores["range_high_iv"] > 0.45:
            regime_hint = "range_high_iv"
    if (ivr < 20 or bb["is_squeeze"]) and atr_pct < 1.3 and scores["low_vol_compression"] > 0.45:
        regime_hint = "low_vol_compression"

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
        "regime_scores": scores,
        "realized_vol_5d_annual": round(float(rv_5), 2),
        "realized_vol_60d_annual": round(float(rv_60), 2),
        "rv_term_structure": rv_term,
        "expected_move_pct": exp_move_pct,
        "oi_concentration": oi_stats["oi_concentration"],
        "max_oi_strike": oi_stats["max_oi_strike"],
        "bars": len(bars),
    }

