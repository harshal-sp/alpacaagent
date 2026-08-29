"""LLM Brain — Fireworks AI (primary) + OpenRouter (secondary) concurrent → ensemble → Rules."""
import json
import os
import re
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List
import requests

from src.config import (
    FIREWORKS_API_KEY, FIREWORKS_BASE_URL, FIREWORKS_MODEL,
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL, OPENROUTER_REFERRER, OPENROUTER_TITLE,
    LLM_CONCURRENT, LLM_TIMEOUT_S, LLM_CACHE_TTL_S,
)
from src.utils.logger import logger, log_event

# simple in-memory LLM cache: key -> (parsed, ts)
_LLM_CACHE: Dict[str, tuple] = {}

def _extract_json(text: str) -> Dict[str, Any] | None:
    if not text:
        return None
    cleaned = text.strip()
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
    match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(1)
    try:
        return json.loads(cleaned.strip())
    except Exception as e:
        logger.debug(f"JSON parse err: {e} | {text[:300]}")
        return None

# Unified finance-native prompt — works for both Fireworks (Llama/DeepSeek) and OpenRouter (Ling finance)
SYSTEM_PROMPT = """You are Vega-Fin, an autonomous options alpha quant. You manage a $100k Alpaca paper account trading 0-7 DTE options on SPY/QQQ/IWM/SMH and high-beta names (NVDA, PLTR, AVGO, COIN, etc.).

Mission: For a 7-day hackathon P&L sprint, pick ONE optimal defined-risk options structure that maximizes risk-adjusted P&L over next 1-3 trading days. Be aggressive but risk-aware.

Context: {symbol, last, change_pct, ema20/50, trend, atr, atr_pct, realized_vol_20d_annual, rsi, avg_iv, iv_rank, vrp, put_call_iv_ratio, bb_squeeze, bb_width_pct, regime_hint, regime_scores, expected_move_pct, volume_ratio, vwap_dist_pct, support/resistance, news_sentiment} + earnings_watch.

Finance reasoning (internal, 3-5 lines):
1. VRP = IV - RV. If VRP > 4.0 and IVR > 25 → IV rich → sell premium (IRON_CONDOR, BULL_PUT_SPREAD, BEAR_CALL_SPREAD).
   If bb_squeeze or (IVR <20 and VRP <1.0) + catalyst → cheap gamma → LONG_STRADDLE/LONG_STRANGLE.
   Strong EMA trend + high ATR% + MACD hist → directional spread.
   News sentiment aligned with bias → +confidence; neutral → ignore.
2. Pick ONE defined-risk structure:
   range_high_iv/high VRP → IRON_CONDOR (15Δ short /5Δ wing)
   low_vol_compression + earnings≤3d → LONG_STRADDLE (ATM)
   low_vol_compression/squeeze → LONG_STRANGLE (OTM)
   trending_high_vol + up → BULL_PUT_SPREAD; down → BEAR_CALL_SPREAD
   breakout momentum → LONG_CALL/LONG_PUT; no edge → NO_TRADE
3. Risk: Max 20% BP/trade, max loss = width-credit or debit. Never naked. Reject if net credit after $0.17/contract < $0.10/share.

Output strictly JSON (no markdown):
{
  "regime": "range_high_iv|low_vol_compression|trending_high_vol|mixed|volatile",
  "confidence": 0.0-1.0,
  "strategy": "IRON_CONDOR|BULL_PUT_SPREAD|BEAR_CALL_SPREAD|LONG_STRADDLE|LONG_STRANGLE|LONG_CALL|LONG_PUT|NO_TRADE",
  "rationale": "one finance-aware sentence",
  "symbol": "SPY/etc",
  "bias": "neutral|bullish|bearish",
  "risk_note": "one sentence with max loss/wing",
  "edge_bps": 0,
  "expected_move_pct": 0.0
}
"""

FIREWORKS_SYSTEM_PROMPT = SYSTEM_PROMPT
OPENROUTER_SYSTEM_PROMPT = SYSTEM_PROMPT

VEGA_JSON_SCHEMA = {
    "name": "vega_decision",
    "schema": {
        "type": "object",
        "properties": {
            "regime": {"type": "string", "enum": ["range_high_iv", "low_vol_compression", "trending_high_vol", "mixed", "volatile"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "strategy": {"type": "string", "enum": ["IRON_CONDOR", "BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "LONG_STRADDLE", "LONG_STRANGLE", "LONG_CALL", "LONG_PUT", "NO_TRADE"]},
            "rationale": {"type": "string"},
            "symbol": {"type": "string"},
            "bias": {"type": "string", "enum": ["neutral", "bullish", "bearish"]},
            "risk_note": {"type": "string"},
            "edge_bps": {"type": "integer"},
            "expected_move_pct": {"type": "number"}
        },
        "required": ["regime", "confidence", "strategy", "rationale", "symbol", "bias", "risk_note"],
        "additionalProperties": False
    }
}

def _cache_key(features: Dict[str, Any], earnings: List[Dict] | None, provider: str, model: str) -> str:
    raw = json.dumps({"f": {k: features.get(k) for k in sorted(features.keys())}, "e": earnings or [], "p": provider, "m": model}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def call_fireworks(features: Dict[str, Any], earnings: List[Dict] | None = None) -> Dict[str, Any] | None:
    api_key = FIREWORKS_API_KEY or os.getenv("FIREWORKS_API_KEY", "")
    if not api_key:
        return None
    base_url = (FIREWORKS_BASE_URL or "https://api.fireworks.ai/inference/v1").rstrip("/")
    model = FIREWORKS_MODEL or "accounts/fireworks/models/llama-v3p3-70b-instruct"
    ck = _cache_key(features, earnings, "fw", model)
    cached = _LLM_CACHE.get(ck)
    if cached and (time.time() - cached[1] < LLM_CACHE_TTL_S):
        log_event("llm_fireworks_cache_hit", model=model)
        return dict(cached[0])

    payload = {
        "features": features,
        "earnings_watch": earnings or [],
        "task": "Pick best defined-risk options structure. Return strict JSON.",
        "fee_hint": "Subtract $0.17/contract round-trip. Reject if net credit <0.10/share.",
    }
    url = f"{base_url}/chat/completions"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": FIREWORKS_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload)},
        ],
        "temperature": 0.15,
        "max_tokens": 650,
        "response_format": {"type": "json_object"},
    }
    try:
        t0 = time.time()
        resp = requests.post(url, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=body, timeout=LLM_TIMEOUT_S)
        latency = round((time.time() - t0) * 1000)
        if resp.status_code != 200:
            logger.debug(f"Fireworks {model} {resp.status_code}: {resp.text[:500]} latency {latency}ms")
            if resp.status_code in (400, 422) and "response_format" in body:
                body.pop("response_format")
                resp = requests.post(url, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=body, timeout=LLM_TIMEOUT_S)
                if resp.status_code != 200:
                    return None
            else:
                return None
        data = resp.json()
        content = data["choices"][0]["message"]["content"] if data.get("choices") else ""
        if not content:
            content = data["choices"][0]["message"].get("reasoning_content", "") or ""
        parsed = _extract_json(content)
        if parsed and "strategy" in parsed:
            parsed["_latency_ms"] = latency
            _LLM_CACHE[ck] = (dict(parsed), time.time())
            log_event("llm_fireworks", model=model, latency_ms=latency, output=parsed)
            return parsed
        logger.warning(f"Fireworks invalid JSON: {content[:400]}")
        return None
    except Exception as e:
        logger.debug(f"Fireworks call failed: {e}")
        return None

def call_openrouter(features: Dict[str, Any], earnings: List[Dict] | None = None) -> Dict[str, Any] | None:
    api_key = OPENROUTER_API_KEY or os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return None
    base_url = (OPENROUTER_BASE_URL or "https://openrouter.ai/api/v1").rstrip("/")
    model = OPENROUTER_MODEL or "inclusionai/ling-3.0-flash-fin:free"
    ck = _cache_key(features, earnings, "or", model)
    cached = _LLM_CACHE.get(ck)
    if cached and (time.time() - cached[1] < LLM_CACHE_TTL_S):
        log_event("llm_openrouter_cache_hit", model=model)
        return dict(cached[0])

    payload = {
        "features": features,
        "earnings_watch": earnings or [],
        "task": "Pick best defined-risk options structure. Return strict JSON.",
        "fee_hint": "Subtract $0.17/contract round-trip. Reject if net credit <0.10/share.",
    }
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": OPENROUTER_REFERRER,
        "X-Title": OPENROUTER_TITLE,
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": OPENROUTER_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload)},
        ],
        "temperature": 0.15,
        "max_tokens": 650,
        "response_format": {"type": "json_object"},
    }
    # Ling finance model supports reasoning effort
    if "ling" in model.lower():
        body["reasoning"] = {"effort": "medium"}
    try:
        t0 = time.time()
        resp = requests.post(url, headers=headers, json=body, timeout=LLM_TIMEOUT_S)
        latency = round((time.time() - t0) * 1000)
        if resp.status_code != 200:
            logger.debug(f"OpenRouter {model} {resp.status_code}: {resp.text[:500]} latency {latency}ms")
            if resp.status_code in (400, 422) and "response_format" in body:
                body.pop("response_format")
                resp = requests.post(url, headers=headers, json=body, timeout=LLM_TIMEOUT_S)
                if resp.status_code != 200:
                    return None
            else:
                return None
        data = resp.json()
        content = data["choices"][0]["message"]["content"] if data.get("choices") else ""
        if not content and data.get("choices"):
            content = data["choices"][0]["message"].get("reasoning_content", "") or ""
        parsed = _extract_json(content)
        if parsed and "strategy" in parsed:
            parsed["_latency_ms"] = latency
            _LLM_CACHE[ck] = (dict(parsed), time.time())
            log_event("llm_openrouter", model=model, latency_ms=latency, output=parsed)
            return parsed
        logger.warning(f"OpenRouter invalid JSON: {content[:400]}")
        return None
    except Exception as e:
        logger.debug(f"OpenRouter call failed: {e}")
        return None

def _ensemble_pick(candidates: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    """Pick best candidate by score = confidence + edge_bps/10000, tie by latency."""
    valid = {"IRON_CONDOR", "BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "LONG_STRADDLE", "LONG_STRANGLE", "LONG_CALL", "LONG_PUT", "NO_TRADE"}
    scored = []
    for c in candidates:
        if not c or c.get("strategy") not in valid:
            continue
        try:
            conf = float(c.get("confidence", 0.5))
        except Exception:
            conf = 0.5
        edge = int(c.get("edge_bps", 0) or 0)
        score = conf + edge / 10000.0
        # small boost for high edge with reasonable confidence
        scored.append((score, -c.get("_latency_ms", 9999), c))
    if not scored:
        return None
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return scored[0][2]

def rules_classifier(features: Dict[str, Any], earnings: List[Dict] | None = None) -> Dict[str, Any]:
    ivr = features.get("iv_rank", 25)
    vrp = features.get("vrp", 0.0)
    trend = features.get("trend", "sideways")
    rsi_val = features.get("rsi", 50)
    atr_pct = features.get("atr_pct", 1.0)
    is_squeeze = features.get("bb_squeeze", False)
    symbol = features.get("symbol", "SPY")
    has_earnings = False
    earn_days = 99
    if earnings:
        for e in earnings:
            if e.get("ticker") == symbol and e.get("days_ahead", 99) <= 3:
                has_earnings = True
                earn_days = e.get("days_ahead", 99)
                break
    if has_earnings and ivr < 60:
        return {"regime": "low_vol_compression", "confidence": 0.85, "strategy": "LONG_STRADDLE", "rationale": f"{symbol} earnings in {earn_days}d (IVR {ivr}) — cheap gamma capture before catalyst move", "symbol": symbol, "bias": "neutral", "risk_note": "Defined-risk ATM straddle, max loss = total debit paid", "edge_bps": 120, "expected_move_pct": round(atr_pct * 1.2, 2)}
    if is_squeeze and ivr < 30 and not has_earnings:
        return {"regime": "low_vol_compression", "confidence": 0.80, "strategy": "LONG_STRANGLE", "rationale": f"{symbol} BB squeeze compression (IVR {ivr}, ATR {atr_pct}%) — positioning for explosive breakout", "symbol": symbol, "bias": "neutral", "risk_note": "Defined-risk OTM strangle, max loss = debit", "edge_bps": 85, "expected_move_pct": round(atr_pct * 1.5, 2)}
    if (ivr > 25 or vrp > 3.5) and trend == "sideways" and atr_pct < 2.2:
        return {"regime": "range_high_iv", "confidence": 0.84, "strategy": "IRON_CONDOR", "rationale": f"{symbol} elevated IVR {ivr} and positive VRP {vrp:.1f}% in range-bound structure favors theta decay", "symbol": symbol, "bias": "neutral", "risk_note": "Defined-risk 4-leg condor, max loss = wing width - credit collected", "edge_bps": 95, "expected_move_pct": round(atr_pct * 0.8, 2)}
    if trend == "uptrend" and (rsi_val < 72 or atr_pct > 1.0):
        return {"regime": "trending_high_vol", "confidence": 0.78, "strategy": "BULL_PUT_SPREAD", "rationale": f"{symbol} uptrend momentum (RSI {rsi_val}, ATR {atr_pct}%) favors selling high-IV OTM put spread", "symbol": symbol, "bias": "bullish", "risk_note": "Defined-risk vertical put credit spread, max loss = strike width - credit", "edge_bps": 75, "expected_move_pct": round(atr_pct, 2)}
    if trend == "downtrend" and (rsi_val > 28 or atr_pct > 1.0):
        return {"regime": "trending_high_vol", "confidence": 0.78, "strategy": "BEAR_CALL_SPREAD", "rationale": f"{symbol} downtrend momentum (RSI {rsi_val}, ATR {atr_pct}%) favors selling OTM call credit spread", "symbol": symbol, "bias": "bearish", "risk_note": "Defined-risk vertical call credit spread, max loss = strike width - credit", "edge_bps": 75, "expected_move_pct": round(atr_pct, 2)}
    if ivr < 22 and atr_pct < 2.0:
        return {"regime": "low_vol_compression", "confidence": 0.72, "strategy": "LONG_STRANGLE", "rationale": f"{symbol} low IVR {ivr} + compressed ATR {atr_pct}% allows cheap gamma accumulation", "symbol": symbol, "bias": "neutral", "risk_note": "Defined-risk long gamma, max loss = total debit", "edge_bps": 60, "expected_move_pct": round(atr_pct * 1.3, 2)}
    if trend == "uptrend":
        return {"regime": "mixed", "confidence": 0.65, "strategy": "LONG_CALL", "rationale": f"{symbol} momentum continuation long call (IVR {ivr}, ATR {atr_pct}%)", "symbol": symbol, "bias": "bullish", "risk_note": "Max loss = premium paid", "edge_bps": 50, "expected_move_pct": round(atr_pct * 1.1, 2)}
    if trend == "downtrend":
        return {"regime": "mixed", "confidence": 0.65, "strategy": "LONG_PUT", "rationale": f"{symbol} momentum continuation long put (IVR {ivr}, ATR {atr_pct}%)", "symbol": symbol, "bias": "bearish", "risk_note": "Max loss = premium paid", "edge_bps": 50, "expected_move_pct": round(atr_pct * 1.1, 2)}
    if ivr > 15:
        return {"regime": "mixed", "confidence": 0.62, "strategy": "IRON_CONDOR", "rationale": f"{symbol} range-bound condor income for multi-day horizon (IVR {ivr})", "symbol": symbol, "bias": "neutral", "risk_note": "Defined-risk 4-leg condor", "edge_bps": 40, "expected_move_pct": round(atr_pct, 2)}
    return {"regime": "mixed", "confidence": 0.58, "strategy": "LONG_STRANGLE", "rationale": f"{symbol} low IVR {ivr} fallback strangle", "symbol": symbol, "bias": "neutral", "risk_note": "Defined-risk strangle", "edge_bps": 30, "expected_move_pct": round(atr_pct * 1.2, 2)}

def classify(symbol_features: Dict[str, Any], earnings: List[Dict] | None = None) -> Dict[str, Any]:
    """Fireworks + OpenRouter concurrent ensemble → best pick → rules. Always returns valid."""
    valid = {"IRON_CONDOR", "BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "LONG_STRADDLE", "LONG_STRANGLE", "LONG_CALL", "LONG_PUT", "NO_TRADE"}
    symbol = symbol_features.get("symbol", "SPY")

    has_fw = bool(FIREWORKS_API_KEY or os.getenv("FIREWORKS_API_KEY"))
    has_or = bool(OPENROUTER_API_KEY or os.getenv("OPENROUTER_API_KEY"))

    candidates: List[Dict[str, Any]] = []

    # Concurrent dual-provider if both keys present and concurrent enabled
    if has_fw and has_or and LLM_CONCURRENT:
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_map = {
                pool.submit(call_fireworks, symbol_features, earnings): "fw",
                pool.submit(call_openrouter, symbol_features, earnings): "or",
            }
            for fut in as_completed(fut_map):
                provider = fut_map[fut]
                try:
                    out = fut.result()
                    if out and out.get("strategy") in valid:
                        out["_provider"] = provider
                        candidates.append(out)
                except Exception as e:
                    logger.debug(f"LLM {provider} thread failed: {e}")
    else:
        # Sequential fallback — Fireworks first (speed), then OpenRouter (finance)
        if has_fw:
            out = call_fireworks(symbol_features, earnings)
            if out and out.get("strategy") in valid:
                out["_provider"] = "fw"
                candidates.append(out)
        if has_or:
            out = call_openrouter(symbol_features, earnings)
            if out and out.get("strategy") in valid:
                out["_provider"] = "or"
                candidates.append(out)

    best = _ensemble_pick(candidates)
    if best:
        provider = best.pop("_provider", "fw")
        model = FIREWORKS_MODEL if provider == "fw" else OPENROUTER_MODEL
        best["source"] = f"{'fireworks' if provider=='fw' else 'openrouter'}:{model.split('/')[-1] if '/' in model else model}"
        best["symbol"] = best.get("symbol") or symbol
        best.setdefault("edge_bps", 0)
        best.setdefault("expected_move_pct", 0)
        try:
            best["confidence"] = max(0.0, min(1.0, float(best.get("confidence", 0.5))))
        except Exception:
            best["confidence"] = 0.65
        # If both providers agreed, boost confidence slightly
        if len(candidates) >= 2 and candidates[0].get("strategy") == candidates[1].get("strategy"):
            best["confidence"] = min(0.95, best["confidence"] + 0.04)
            best["rationale"] = (best.get("rationale", "") + " [dual confirm]")[:220]
        best.pop("_latency_ms", None)
        return best

    # Final deterministic fallback
    out = rules_classifier(symbol_features, earnings)
    out["source"] = "rules"
    log_event("llm_rules", symbol=symbol, output=out)
    return out
