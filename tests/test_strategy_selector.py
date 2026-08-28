from src.strategy.selector import build_legs

def fake_chain(spot=580, expiry="2026-09-02"):
    chain = []
    for k in [spot*0.96, spot*0.98, spot, spot*1.02, spot*1.04]:
        for typ in ["call","put"]:
            mid = max(0.8, abs(spot-k)*0.08 + 1.0)
            chain.append({
                "symbol": f"SPY{expiry.replace('-','')}C{int(k*1000):08d}" if typ=="call" else f"SPY{expiry.replace('-','')}P{int(k*1000):08d}",
                "strike": k, "type": typ, "bid": mid-0.05, "ask": mid+0.05, "last": mid, "iv": 0.22, "volume": 1000, "openInterest": 5000,
                "expiration": expiry
            })
    return chain

def test_iron_condor():
    chain = fake_chain(580)
    decision = {"strategy": "IRON_CONDOR", "symbol": "SPY", "rationale": "test"}
    out = build_legs(decision, 580, chain, buying_power=400000)
    assert len(out["legs"]) == 4
    assert out["strategy"] == "IRON_CONDOR"

def test_long_straddle():
    chain = fake_chain(580)
    decision = {"strategy": "LONG_STRADDLE", "symbol": "SPY"}
    out = build_legs(decision, 580, chain, 200000)
    assert len(out["legs"]) == 2
    assert "est_debit" in out

def test_no_trade():
    out = build_legs({"strategy": "NO_TRADE", "symbol": "SPY"}, 580, fake_chain(580), 100000)
    assert out["legs"] == []
