import pandas as pd
import numpy as np
from src.features.indicators import compute_features, iv_rank, rsi
from src.features.greeks import bs_greeks

def test_iv_rank():
    assert 0 <= iv_rank(0.12) <= 100
    assert iv_rank(0.40) > iv_rank(0.15)

def test_rsi():
    s = pd.Series([100 + i*0.5 + np.sin(i) for i in range(30)])
    v = rsi(s, 14)
    assert 0 <= v <= 100

def test_compute_features():
    dates = pd.date_range(end=pd.Timestamp.now(), periods=60, freq="B")
    close = 580 + np.cumsum(np.random.randn(60))
    df = pd.DataFrame({
        "timestamp": dates,
        "open": close*0.998, "high": close*1.01, "low": close*0.99, "close": close, "volume": 80_000_000
    })
    chain = [{"iv": 0.22},{"iv":0.25}]
    f = compute_features("SPY", df, chain)
    assert "rsi" in f and "iv_rank" in f and "regime_hint" in f

def test_bs_greeks():
    g = bs_greeks(580, 580, 3/365, 0.045, 0.22, "call")
    assert 0 <= g["delta"] <= 1
    assert g["gamma"] >= 0
    assert "price" in g
