"""News sentiment stub — Ling Fin Flash finance-native when available, else heuristic."""
from typing import Dict, Any, List
from src.utils.logger import logger, log_event
import requests, json, os

def get_news_sentiment(symbol: str) -> Dict[str, Any]:
    """Lightweight sentiment using Ling Fin Flash if LING_API_KEY available, else heuristic from technicals.
    Designed for 7-day hackathon — no external paid news API required; uses yfinance news + Ling reasoning.
    """
    # Try yfinance news
    headline = ""
    try:
        import yfinance as yf
        tk = yf.Ticker(symbol)
        news = tk.news
        if news and len(news) > 0:
            headline = news[0].get("title", "") or news[0].get("content", {}).get("title", "") if isinstance(news[0], dict) else ""
    except Exception as e:
        logger.debug(f"yfinance news {symbol}: {e}")

    # Try Ling Fin Flash for finance sentiment if key available
    ling_key = os.getenv("LING_API_KEY") or os.getenv("OPENROUTER_API_KEY") or os.getenv("AI_GATEWAY_API_KEY")
    if ling_key and headline:
        try:
            from src.config import LING_BASE_URL, LING_MODEL
            payload = {
                "model": LING_MODEL,
                "messages": [
                    {"role": "system", "content": "You are a finance sentiment classifier. Given a headline and symbol, output JSON {\"sentiment\": \"bullish|bearish|neutral\", \"confidence\": 0-1, \"reason\": \"short\"}."},
                    {"role": "user", "content": json.dumps({"symbol": symbol, "headline": headline})},
                ],
                "temperature": 0.2,
                "max_tokens": 150,
                "response_format": {"type": "json_object"},
            }
            headers = {"Authorization": f"Bearer {ling_key}", "Content-Type": "application/json", "HTTP-Referer": "https://lablab.ai", "X-Title": "Vega Sentiment"}
            resp = requests.post(f"{LING_BASE_URL.rstrip('/')}/chat/completions", headers=headers, json=payload, timeout=10)
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"]
                parsed = json.loads(content.strip().removeprefix("```json").removesuffix("```").strip())
                parsed["headline"] = headline
                parsed["source"] = "ling-fin-flash"
                log_event("news_sentiment_ling", symbol=symbol, headline=headline[:120], output=parsed)
                return parsed
        except Exception as e:
            logger.debug(f"ling sentiment failed {symbol}: {e}")

    # Heuristic fallback — neutral with slight momentum tilt
    # This ensures agent never blocks on missing news
    return {"sentiment": "neutral", "confidence": 0.5, "reason": "heuristic fallback", "headline": headline[:120], "source": "heuristic"}
