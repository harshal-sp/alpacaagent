from src.utils.fees import estimate_fees, is_profitable_after_fees

def test_estimate_fees_option_spread():
    legs = [
        {"symbol": "AAPL260902C00325000", "side": "sell", "qty": 5, "limit_price": 1.26},
        {"symbol": "AAPL260902C00330000", "side": "buy", "qty": 5, "limit_price": 0.43},
    ]
    fees = estimate_fees(legs)
    # 5 contracts *2 legs * (0.15+0.02) = 1.70 commission+reg + slippage
    assert fees["commission"] > 0
    assert fees["total_one_way"] > 1.0
    assert fees["total_round_trip"] == round(fees["total_one_way"]*2, 2)

def test_spread_profitable_after_fees():
    # Bear call spread: credit 0.83/share, 5 qty => gross 415, fees ~3.4 one-way => net 0.82 still >0.10 => profitable
    legs = [
        {"symbol": "AAPL260902C00325000", "side": "sell", "qty": 5, "limit_price": 1.26},
        {"symbol": "AAPL260902C00330000", "side": "buy", "qty": 5, "limit_price": 0.43},
    ]
    proposal = {"strategy": "BEAR_CALL_SPREAD", "legs": legs, "qty": 5, "est_credit": 0.83}
    ok, reason, fees = is_profitable_after_fees(proposal)
    assert ok, reason

def test_spread_not_profitable_tiny_credit():
    # Tiny credit $0.10/share with 5 qty gross $50, fees ~$2 one-way => net $0.096 <0.10 => rejected
    legs = [
        {"symbol": "SPY260828C00776000", "side": "sell", "qty": 5, "limit_price": 0.10},
        {"symbol": "SPY260828C00779000", "side": "buy", "qty": 5, "limit_price": 0.05},
    ]
    proposal = {"strategy": "BEAR_CALL_SPREAD", "legs": legs, "qty": 5, "est_credit": 0.05}
    ok, reason, fees = is_profitable_after_fees(proposal)
    assert not ok

def test_long_debit_fees():
    legs = [{"symbol": "SPY260828C00722000", "side": "buy", "qty": 2, "limit_price": 1.5}]
    proposal = {"strategy": "LONG_CALL", "legs": legs, "qty": 2, "est_debit": 1.5}
    ok, reason, fees = is_profitable_after_fees(proposal)
    # gross 300, fees ~0.7 one-way => <50% => ok
    assert ok
