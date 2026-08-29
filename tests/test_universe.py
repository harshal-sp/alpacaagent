"""Unit tests for expanded universe, presets, ETF filtering, and option legs."""
import pytest
from src.config import (
    UNIVERSE,
    UNIVERSE_PRESETS,
    INDEX_ETFS,
    MAG_SEVEN,
    AI_AND_SEMIS,
    HIGH_BETA_MOMENTUM,
    EARNINGS_WATCH,
    parse_universe,
)
from src.data.earnings import get_upcoming_earnings, NON_EARNINGS_TICKERS
from src.data.alpaca_client import AlpacaClient
from src.strategy.selector import build_legs

def test_universe_core_defaults():
    """Verify default universe contains high-performing stocks and liquid ETFs."""
    assert "NVDA" in UNIVERSE
    assert "PLTR" in UNIVERSE
    assert "AVGO" in UNIVERSE
    assert "COIN" in UNIVERSE
    assert "AMZN" in UNIVERSE
    assert "GOOGL" in UNIVERSE
    assert "NFLX" in UNIVERSE
    assert "IWM" in UNIVERSE
    assert "SMH" in UNIVERSE
    assert len(UNIVERSE) >= 16

def test_universe_presets():
    """Verify preset parsing."""
    assert parse_universe("MAG_SEVEN") == MAG_SEVEN
    assert parse_universe("INDEX_ETFS") == INDEX_ETFS
    assert parse_universe("AI_AND_SEMIS") == AI_AND_SEMIS
    assert parse_universe("HIGH_BETA_MOMENTUM") == HIGH_BETA_MOMENTUM
    assert "QQQ" in parse_universe("TECH")
    assert "PLTR" in parse_universe("TECH")
    
    # Custom comma-separated list
    custom = parse_universe("pltr, nvda, coin, avgo")
    assert custom == ["PLTR", "NVDA", "COIN", "AVGO"]

def test_earnings_etf_skip():
    """Verify ETFs are excluded from corporate earnings calendar."""
    test_tickers = ["SPY", "QQQ", "IWM", "NVDA", "CRM", "AVGO"]
    results = get_upcoming_earnings(test_tickers, days_ahead=7)
    res_tickers = {r["ticker"] for r in results}
    
    # Ensure no ETFs in earnings results
    for etf in NON_EARNINGS_TICKERS:
        assert etf not in res_tickers
    
    # Ensure watch stocks are present
    assert any(t in res_tickers for t in ["NVDA", "CRM", "AVGO"])

def test_synthetic_bars_for_new_tickers():
    """Verify synthetic bars generate realistic spot values for new high-performing stocks."""
    client = AlpacaClient(paper=True)
    for sym in ["PLTR", "AVGO", "COIN", "AMZN", "GOOGL", "IWM", "SMH"]:
        df = client.get_bars(sym, days=5)
        assert not df.empty
        assert "close" in df.columns
        last_price = float(df.iloc[-1]["close"])
        assert last_price > 0

def test_build_legs_on_high_performing_stock():
    """Verify strategy selector builds valid defined-risk legs on PLTR, AVGO, COIN."""
    client = AlpacaClient(paper=True)
    
    test_cases = [
        {"symbol": "PLTR", "strategy": "BULL_PUT_SPREAD"},
        {"symbol": "AVGO", "strategy": "BEAR_CALL_SPREAD"},
        {"symbol": "COIN", "strategy": "IRON_CONDOR"},
    ]
    
    for tc in test_cases:
        bars = client.get_bars(tc["symbol"], days=5)
        spot = float(bars.iloc[-1]["close"]) if not bars.empty else 150.0
        chain = client.get_option_chain(tc["symbol"])
        assert len(chain) > 0
        
        decision = {
            "symbol": tc["symbol"],
            "strategy": tc["strategy"],
            "confidence": 0.85,
            "rationale": f"High alpha momentum on {tc['symbol']}",
        }
        proposal = build_legs(decision, spot=spot, chain=chain, buying_power=100000.0)
        assert proposal.get("legs") is not None
        assert len(proposal["legs"]) >= 2
        assert proposal["strategy"] == tc["strategy"]
