"""LLM Brain — Ling 3.0 Flash Fin (primary finance-native) → Featherless → OpenAI → Rules."""
import json
import os
import re
from typing import Dict, Any, List
import requests

from src.config import (
    LING_API_KEY, LING_BASE_URL, LING_MODEL, OPENCODE_LING_MODEL,
    FEATHERLESS_API_KEY, FEATHERLESS_BASE_URL, FEATHERLESS_MODEL,
    OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL,
    AI_GATEWAY_API_KEY, AI_GATEWAY_BASE_URL
)
from src.utils.logger import logger, log_event

def _extract_json(text: str) -> Dict[str, Any] | None:
    """Robust JSON extractor supporting markdown blocks, trailing tokens, or conversational wrappers."""
    if not text:
        return None
    cleaned = text.strip()
    # Remove markdown code fences
    if "```" in cleaned:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1)
        else:
            parts = cleaned.split("```")
            if len(parts) >= 3:
                cleaned = parts[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
    # Direct search for outermost JSON object
    match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(1)
    try:
        return json.loads(cleaned.strip())
    except Exception as e:
        logger.debug(f"JSON parsing error: {e} | Raw text: {text[:200]}")
        return None

# Finance-native prompt optimized for Ling 3.0 Flash Fin (124B MoE, 5.1B active, 256K context, finance-tuned on FinFIRST/FinSearchComp/FinanceAgent)
LING_FIN_SYSTEM_PROMPT = """You are Vega-Fin, an autonomous options alpha quant built on Ling 3.0 Flash Fin.
You manage a $100k Alpaca paper account trading 0-7 DTE options on SPY/QQQ and 6 earnings names. You have 256K context and finance tool-calling.

Mission: For a 7-day hackathon P&L sprint, pick ONE optimal defined-risk options structure that maximizes risk-adjusted P&L over next 1-3 trading days.

Context you receive: {symbol, last, change_pct, ema20/50, trend, atr, atr_pct, realized_vol_20d_annual, rsi, avg_iv, iv_rank, vrp, put_call_iv_ratio, bb_squeeze, regime_hint, bars} + earnings_watch.

Finance reasoning steps (think step-by-step, 3-5 lines internally):
1. Regime & Edge:
   - Volatility Risk Premium (VRP = IV - RV): If VRP > 4.0% and IVR > 25 → Implied Vol is rich, favor selling premium (IRON_CONDOR, BULL_PUT_SPREAD, BEAR_CALL_SPREAD).
   - Low Vol Squeeze: If bb_squeeze=True or (IVR < 20 and VRP < 1.0%) with upcoming catalyst → Cheap gamma, favor LONG_STRADDLE or LONG_STRANGLE.
   - Trend Momentum: If strong EMA trend + high ATR%, pick directional spread aligned with trend.
2. Structure selection (only defined-risk):
   - range_high_iv / high VRP → IRON_CONDOR (4 legs, 15Δ short / 5Δ wings)
   - low_vol_compression + earnings≤3d → LONG_STRADDLE (ATM call+put)
   - low_vol_compression without earnings / squeeze → LONG_STRANGLE (OTM call+put)
   - trending_high_vol + uptrend → BULL_PUT_SPREAD (20Δ short put / 7Δ long put)
   - trending_high_vol + downtrend → BEAR_CALL_SPREAD (20Δ short call / 7Δ long call)
   - high directional momentum → LONG_CALL (uptrend) / LONG_PUT (downtrend)
3. Risk: Max 20% BP/trade, max loss = width-credit (spreads) or premium (longs). Never naked. Reject if net credit after $0.17/contract fees < $0.10/share.

Output strictly JSON (no markdown):
{
  "regime": "range_high_iv|low_vol_compression|trending_high_vol|mixed|volatile",
  "confidence": 0.0-1.0,
  "strategy": "IRON_CONDOR|BULL_PUT_SPREAD|BEAR_CALL_SPREAD|LONG_STRADDLE|LONG_STRANGLE|LONG_CALL|LONG_PUT|NO_TRADE",
  "rationale": "one sentence finance-aware explanation",
  "symbol": "SPY/QQQ/etc",
  "bias": "neutral|bullish|bearish",
  "risk_note": "one sentence with max loss and wing definition",
  "edge_bps": 0,
  "expected_move_pct": 0.0
}
"""

SYSTEM_PROMPT = """You are Vega, an aggressive autonomous options alpha agent for a 7-day Alpaca paper trading hackathon.
You manage a $100k paper account trading 0-7 DTE options on SPY/QQQ and earnings names.
Your job: classify regime and pick ONE optimal options structure.

Rules:
- Be aggressive but risk-aware. Max 20% buying power per trade.
- Prefer short premium (iron condor / put spreads) when IV rank >22 or VRP is positive and market is range-bound.
- Prefer long gamma (straddle/strangle) when IV is low, squeeze is active, or event is near (earnings within 3 days).
- Prefer directional long calls/puts when strong trend + high ATR.
- Never recommend naked options. Always spreads or defined-risk longs.
- Output strictly JSON.

Available strategies:
- IRON_CONDOR: 4 legs, high IV / high VRP + sideways
- BULL_PUT_SPREAD: 2 legs bullish, moderate/high IV
- BEAR_CALL_SPREAD: 2 legs bearish, moderate/high IV
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

def call_ling_fin_flash(features: Dict[str, Any], earnings: List[Dict] | None = None) -> Dict[str, Any] | None:
    """Primary: Ling 3.0 Flash Fin via OpenRouter free, Vercel AI Gateway, or Opencode proxy."""
    user_payload = {
        "features": features,
        "earnings_watch": earnings or [],
        "task": "Pick best defined-risk options structure for aggressive 1-3 day P&L. Return strict JSON.",
        "fee_hint": "Subtract $0.17/contract round-trip before edge. Reject if net credit <0.10/share.",
    }
    endpoints = []
    # OpenRouter free tier
    endpoints.append({
        "url": f"{LING_BASE_URL.rstrip('/')}/chat/completions",
        "key": LING_API_KEY or os.getenv("OPENROUTER_API_KEY", ""),
        "model": LING_MODEL,
        "headers_extra": {"HTTP-Referer": "https://lablab.ai", "X-Title": "Vega Options Alpha"},
    })
    # Vercel AI Gateway
    if AI_GATEWAY_API_KEY:
        endpoints.append({
            "url": f"{AI_GATEWAY_BASE_URL.rstrip('/')}/chat/completions",
            "key": AI_GATEWAY_API_KEY,
            "model": "inclusionai/ling-3.0-flash-fin",
            "headers_extra": {},
        })
    # Direct Ant Ling API
    if os.getenv("ANT_LING_API_KEY"):
        endpoints.append({
            "url": "https://api.ling.ai/v1/chat/completions",
            "key": os.getenv("ANT_LING_API_KEY"),
            "model": "ling-3.0-flash-fin",
            "headers_extra": {},
        })

    for ep in endpoints:
        try:
            headers = {"Content-Type": "application/json"}
            if ep["key"]:
                headers["Authorization"] = f"Bearer {ep['key']}"
            body = {
                "model": ep["model"],
                "messages": [
                    {"role": "system", "content": LING_FIN_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(user_payload)},
                ],
                "temperature": 0.2,
                "max_tokens": 600,
                "response_format": {"type": "json_object"},
            }
            if "ling" in ep["model"]:
                body["reasoning"] = {"effort": "medium"}
            resp = requests.post(ep["url"], headers={**headers, **ep["headers_extra"]}, json=body, timeout=25)
            if resp.status_code != 200:
                logger.debug(f"Ling {ep['url']} {ep['model']} {resp.status_code}: {resp.text[:300]}")
                continue
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            parsed = _extract_json(content)
            if parsed and "strategy" in parsed:
                log_event("llm_ling_fin", model=ep["model"], url=ep["url"], output=parsed)
                return parsed
        except Exception as e:
            logger.debug(f"Ling endpoint {ep['url']} failed: {e}")
            continue

    logger.info("Ling Fin Flash all endpoints failed or no key — falling back to Featherless/OpenAI/Rules")
    return None

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
        parsed = _extract_json(content)
        if parsed and "strategy" in parsed:
            log_event("llm_featherless", model=FEATHERLESS_MODEL, output=parsed)
            return parsed
        return None
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
        parsed = _extract_json(content)
        if parsed and "strategy" in parsed:
            log_event("llm_openai", model=OPENAI_MODEL, output=parsed)
            return parsed
        return None
    except Exception as e:
        logger.warning(f"OpenAI fallback failed: {e}")
        return None

def rules_classifier(features: Dict[str, Any], earnings: List[Dict] | None = None) -> Dict[str, Any]:
    """Deterministic quantitative fallback engine — incorporates VRP, Squeeze, Skew, and Technicals."""
    ivr = features.get("iv_rank", 25)
    vrp = features.get("vrp", 0.0)
    trend = features.get("trend", "sideways")
    rsi_val = features.get("rsi", 50)
    atr_pct = features.get("atr_pct", 1.0)
    is_squeeze = features.get("bb_squeeze", False)
    put_call_ratio = features.get("put_call_iv_ratio", 1.0)
    symbol = features.get("symbol", "SPY")

    has_earnings = False
    earn_days = 99
    if earnings:
        for e in earnings:
            if e.get("ticker") == symbol and e.get("days_ahead", 99) <= 3:
                has_earnings = True
                earn_days = e.get("days_ahead", 99)
                break

    # 1. Earnings catalyst with cheap/moderate IV -> LONG_STRADDLE
    if has_earnings and ivr < 60:
        return {
            "regime": "low_vol_compression",
            "confidence": 0.85,
            "strategy": "LONG_STRADDLE",
            "rationale": f"{symbol} earnings in {earn_days}d (IVR {ivr}) — cheap gamma capture before catalyst move",
            "symbol": symbol,
            "bias": "neutral",
            "risk_note": "Defined-risk ATM straddle, max loss = total debit paid",
            "edge_bps": 120,
        }

    # 2. Bollinger Squeeze setup -> LONG_STRANGLE
    if is_squeeze and ivr < 30 and not has_earnings:
        return {
            "regime": "low_vol_compression",
            "confidence": 0.80,
            "strategy": "LONG_STRANGLE",
            "rationale": f"{symbol} BB squeeze compression (IVR {ivr}, ATR {atr_pct}%) — positioning for explosive breakout",
            "symbol": symbol,
            "bias": "neutral",
            "risk_note": "Defined-risk OTM strangle, max loss = debit",
            "edge_bps": 85,
        }

    # 3. High IV Rank or Positive VRP in sideways market -> IRON_CONDOR
    if (ivr > 25 or vrp > 3.5) and trend == "sideways" and atr_pct < 2.2:
        return {
            "regime": "range_high_iv",
            "confidence": 0.84,
            "strategy": "IRON_CONDOR",
            "rationale": f"{symbol} elevated IVR {ivr} and positive VRP {vrp:.1f}% in range-bound structure favors theta decay",
            "symbol": symbol,
            "bias": "neutral",
            "risk_note": "Defined-risk 4-leg condor, max loss = wing width - credit collected",
            "edge_bps": 95,
        }

    # 4. Trending Uptrend with put skew / positive momentum -> BULL_PUT_SPREAD
    if trend == "uptrend" and (rsi_val < 72 or atr_pct > 1.0):
        return {
            "regime": "trending_high_vol",
            "confidence": 0.78,
            "strategy": "BULL_PUT_SPREAD",
            "rationale": f"{symbol} uptrend momentum (RSI {rsi_val}, ATR {atr_pct}%) favors selling high-IV OTM put spread",
            "symbol": symbol,
            "bias": "bullish",
            "risk_note": "Defined-risk vertical put credit spread, max loss = strike width - credit",
            "edge_bps": 75,
        }

    # 5. Trending Downtrend with call skew -> BEAR_CALL_SPREAD
    if trend == "downtrend" and (rsi_val > 28 or atr_pct > 1.0):
        return {
            "regime": "trending_high_vol",
            "confidence": 0.78,
            "strategy": "BEAR_CALL_SPREAD",
            "rationale": f"{symbol} downtrend momentum (RSI {rsi_val}, ATR {atr_pct}%) favors selling OTM call credit spread",
            "symbol": symbol,
            "bias": "bearish",
            "risk_note": "Defined-risk vertical call credit spread, max loss = strike width - credit",
            "edge_bps": 75,
        }

    # 6. Low IV compression without earnings -> LONG_STRANGLE
    if ivr < 22 and atr_pct < 2.0:
        return {
            "regime": "low_vol_compression",
            "confidence": 0.72,
            "strategy": "LONG_STRANGLE",
            "rationale": f"{symbol} low IVR {ivr} + compressed ATR {atr_pct}% allows cheap gamma accumulation",
            "symbol": symbol,
            "bias": "neutral",
            "risk_note": "Defined-risk long gamma, max loss = total debit",
            "edge_bps": 60,
        }

    # 7. Directional breakout momentum -> LONG_CALL / LONG_PUT
    if trend == "uptrend":
        return {
            "regime": "mixed",
            "confidence": 0.65,
            "strategy": "LONG_CALL",
            "rationale": f"{symbol} momentum continuation long call (IVR {ivr}, ATR {atr_pct}%)",
            "symbol": symbol,
            "bias": "bullish",
            "risk_note": "Max loss = premium paid",
            "edge_bps": 50,
        }
    if trend == "downtrend":
        return {
            "regime": "mixed",
            "confidence": 0.65,
            "strategy": "LONG_PUT",
            "rationale": f"{symbol} momentum continuation long put (IVR {ivr}, ATR {atr_pct}%)",
            "symbol": symbol,
            "bias": "bearish",
            "risk_note": "Max loss = premium paid",
            "edge_bps": 50,
        }

    # 8. General fallback condor
    if ivr > 15:
        return {
            "regime": "mixed",
            "confidence": 0.62,
            "strategy": "IRON_CONDOR",
            "rationale": f"{symbol} range-bound condor income for multi-day horizon (IVR {ivr})",
            "symbol": symbol,
            "bias": "neutral",
            "risk_note": "Defined-risk 4-leg condor",
            "edge_bps": 40,
        }

    return {
        "regime": "mixed",
        "confidence": 0.58,
        "strategy": "LONG_STRANGLE",
        "rationale": f"{symbol} low IVR {ivr} fallback strangle",
        "symbol": symbol,
        "bias": "neutral",
        "risk_note": "Defined-risk strangle",
        "edge_bps": 30,
    }

def classify(symbol_features: Dict[str, Any], earnings: List[Dict] | None = None) -> Dict[str, Any]:
    """Try Ling Fin Flash → Featherless → OpenAI → Rules. Always returns a valid structured dictionary."""
    valid = {"IRON_CONDOR", "BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "LONG_STRADDLE", "LONG_STRANGLE", "LONG_CALL", "LONG_PUT", "NO_TRADE"}
    symbol = symbol_features.get("symbol", "SPY")

    # 1) Ling 3.0 Flash Fin — finance-native (primary)
    out = call_ling_fin_flash(symbol_features, earnings)
    if out and isinstance(out, dict) and out.get("strategy") in valid:
        out["source"] = "ling-fin-flash"
        out["symbol"] = out.get("symbol") or symbol
        out.setdefault("edge_bps", 0)
        return out

    # 2) Featherless
    out = call_featherless(symbol_features, earnings)
    if out and isinstance(out, dict) and out.get("strategy") in valid:
        out["source"] = "featherless"
        out["symbol"] = out.get("symbol") or symbol
        out.setdefault("edge_bps", 0)
        return out

    # 3) OpenAI
    out = call_openai_fallback(symbol_features, earnings)
    if out and isinstance(out, dict) and out.get("strategy") in valid:
        out["source"] = "openai"
        out["symbol"] = out.get("symbol") or symbol
        out.setdefault("edge_bps", 0)
        return out

    # 4) Deterministic Quantitative Rules Engine
    out = rules_classifier(symbol_features, earnings)
    out["source"] = "rules"
    log_event("llm_rules", symbol=symbol, output=out)
    return out

