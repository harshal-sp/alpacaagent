import pytest
from src.risk.gates import validate_trade, check_buying_power, check_daily_loss

def test_buying_power_fail():
    acct = {"buying_power": "1000", "options_buying_power": "1000"}
    r = check_buying_power(acct, 5000)
    assert not r.passed

def test_buying_power_pass():
    acct = {"buying_power": "200000", "options_buying_power": "150000"}
    r = check_buying_power(acct, 2000)
    assert r.passed

def test_daily_halt():
    acct = {"equity": "96000", "last_equity": "100000"}
    r = check_daily_loss(acct, initial_equity=100000)
    assert not r.passed  # -4% > -3% halt

def test_no_trade_blocked():
    acct = {"equity": "100000", "buying_power": "400000"}
    proposal = {"strategy": "NO_TRADE", "legs": []}
    passed, reasons = validate_trade(acct, [], [], proposal, [], initial_equity=100000)
    assert not passed

def test_validate_trade_pass():
    acct = {"equity": "100000", "buying_power": "400000", "options_buying_power": "200000", "last_equity": "100000"}
    proposal = {
        "strategy": "BULL_PUT_SPREAD",
        "symbol": "SPY",
        "legs": [
            {"symbol": "SPY260904P00580000", "side": "sell", "qty": 1, "limit_price": 1.2, "role": "short_put"},
            {"symbol": "SPY260904P00570000", "side": "buy", "qty": 1, "limit_price": 0.6, "role": "long_put"},
        ],
        "qty": 1,
        "max_loss": 400,
    }
    chain = [{"expiration": "2026-09-04"}]
    passed, reasons = validate_trade(acct, [], [], proposal, chain, initial_equity=100000)
    assert passed
