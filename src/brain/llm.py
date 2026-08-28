"""LLM Brain — Featherless primary, OpenAI fallback, rules fallback."""
import json
import os
from typing import Dict, Any, List
import requests

from src.config import FEATHERLESS_API_KEY, FEATHERLESS_BASE_URL, FEATHERLESS_MODEL, OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL
from src.utils.logger import logger, log_event

SYSTEM_PROMPT = """You are Vega, an aggressive autonomous options alpha agent for a 7-day Alpaca paper trading hackathon.
You manage a $100k paper account trading 0-7 DTE options on SPY/QQQ and earnings names.
Your job: classify regime and pick ONE optimal options structure.

Rules:
- Be aggressive but risk-aware. Max 20% buying power per trade.
- Prefer short premium (iron condor / put spreads) when IV rank >30 and market is range-bound.
- Prefer long gamma (straddle/strangle) when IV is low but event is near (earnings within 3 days) or vol compression.
- Prefer directional long calls/puts when strong trend + high ATR.
- Never recommend naked options. Always spreads or defined-risk longs.
- Output strictly JSON.

Available strategies:
- IRON_CONDOR: 4 legs, high IV + sideways
- BULL_PUT_SPREAD: 2 legs bullish, moderate IV
- BEAR_CALL_SPREAD: 2 legs bearish, moderate IV
- LONG_STRADDLE: 2 legs (ATM call+put), pre-event
- LONG_STRANGLE: 2 legs (OTM call+put), cheaper gamma
- LONG_CALL: 1 leg directional up
- LONG_PUT: 1 leg directional down
- NO_TRADE: when risk too high or no edge

Output JSON:
{
  "regime": "range_high_iv|low_vol_compression|trending_high_vol|mixed|volatile",
  "confidence": 0.0-1.0,
  "strategy": "IRON_CONDOR|BULL_PUT_SPREAD|BEAR_CALL_SPREAD|LONG_STRADDLE|LONG_STRANGLE|LONG_CALL|LONG_PUT|NO_TRADE",
  "rationale": "one sentence",
  "symbol": "SPY/QQQ/etc",
  "bias": "neutral|bullish|bearish",
  "risk_note": "one sentence"
}
"""

def call_featherless(features: Dict[str, Any], earnings: List[Dict] | None = None) -> Dict[str, Any] | None:
    if not FEATHERLESS_API_KEY:
        return None
    user_payload = {
        "features": features,
        "earnings_watch": earnings or [],
        "task": "Classify regime and pick best options structure for aggressive P&L in next 1-3 days."
    }
    try:
        resp = requests.post(
            f"{FEATHERLESS_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {FEATHERLESS_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": FEATHERLESS_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(user_payload)},
                ],
                "temperature": 0.3,
                "max_tokens": 500,
                "response_format": {"type": "json_object"} if "llama" not in FEATHERLESS_MODEL.lower() else None,
            },
            timeout=20,
        )
        if resp.status_code != 200:
            logger.warning(f"Featherless {resp.status_code}: {resp.text[:300]}")
            return None
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        # extract JSON
        parsed = json.loads(content.strip().removeprefix("```json").removesuffix("```").strip())
        log_event("llm_featherless", model=FEATHERLESS_MODEL, output=parsed)
        return parsed
    except Exception as e:
        logger.warning(f"Featherless call failed: {e}")
        return None

def call_openai_fallback(features: Dict[str, Any], earnings: List[Dict] | None = None) -> Dict[str, Any] | None:
    if not OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL or None)
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"features": features, "earnings_watch": earnings or []})},
            ],
            temperature=0.3,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or "{}"
        parsed = json.loads(content)
        log_event("llm_openai", model=OPENAI_MODEL, output=parsed)
        return parsed
    except Exception as e:
        logger.warning(f"OpenAI fallback failed: {e}")
        return None

def rules_classifier(features: Dict[str, Any], earnings: List[Dict] | None = None) -> Dict[str, Any]:
    """Deterministic fallback — aggressive: rarely NO_TRADE, favors defined-risk spreads."""
    ivr = features.get("iv_rank", 25)
    trend = features.get("trend", "sideways")
    rsi = features.get("rsi", 50)
    atr_pct = features.get("atr_pct", 1.0)
    symbol = features.get("symbol", "SPY")
    # earnings near?
    has_earnings = False
    earn_days = 99
    if earnings:
        for e in earnings:
            if e["ticker"] == symbol and e.get("days_ahead", 99) <= 3:
                has_earnings = True
                earn_days = e.get("days_ahead", 99)
                break
    # 1) Earnings gamma — highest priority
    if has_earnings and ivr < 55:
        return {"regime": "low_vol_compression", "confidence": 0.80, "strategy": "LONG_STRADDLE", "rationale": f"{symbol} earnings in {earn_days}d with IVR {ivr} — cheap gamma before event", "symbol": symbol, "bias": "neutral", "risk_note": "Defined-risk long gamma, max loss premium"}
    # 2) High IV + sideways -> theta decay (aggressive threshold 22)
    if ivr > 22 and trend == "sideways" and atr_pct < 2.0:
        return {"regime": "range_high_iv", "confidence": 0.82, "strategy": "IRON_CONDOR", "rationale": f"IVR {ivr} elevated + sideways + ATR {atr_pct}% favors theta", "symbol": symbol, "bias": "neutral", "risk_note": "Iron condor max loss = width - credit, defined"}
    # 3) Trending with decent vol -> directional spreads (income + trend)
    if trend == "uptrend" and (rsi < 72 or atr_pct > 1.0):
        return {"regime": "trending_high_vol", "confidence": 0.76, "strategy": "BULL_PUT_SPREAD", "rationale": f"Uptrend RSI {rsi} ATR {atr_pct}% favors put spread income with trend", "symbol": symbol, "bias": "bullish", "risk_note": "Spread max loss limited"}
    if trend == "downtrend" and (rsi > 28 or atr_pct > 1.0):
        return {"regime": "trending_high_vol", "confidence": 0.76, "strategy": "BEAR_CALL_SPREAD", "rationale": f"Downtrend RSI {rsi} ATR {atr_pct}% favors call spread income", "symbol": symbol, "bias": "bearish", "risk_note": "Defined-risk spread"}
    # 4) Low vol compression -> cheap gamma (aggressive: atr < 2.0 not 1.0)
    if ivr < 22 and atr_pct < 2.0:
        return {"regime": "low_vol_compression", "confidence": 0.71, "strategy": "LONG_STRANGLE", "rationale": f"IVR {ivr} low + ATR {atr_pct}% compressed — long strangle cheap gamma", "symbol": symbol, "bias": "neutral", "risk_note": "Long gamma max loss premium"}
    # 5) Fallbacks — momentum longs before giving up
    if trend == "uptrend":
        return {"regime": "mixed", "confidence": 0.62, "strategy": "LONG_CALL", "rationale": f"Momentum uptrend favors long call, IVR {ivr} ATR {atr_pct}%", "symbol": symbol, "bias": "bullish", "risk_note": "Max loss premium"}
    if trend == "downtrend":
        return {"regime": "mixed", "confidence": 0.62, "strategy": "LONG_PUT", "rationale": f"Momentum downtrend favors long put, IVR {ivr} ATR {atr_pct}%", "symbol": symbol, "bias": "bearish", "risk_note": "Max loss premium"}
    # 6) Even if sideways with moderate IV, still trade — iron condor is safest income for 7-day window
    if ivr > 15:
        return {"regime": "mixed", "confidence": 0.60, "strategy": "IRON_CONDOR", "rationale": f"Sideways IVR {ivr} condor income for week window", "symbol": symbol, "bias": "neutral", "risk_note": "Defined-risk condor"}
    # 7) Last resort — long strangle still better than NO_TRADE for aggressive profile
    return {"regime": "mixed", "confidence": 0.58, "strategy": "LONG_STRANGLE", "rationale": f"Low IVR {ivr} fallback long strangle for gamma", "symbol": symbol, "bias": "neutral", "risk_note": "Premium-defined gamma"}

def classify(symbol_features: Dict[str, Any], earnings: List[Dict] | None = None) -> Dict[str, Any]:
    """Try LLM, fallback to rules. Always returns valid dict."""
    # Try Featherless first
    out = call_featherless(symbol_features, earnings)
    if out and "strategy" in out:
        out["source"] = "featherless"
        # validate strategy
        valid = {"IRON_CONDOR","BULL_PUT_SPREAD","BEAR_CALL_SPREAD","LONG_STRADDLE","LONG_STRANGLE","LONG_CALL","LONG_PUT","NO_TRADE"}
        if out.get("strategy") not in valid:
            logger.warning(f"LLM returned invalid strategy {out.get('strategy')} — falling back to rules")
            out = None
        else:
            # ensure symbol populated
            if not out.get("symbol"):
                out["symbol"] = symbol_features.get("symbol", "SPY")
            return out
    # Try OpenAI
    out = call_openai_fallback(symbol_features, earnings)
    if out and "strategy" in out:
        out["source"] = "openai"
        valid = {"IRON_CONDOR","BULL_PUT_SPREAD","BEAR_CALL_SPREAD","LONG_STRADDLE","LONG_STRANGLE","LONG_CALL","LONG_PUT","NO_TRADE"}
        if out.get("strategy") in valid:
            if not out.get("symbol"):
                out["symbol"] = symbol_features.get("symbol", "SPY")
            return out
    # Rules
    out = rules_classifier(symbol_features, earnings)
    out["source"] = "rules"
    log_event("llm_rules", symbol=symbol_features.get("symbol"), output=out)
    return out
