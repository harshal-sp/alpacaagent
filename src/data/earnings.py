"""Earnings calendar helper — for straddle targeting."""
from datetime import datetime, timedelta
from typing import List, Dict
import requests
from src.utils.logger import logger

def get_upcoming_earnings(tickers: List[str], days_ahead: int = 7) -> List[Dict]:
    """Try Finnhub/yfinance, fallback to static watchlist for hackathon week."""
    upcoming = []
    # Try yfinance earnings dates
    try:
        import yfinance as yf
        for t in tickers:
            try:
                tk = yf.Ticker(t)
                cal = tk.calendar
                if cal is not None and not cal.empty:
                    # calendar is dict or df depending on version
                    logger.info(f"yfinance calendar for {t}: {cal}")
                    # parse earnings date if available
                    # Fallback: try get_earnings_dates
                    try:
                        ed = tk.get_earnings_dates(limit=4)
                        if ed is not None and not ed.empty:
                            for idx, row in ed.iterrows():
                                d = idx.date() if hasattr(idx, 'date') else idx
                                delta = (d - datetime.now().date()).days
                                if 0 <= delta <= days_ahead:
                                    upcoming.append({"ticker": t, "date": str(d), "days_ahead": delta})
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"earnings yfinance {t}: {e}")
    except Exception:
        pass
    # Static hackathon week 2026-08-28 to 2026-09-04 expectations
    # Known US earnings in this window often include CRM, AVGO, DELL etc — add as watch
    static = [
        {"ticker": "NVDA", "date": "2026-09-02", "days_ahead": 2, "note": "expected earnings window"},
        {"ticker": "CRM", "date": "2026-09-03", "days_ahead": 3, "note": "expected"},
        {"ticker": "AVGO", "date": "2026-09-04", "days_ahead": 4, "note": "expected"},
    ]
    # merge if not already present
    existing = {u["ticker"] for u in upcoming}
    for s in static:
        if s["ticker"] not in existing and s["days_ahead"] <= days_ahead:
            upcoming.append(s)
    return upcoming
