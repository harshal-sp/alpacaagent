"""News sentiment — Fireworks + OpenRouter finance-native when available, else heuristic."""
from typing import Dict, Any, List
from src.utils.logger import logger, log_event
import requests, json, os

def _classify_with(provider: str, headline: str, symbol: str) -> Dict[str, Any] | None:
    try:
        if provider == "fireworks":
            from src.config import FIREWORKS_BASE_URL, FIREWORKS_MODEL
            key = os.getenv("FIREWORKS_API_KEY", "")
            if not key:
                return None
            base_url = (FIREWORKS_BASE_URL or "https://api.fireworks.ai/inference/v1").rstrip("/")
            model = FIREWORKS_MODEL
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        else:
            from src.config import OPENROUTER_BASE_URL, OPENROUTER_MODEL, OPENROUTER_REFERRER, OPENROUTER_TITLE
            key = os.getenv("OPENROUTER_API_KEY", "")
            if not key:
                return None
            base_url = (OPENROUTER_BASE_URL or "https://openrouter.ai/api/v1").rstrip("/")
            model = OPENROUTER_MODEL
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "HTTP-Referer": OPENROUTER_REFERRER, "X-Title": OPENROUTER_TITLE}
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a finance sentiment classifier. Given a headline and symbol, output JSON {\"sentiment\": \"bullish|bearish|neutral\", \"confidence\": 0-1, \"reason\": \"short\"}."},
                {"role": "user", "content": json.dumps({"symbol": symbol, "headline": headline})},
            ],
            "temperature": 0.15,
            "max_tokens": 150,
            "response_format": {"type": "json_object"},
        }
        resp = requests.post(f"{base_url}/chat/completions", headers=headers, json=payload, timeout=10)
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"]
            txt = content.strip()
            if txt.startswith("```"):
                txt = txt.split("```")[1]
                if txt.startswith("json"):
                    txt = txt[4:]
            parsed = json.loads(txt.strip())
            parsed["headline"] = headline[:120]
            parsed["source"] = provider
            return parsed
    except Exception as e:
        logger.debug(f"{provider} sentiment failed {symbol}: {e}")
    return None

def get_news_sentiment(symbol: str) -> Dict[str, Any]:
    """Lightweight sentiment using Fireworks/OpenRouter if key available, else heuristic.
    Uses yfinance news + dual-provider reasoning. No paid news API required.
    """
    headline = ""
    try:
        import yfinance as yf
        tk = yf.Ticker(symbol)
        news = tk.news
        if news and len(news) > 0:
            headline = news[0].get("title", "") or news[0].get("content", {}).get("title", "") if isinstance(news[0], dict) else ""
    except Exception as e:
        logger.debug(f"yfinance news {symbol}: {e}")

    if headline:
        # Try Fireworks first (fast), then OpenRouter fallback
        for provider in ("fireworks", "openrouter"):
            res = _classify_with(provider, headline, symbol)
            if res:
                log_event(f"news_sentiment_{provider}", symbol=symbol, headline=headline[:120], output=res)
                return res

    return {"sentiment": "neutral", "confidence": 0.5, "reason": "heuristic fallback", "headline": headline[:120], "source": "heuristic"}
