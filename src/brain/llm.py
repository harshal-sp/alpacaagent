"""LLM Brain — Ling 3.0 Flash Fin (primary finance-native) → Featherless → OpenAI → Rules."""
import json
import os
from typing import Dict, Any, List
import requests

from src.config import (
    LING_API_KEY, LING_BASE_URL, LING_MODEL, OPENCODE_LING_MODEL,
    FEATHERLESS_API_KEY, FEATHERLESS_BASE_URL, FEATHERLESS_MODEL,
    OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL,
    AI_GATEWAY_API_KEY, AI_GATEWAY_BASE_URL
)
from src.utils.logger import logger, log_event

# Finance-native prompt optimized for Ling 3.0 Flash Fin (124B MoE, 5.1B active, 256K context, finance-tuned on FinFIRST/FinSearchComp/FinanceAgent)
LING_FIN_SYSTEM_PROMPT = """You are Vega-Fin, an autonomous options alpha quant built on Ling 3.0 Flash Fin.
You manage a $100k Alpaca paper account trading 0-7 DTE options on SPY/QQQ and 6 earnings names. You have 256K context and finance tool-calling.

Mission: For a 7-day hackathon P&L sprint, pick ONE optimal defined-risk options structure that maximizes risk-adjusted P&L over next 1-3 trading days.

Context you receive: {symbol, last, change_pct, ema20/50, trend, atr, atr_pct, realized_vol_20d_annual, rsi, avg_iv, iv_rank, regime_hint, bars} + earnings_watch.

Finance reasoning steps (think step-by-step, 3-5 lines internally):
1. Regime: use IV Rank + trend + ATR% + RSI + earnings proximity. High IVR>22 + sideways + ATR<2% → range_high_iv. Low IVR<22 + ATR<2% → low_vol_compression. Trend+ATR>1% → trending_high_vol.
2. Edge: Compare premium collected vs expected move (1*ATR) vs fees. Short premium needs high IVR; long gamma needs cheap premium and catalyst.
3. Structure selection (only defined-risk):
   - range_high_iv → IRON_CONDOR (4 legs, 15Δ short / 5Δ wings) or BULL_PUT/BEAR_CALL if directional tilt
   - low_vol_compression + earnings≤3d → LONG_STRADDLE (ATM call+put)
   - low_vol_compression without earnings → LONG_STRANGLE (OTM, cheaper)
   - trending_high_vol + uptrend → BULL_PUT_SPREAD (20Δ short put / 7Δ long put)
   - trending_high_vol + downtrend → BEAR_CALL_SPREAD (20Δ short call / 7Δ long call)
   - fallback momentum → LONG_CALL / LONG_PUT (35Δ)
4. Risk: Max 20% BP/trade, max loss = width-credit (spreads) or premium (longs). Never naked. Reject if net credit after $0.17/contract fees < $0.10/share.

Output strictly JSON (no markdown):
{
  "regime": "range_high_iv|low_vol_compression|trending_high_vol|mixed|volatile",
  "confidence": 0.0-1.0,
  "strategy": "IRON_CONDOR|BULL_PUT_SPREAD|BEAR_CALL_SPREAD|LONG_STRADDLE|LONG_STRANGLE|LONG_CALL|LONG_PUT|NO_TRADE",
  "rationale": "one sentence finance-aware",
  "symbol": "SPY/QQQ/etc",
  "bias": "neutral|bullish|bearish",
  "risk_note": "one sentence with max loss",
  "edge_bps": 0,
  "expected_move_pct": 0.0
}
"""

# Fallback prompt for generic LLMs
SYSTEM_PROMPT = """You are Vega, an aggressive autonomous options alpha agent for a 7-day Alpaca paper trading hackathon.
You manage a $100k paper account trading 0-7 DTE options on SPY/QQQ and earnings names.
Your job: classify regime and pick ONE optimal options structure.

Rules:
- Be aggressive but risk-aware. Max 20% buying power per trade.
- Prefer short premium (iron condor / put spreads) when IV rank >22 and market is range-bound.
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

def call_ling_fin_flash(features: Dict[str, Any], earnings: List[Dict] | None = None) -> Dict[str, Any] | None:
    """Primary: Ling 3.0 Flash Fin via OpenRouter free, Vercel AI Gateway, or Opencode proxy.
    Tries endpoints in order; free tier through Sep 25 2026.
    """
    # Build payload once
    user_payload = {
        "features": features,
        "earnings_watch": earnings or [],
        "task": "Pick best defined-risk options structure for aggressive 1-3 day P&L. Return strict JSON.",
        "fee_hint": "Subtract $0.17/contract round-trip before edge. Reject if net credit <0.10/share.",
    }
    # Endpoints to try
    endpoints = []
    # Opencode provider (when running via `opencode run -m opencode/ling-3.0-flash-fin-free` this is local proxy)
    # Not an HTTP endpoint for standalone — skip unless OPENCODE_API_KEY set
    # OpenRouter free
    if True:  # always try OpenRouter free (works without key via opencode's key, else try env)
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
            "model": "inclusionai/ling-3.0-flash-fin",  # Vercel uses without :free suffix
            "headers_extra": {},
        })
    # Direct Ant Ling API (if LING_API_KEY is Ant key)
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
            # For OpenRouter free without key, still try without auth (may use opencode's key)
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
            # Ling supports reasoning; enable thinking for complex finance
            # Some endpoints ignore extra fields; we add safely
            if "ling" in ep["model"]:
                body["reasoning"] = {"effort": "medium"}
            resp = requests.post(ep["url"], headers={**headers, **ep["headers_extra"]}, json=body, timeout=25)
            if resp.status_code != 200:
                logger.debug(f"Ling {ep['url']} {ep['model']} {resp.status_code}: {resp.text[:400]}")
                continue
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            # Strip markdown fences if present
            if "```" in content:
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            parsed = json.loads(content.strip())
            log_event("llm_ling_fin", model=ep["model"], url=ep["url"], output=parsed)
            return parsed
        except Exception as e:
            logger.debug(f"Ling endpoint {ep['url']} failed: {e}")
            continue
    # If all endpoints fail, log and return None to fallback
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
    has_earnings = False
    earn_days = 99
    if earnings:
        for e in earnings:
            if e["ticker"] == symbol and e.get("days_ahead", 99) <= 3:
                has_earnings = True
                earn_days = e.get("days_ahead", 99)
                break
    if has_earnings and ivr < 55:
        return {"regime": "low_vol_compression", "confidence": 0.80, "strategy": "LONG_STRADDLE", "rationale": f"{symbol} earnings in {earn_days}d with IVR {ivr} — cheap gamma before event", "symbol": symbol, "bias": "neutral", "risk_note": "Defined-risk long gamma, max loss premium"}
    if ivr > 22 and trend == "sideways" and atr_pct < 2.0:
        return {"regime": "range_high_iv", "confidence": 0.82, "strategy": "IRON_CONDOR", "rationale": f"IVR {ivr} elevated + sideways + ATR {atr_pct}% favors theta", "symbol": symbol, "bias": "neutral", "risk_note": "Iron condor max loss = width - credit, defined"}
    if trend == "uptrend" and (rsi < 72 or atr_pct > 1.0):
        return {"regime": "trending_high_vol", "confidence": 0.76, "strategy": "BULL_PUT_SPREAD", "rationale": f"Uptrend RSI {rsi} ATR {atr_pct}% favors put spread income with trend", "symbol": symbol, "bias": "bullish", "risk_note": "Spread max loss limited"}
    if trend == "downtrend" and (rsi > 28 or atr_pct > 1.0):
        return {"regime": "trending_high_vol", "confidence": 0.76, "strategy": "BEAR_CALL_SPREAD", "rationale": f"Downtrend RSI {rsi} ATR {atr_pct}% favors call spread income", "symbol": symbol, "bias": "bearish", "risk_note": "Defined-risk spread"}
    if ivr < 22 and atr_pct < 2.0:
        return {"regime": "low_vol_compression", "confidence": 0.71, "strategy": "LONG_STRANGLE", "rationale": f"IVR {ivr} low + ATR {atr_pct}% compressed — long strangle cheap gamma", "symbol": symbol, "bias": "neutral", "risk_note": "Long gamma max loss premium"}
    if trend == "uptrend":
        return {"regime": "mixed", "confidence": 0.62, "strategy": "LONG_CALL", "rationale": f"Momentum uptrend favors long call, IVR {ivr} ATR {atr_pct}%", "symbol": symbol, "bias": "bullish", "risk_note": "Max loss premium"}
    if trend == "downtrend":
        return {"regime": "mixed", "confidence": 0.62, "strategy": "LONG_PUT", "rationale": f"Momentum downtrend favors long put, IVR {ivr} ATR {atr_pct}%", "symbol": symbol, "bias": "bearish", "risk_note": "Max loss premium"}
    if ivr > 15:
        return {"regime": "mixed", "confidence": 0.60, "strategy": "IRON_CONDOR", "rationale": f"Sideways IVR {ivr} condor income for week window", "symbol": symbol, "bias": "neutral", "risk_note": "Defined-risk condor"}
    return {"regime": "mixed", "confidence": 0.58, "strategy": "LONG_STRANGLE", "rationale": f"Low IVR {ivr} fallback long strangle for gamma", "symbol": symbol, "bias": "neutral", "risk_note": "Premium-defined gamma"}

def classify(symbol_features: Dict[str, Any], earnings: List[Dict] | None = None) -> Dict[str, Any]:
    """Try Ling Fin Flash → Featherless → OpenAI → Rules. Always returns valid dict."""
    # 1) Ling 3.0 Flash Fin — finance-native (primary)
    out = call_ling_fin_flash(symbol_features, earnings)
    if out and "strategy" in out:
        out["source"] = "ling-fin-flash"
        valid = {"IRON_CONDOR","BULL_PUT_SPREAD","BEAR_CALL_SPREAD","LONG_STRADDLE","LONG_STRANGLE","LONG_CALL","LONG_PUT","NO_TRADE"}
        if out.get("strategy") not in valid:
            logger.warning(f"Ling returned invalid {out.get('strategy')} — fallback")
            out = None
        else:
            if not out.get("symbol"):
                out["symbol"] = symbol_features.get("symbol", "SPY")
            # Ensure finance fields present
            if "edge_bps" not in out:
                out["edge_bps"] = 0
            return out
    # 2) Featherless
    out = call_featherless(symbol_features, earnings)
    if out and "strategy" in out:
        out["source"] = "featherless"
        valid = {"IRON_CONDOR","BULL_PUT_SPREAD","BEAR_CALL_SPREAD","LONG_STRADDLE","LONG_STRANGLE","LONG_CALL","LONG_PUT","NO_TRADE"}
        if out.get("strategy") not in valid:
            logger.warning(f"LLM returned invalid strategy {out.get('strategy')} — falling back to rules")
            out = None
        else:
            if not out.get("symbol"):
                out["symbol"] = symbol_features.get("symbol", "SPY")
            return out
    # 3) OpenAI
    out = call_openai_fallback(symbol_features, earnings)
    if out and "strategy" in out:
        out["source"] = "openai"
        valid = {"IRON_CONDOR","BULL_PUT_SPREAD","BEAR_CALL_SPREAD","LONG_STRADDLE","LONG_STRANGLE","LONG_CALL","LONG_PUT","NO_TRADE"}
        if out.get("strategy") in valid:
            if not out.get("symbol"):
                out["symbol"] = symbol_features.get("symbol", "SPY")
            return out
    # 4) Rules
    out = rules_classifier(symbol_features, earnings)
    out["source"] = "rules"
    log_event("llm_rules", symbol=symbol_features.get("symbol"), output=out)
    return out
