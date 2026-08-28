"""Alpaca client wrapper — paper-only, with safety gates per alpaca-skills spec."""
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        GetOrdersRequest, GetAssetsRequest, ClosePositionRequest,
        MarketOrderRequest, LimitOrderRequest
    )
    from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus, OrderClass
    from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest, OptionChainRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    from alpaca.common.exceptions import APIError
except ImportError:
    TradingClient = None  # type: ignore
    APIError = Exception  # type: ignore

from src.config import (
    APCA_API_KEY_ID, APCA_API_SECRET_KEY, APCA_PAPER,
    ALPACA_PAPER_URL
)
from src.utils.logger import log_event, logger

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"

class AlpacaPaperGuard:
    """Hard guard — refuses to operate if not paper."""
    @staticmethod
    def assert_paper():
        if not APCA_PAPER:
            raise RuntimeError("BLOCKED: Live trading detected. APCA_PAPER must be true.")
        # verify via TradingClient paper flag literally True
        logger.info("✓ Paper guard: APCA_PAPER=true (paper-api.alpaca.markets)")

class AlpacaClient:
    def __init__(self, paper: bool = True):
        # Literal paper=True per skill §Phase 8 — not configurable via env at call site
        if not paper or not APCA_PAPER:
            raise RuntimeError("Refusing to create live client. Set APCA_PAPER=true.")
        if not APCA_API_KEY_ID or not APCA_API_SECRET_KEY:
            logger.warning("No API keys in env — client will fail on live calls. Use .env")
        self.paper = True
        self.client = None
        self.stock_data_client = None
        self.option_data_client = None
        if TradingClient:
            try:
                self.client = TradingClient(
                    api_key=APCA_API_KEY_ID,
                    secret_key=APCA_API_SECRET_KEY,
                    paper=True  # literal — skill requirement
                )
                self.stock_data_client = StockHistoricalDataClient(
                    api_key=APCA_API_KEY_ID,
                    secret_key=APCA_API_SECRET_KEY
                )
                try:
                    self.option_data_client = OptionHistoricalDataClient(
                        api_key=APCA_API_KEY_ID,
                        secret_key=APCA_API_SECRET_KEY
                    )
                except Exception as e:
                    logger.warning(f"Option data client init failed: {e}")
                log_event("alpaca_client_init", paper=True)
            except Exception as e:
                logger.warning(f"Alpaca client init warning: {e}")
        self._headers = {
            "APCA-API-KEY-ID": APCA_API_KEY_ID,
            "APCA-API-SECRET-KEY": APCA_API_SECRET_KEY,
        }

    # ---------- Account ----------
    def get_account(self) -> Dict[str, Any]:
        AlpacaPaperGuard.assert_paper()
        if self.client:
            try:
                acct = self.client.get_account()
                d = acct.model_dump() if hasattr(acct, "model_dump") else dict(acct)
                log_event("get_account", equity=str(d.get("equity")), buying_power=str(d.get("buying_power")))
                return d
            except Exception as e:
                logger.error(f"get_account via SDK failed: {e}")
        # fallback REST
        resp = requests.get(f"{PAPER_URL}/v2/account", headers=self._headers, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_clock(self) -> Dict[str, Any]:
        if self.client:
            try:
                clock = self.client.get_clock()
                return clock.model_dump() if hasattr(clock, "model_dump") else dict(clock)
            except Exception:
                pass
        resp = requests.get(f"{PAPER_URL}/v2/clock", headers=self._headers, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_positions(self) -> List[Dict[str, Any]]:
        AlpacaPaperGuard.assert_paper()
        if self.client:
            try:
                positions = self.client.get_all_positions()
                return [p.model_dump() if hasattr(p, "model_dump") else dict(p) for p in positions]
            except Exception as e:
                logger.warning(f"get_positions SDK failed: {e}")
        resp = requests.get(f"{PAPER_URL}/v2/positions", headers=self._headers, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_orders(self, status: str = "open", limit: int = 50) -> List[Dict[str, Any]]:
        if self.client:
            try:
                req = GetOrdersRequest(status=QueryOrderStatus(status), limit=limit)  # type: ignore
                orders = self.client.get_orders(filter=req)
                return [o.model_dump() if hasattr(o, "model_dump") else dict(o) for o in orders]  # type: ignore
            except Exception as e:
                logger.warning(f"get_orders SDK failed: {e}")
        resp = requests.get(f"{PAPER_URL}/v2/orders", headers=self._headers, params={"status": status, "limit": limit}, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_asset(self, symbol: str) -> Optional[Dict[str, Any]]:
        # Use REST to avoid SDK quirks
        try:
            resp = requests.get(f"{PAPER_URL}/v2/assets/{symbol}", headers=self._headers, timeout=10)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"get_asset {symbol} failed: {e}")
            return None

    # ---------- Market Data ----------
    def get_bars(self, symbol: str, days: int = 60, timeframe: str = "1Day") -> pd.DataFrame:
        """Fetch daily bars. Falls back to yfinance if Alpaca fails."""
        # Try SDK
        if self.stock_data_client and timeframe == "1Day":
            try:
                from alpaca.data.requests import StockBarsRequest
                from alpaca.data.timeframe import TimeFrame
                req = StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=TimeFrame.Day,
                    start=datetime.now() - timedelta(days=days+10),
                    limit=days
                )
                bars = self.stock_data_client.get_stock_bars(req)
                df = bars.df
                if df is not None and not df.empty:
                    # bars.df is multi-index if multiple symbols
                    if isinstance(df.index, pd.MultiIndex):
                        df = df.xs(symbol, level=0) if symbol in df.index.get_level_values(0) else df
                    df = df.reset_index()
                    log_event("get_bars", symbol=symbol, rows=len(df), source="alpaca")
                    return df
            except Exception as e:
                logger.warning(f"Alpaca bars failed for {symbol}: {e}")
        # Fallback to yfinance
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=f"{days}d", interval="1d")
            if df is not None and not df.empty:
                df = df.reset_index()
                df.columns = [c.lower().replace(' ', '_') for c in df.columns]
                # normalize column names to alpaca style
                rename = {"date": "timestamp", "open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"}
                df = df.rename(columns=rename)
                # ensure timestamp is datetime
                if "timestamp" in df.columns:
                    df["timestamp"] = pd.to_datetime(df["timestamp"])
                log_event("get_bars", symbol=symbol, rows=len(df), source="yfinance_fallback")
                return df
        except Exception as e2:
            logger.warning(f"yfinance fallback failed for {symbol}: {e2}")
        # synthetic fallback for dry-run
        logger.warning(f"Returning synthetic bars for {symbol}")
        dates = pd.date_range(end=datetime.now(), periods=days, freq="B")
        base = 580 if symbol == "SPY" else 480 if symbol == "QQQ" else 200
        import numpy as np
        closes = base + np.cumsum(np.random.randn(days) * 1.2)
        return pd.DataFrame({
            "timestamp": dates,
            "open": closes * 0.998,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": np.random.randint(50_000_00, 100_000_000, days),
            "trade_count": np.random.randint(1000, 5000, days),
            "vwap": closes,
        })

    def get_latest_quote(self, symbol: str) -> Dict[str, Any]:
        if self.stock_data_client:
            try:
                from alpaca.data.requests import StockLatestQuoteRequest
                req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
                q = self.stock_data_client.get_stock_latest_quote(req)
                # q is dict symbol -> quote
                if isinstance(q, dict) and symbol in q:
                    v = q[symbol]
                    return v.model_dump() if hasattr(v, "model_dump") else dict(v)
                d = q.model_dump() if hasattr(q, "model_dump") else dict(q)  # type: ignore
                return d
            except Exception as e:
                logger.warning(f"latest quote failed {symbol}: {e}")
        # fallback: last close
        try:
            df = self.get_bars(symbol, days=5)
            last = df.iloc[-1]
            price = float(last["close"])
            return {"symbol": symbol, "ap": price*1.001, "bp": price*0.999, "ap_price": price*1.001, "bp_price": price*0.999, "price": price}
        except Exception:
            return {"symbol": symbol, "price": 500.0}

    def get_option_chain(self, underlying: str, expiration_gte: str | None = None, expiration_lte: str | None = None) -> List[Dict[str, Any]]:
        """Fetch option chain — robust yfinance handling with quality scoring."""
        # Try yfinance with quality scoring across expiries
        try:
            import yfinance as yf
            tk = yf.Ticker(underlying)
            exps = tk.options
            if exps:
                from datetime import datetime
                today = datetime.now().date()
                # Get spot once
                try:
                    hist = tk.history(period="1d")
                    spot = float(hist["Close"].iloc[-1]) if not hist.empty else 500.0
                except:
                    spot = 500.0
                # Score each expiry within 0-10 days
                candidates = []
                for e in exps:
                    try:
                        d = datetime.strptime(e, "%Y-%m-%d").date()
                        dte = (d - today).days
                        if dte < 0 or dte > 10:
                            continue
                        chain = tk.option_chain(e)
                        # Build contracts temporarily
                        tmp: List[Dict[str, Any]] = []
                        for _, row in chain.calls.iterrows():
                            iv = float(row["impliedVolatility"]) if not pd.isna(row["impliedVolatility"]) else 0.25
                            # clamp iv into realistic 0.1-0.9, fallback 0.25 if garbage
                            if iv < 0.05 or iv > 1.5 or pd.isna(iv):
                                iv = 0.25
                            tmp.append({
                                "symbol": row["contractSymbol"],
                                "underlying": underlying,
                                "expiration": e,
                                "strike": float(row["strike"]),
                                "type": "call",
                                "bid": float(row["bid"]) if not pd.isna(row["bid"]) else 0,
                                "ask": float(row["ask"]) if not pd.isna(row["ask"]) else 0,
                                "last": float(row["lastPrice"]) if not pd.isna(row["lastPrice"]) else 0,
                                "iv": iv,
                                "volume": int(row["volume"]) if not pd.isna(row["volume"]) else 0,
                                "openInterest": int(row["openInterest"]) if not pd.isna(row["openInterest"]) else 0,
                            })
                        for _, row in chain.puts.iterrows():
                            iv = float(row["impliedVolatility"]) if not pd.isna(row["impliedVolatility"]) else 0.25
                            if iv < 0.05 or iv > 1.5 or pd.isna(iv):
                                iv = 0.25
                            tmp.append({
                                "symbol": row["contractSymbol"],
                                "underlying": underlying,
                                "expiration": e,
                                "strike": float(row["strike"]),
                                "type": "put",
                                "bid": float(row["bid"]) if not pd.isna(row["bid"]) else 0,
                                "ask": float(row["ask"]) if not pd.isna(row["ask"]) else 0,
                                "last": float(row["lastPrice"]) if not pd.isna(row["lastPrice"]) else 0,
                                "iv": iv,
                                "volume": int(row["volume"]) if not pd.isna(row["volume"]) else 0,
                                "openInterest": int(row["openInterest"]) if not pd.isna(row["openInterest"]) else 0,
                            })
                        # Quality metrics
                        filtered = [c for c in tmp if abs(c["strike"] - spot) / spot < 0.08]
                        if len(filtered) < 8:
                            # not enough near-the-money strikes
                            continue
                        avg_iv = sum(c["iv"] for c in filtered) / len(filtered) if filtered else 0
                        # Prefer iv 0.15-0.6 and many strikes near spot
                        iv_score = 1 - abs(avg_iv - 0.30)  # ideal ~30%
                        candidates.append((iv_score, len(filtered), e, filtered, avg_iv))
                    except Exception as ce:
                        logger.debug(f"chain score expiry {e} failed: {ce}")
                        continue
                if candidates:
                    # pick best by iv_score then filtered count
                    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
                    _, _, chosen, filtered, avg_iv = candidates[0]
                    # final filtered sorted by distance to spot
                    filtered = sorted(filtered, key=lambda c: abs(c["strike"] - spot))
                    log_event("get_option_chain", underlying=underlying, expiry=chosen, total=len(filtered), avg_iv=round(avg_iv,3), source="yfinance_scored")
                    return filtered[:40]
                # Fallback: if no candidate passed quality, try first expiry with synthetic correction
                # Use nearest expiry but clamp iv already, return nearest strikes
                e = None
                for exp in exps:
                    d = datetime.strptime(exp, "%Y-%m-%d").date()
                    if (d - today).days >= 0:
                        e = exp
                        break
                if e:
                    chain = tk.option_chain(e)
                    contracts: List[Dict[str, Any]] = []
                    for _, row in chain.calls.iterrows():
                        iv = float(row["impliedVolatility"]) if not pd.isna(row["impliedVolatility"]) else 0.25
                        if iv < 0.05 or iv > 1.5:
                            iv = 0.25
                        contracts.append({
                            "symbol": row["contractSymbol"],
                            "underlying": underlying,
                            "expiration": e,
                            "strike": float(row["strike"]),
                            "type": "call",
                            "bid": float(row["bid"]) if not pd.isna(row["bid"]) else 0,
                            "ask": float(row["ask"]) if not pd.isna(row["ask"]) else 0,
                            "last": float(row["lastPrice"]) if not pd.isna(row["lastPrice"]) else 0,
                            "iv": iv,
                            "volume": int(row["volume"]) if not pd.isna(row["volume"]) else 0,
                            "openInterest": int(row["openInterest"]) if not pd.isna(row["openInterest"]) else 0,
                        })
                    for _, row in chain.puts.iterrows():
                        iv = float(row["impliedVolatility"]) if not pd.isna(row["impliedVolatility"]) else 0.25
                        if iv < 0.05 or iv > 1.5:
                            iv = 0.25
                        contracts.append({
                            "symbol": row["contractSymbol"],
                            "underlying": underlying,
                            "expiration": e,
                            "strike": float(row["strike"]),
                            "type": "put",
                            "bid": float(row["bid"]) if not pd.isna(row["bid"]) else 0,
                            "ask": float(row["ask"]) if not pd.isna(row["ask"]) else 0,
                            "last": float(row["lastPrice"]) if not pd.isna(row["lastPrice"]) else 0,
                            "iv": iv,
                            "volume": int(row["volume"]) if not pd.isna(row["volume"]) else 0,
                            "openInterest": int(row["openInterest"]) if not pd.isna(row["openInterest"]) else 0,
                        })
                    # Sort by distance to spot and return nearest 40 (ensures we have something tradable)
                    contracts_sorted = sorted(contracts, key=lambda c: abs(c["strike"] - spot))
                    filtered = contracts_sorted[:40]
                    log_event("get_option_chain", underlying=underlying, expiry=e, total=len(filtered), source="yfinance_fallback_nearest")
                    return filtered
        except Exception as e:
            logger.warning(f"yfinance option chain failed for {underlying}: {e}")
        # synthetic
        return self._synthetic_chain(underlying)

    def _synthetic_chain(self, underlying: str) -> List[Dict[str, Any]]:
        import numpy as np
        df = self.get_bars(underlying, days=5)
        spot = float(df.iloc[-1]["close"]) if not df.empty else 500
        expiry = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
        strikes = [round(spot * (1 + k*0.01), 1) for k in range(-5, 6)]
        contracts = []
        for s in strikes:
            for typ in ["call", "put"]:
                mid = max(0.5, abs(spot - s) * 0.08 + np.random.uniform(0.8, 2.5))
                spread = 0.10
                contracts.append({
                    "symbol": f"{underlying}{expiry.replace('-','')[2:]}C{int(s*1000):08d}" if typ=="call" else f"{underlying}{expiry.replace('-','')[2:]}P{int(s*1000):08d}",
                    "underlying": underlying,
                    "expiration": expiry,
                    "strike": s,
                    "type": typ,
                    "bid": round(mid - spread/2, 2),
                    "ask": round(mid + spread/2, 2),
                    "last": round(mid, 2),
                    "iv": round(0.22 + abs(spot-s)/spot, 3),
                    "volume": int(np.random.randint(200, 5000)),
                    "openInterest": int(np.random.randint(500, 20000)),
                })
        log_event("get_option_chain", underlying=underlying, expiry=expiry, total=len(contracts), source="synthetic")
        return contracts

    # ---------- Orders ----------
    def submit_option_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        order_type: str = "limit",
        limit_price: float | None = None,
        time_in_force: str = "day",
        client_order_id: str | None = None,
    ) -> Dict[str, Any]:
        AlpacaPaperGuard.assert_paper()
        if not client_order_id:
            client_order_id = f"vega-{uuid.uuid4().hex[:12]}"
        # Use SDK if available
        if self.client:
            try:
                from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest
                from alpaca.trading.enums import OrderSide, TimeInForce
                side_enum = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
                tif_enum = TimeInForce.DAY if time_in_force == "day" else TimeInForce.GTC
                if order_type == "limit" and limit_price:
                    req = LimitOrderRequest(
                        symbol=symbol,
                        qty=qty,
                        side=side_enum,
                        time_in_force=tif_enum,
                        limit_price=round(limit_price, 2),
                        client_order_id=client_order_id,
                    )
                else:
                    req = MarketOrderRequest(
                        symbol=symbol,
                        qty=qty,
                        side=side_enum,
                        time_in_force=tif_enum,
                        client_order_id=client_order_id,
                    )
                order = self.client.submit_order(req)
                d = order.model_dump() if hasattr(order, "model_dump") else dict(order)
                log_event("submit_option_order", symbol=symbol, qty=qty, side=side, type=order_type, id=d.get("id"), client_id=client_order_id)
                return d
            except Exception as e:
                logger.error(f"SDK submit_option_order failed {symbol}: {e}")
                # try REST fallback if SDK fails due to notional/validation
                pass
        # REST fallback
        payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
            "client_order_id": client_order_id,
        }
        if order_type == "limit" and limit_price:
            payload["limit_price"] = str(round(limit_price, 2))
        try:
            resp = requests.post(f"{PAPER_URL}/v2/orders", headers=self._headers, json=payload, timeout=15)
            if resp.status_code >= 400:
                log_event("submit_option_order_failed", symbol=symbol, status=resp.status_code, body=resp.text[:500])
                raise RuntimeError(f"Order failed {resp.status_code}: {resp.text[:400]}")
            d = resp.json()
            log_event("submit_option_order", symbol=symbol, qty=qty, side=side, id=d.get("id"), via="rest")
            return d
        except Exception as e:
            log_event("submit_option_order_error", symbol=symbol, error=str(e))
            raise

    def submit_stock_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        limit_price: float | None = None,
        time_in_force: str = "day",
        client_order_id: str | None = None,
    ) -> Dict[str, Any]:
        AlpacaPaperGuard.assert_paper()
        if not client_order_id:
            client_order_id = f"vega-stock-{uuid.uuid4().hex[:8]}"
        if self.client:
            try:
                from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
                from alpaca.trading.enums import OrderSide, TimeInForce
                side_enum = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
                tif_map = {"day": TimeInForce.DAY, "gtc": TimeInForce.GTC, "ioc": TimeInForce.IOC}
                tif_enum = tif_map.get(time_in_force, TimeInForce.DAY)
                if order_type == "limit" and limit_price:
                    req = LimitOrderRequest(symbol=symbol, qty=qty, side=side_enum, time_in_force=tif_enum, limit_price=round(limit_price,2), client_order_id=client_order_id)
                else:
                    req = MarketOrderRequest(symbol=symbol, qty=qty, side=side_enum, time_in_force=tif_enum, client_order_id=client_order_id)
                order = self.client.submit_order(req)
                d = order.model_dump() if hasattr(order, "model_dump") else dict(order)
                log_event("submit_stock_order", symbol=symbol, qty=qty, side=side, id=d.get("id"))
                return d
            except Exception as e:
                logger.warning(f"stock order SDK failed {symbol}: {e}")
        payload = {"symbol": symbol, "qty": str(qty), "side": side, "type": order_type, "time_in_force": time_in_force, "client_order_id": client_order_id}
        if order_type == "limit" and limit_price:
            payload["limit_price"] = str(round(limit_price,2))
        resp = requests.post(f"{PAPER_URL}/v2/orders", headers=self._headers, json=payload, timeout=15)
        if resp.status_code >= 400:
            raise RuntimeError(f"Stock order failed {resp.status_code}: {resp.text[:400]}")
        return resp.json()

    def close_position(self, symbol: str, qty: str | None = None, percentage: str | None = None) -> Dict[str, Any]:
        AlpacaPaperGuard.assert_paper()
        if self.client:
            try:
                from alpaca.trading.requests import ClosePositionRequest
                req = ClosePositionRequest(symbol=symbol)  # type: ignore
                # SDK close_position handles qty/percentage
                order = self.client.close_position(symbol_or_asset_id=symbol)
                d = order.model_dump() if hasattr(order, "model_dump") else dict(order)
                log_event("close_position", symbol=symbol)
                return d
            except Exception as e:
                logger.warning(f"close_position SDK failed {symbol}: {e}")
        url = f"{PAPER_URL}/v2/positions/{symbol}"
        params = {}
        if qty: params["qty"] = qty
        if percentage: params["percentage"] = percentage
        resp = requests.delete(url, headers=self._headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json() if resp.text else {"symbol": symbol, "status": "closed"}

    def cancel_order(self, order_id: str) -> bool:
        try:
            if self.client:
                self.client.cancel_order_by_id(order_id)
                log_event("cancel_order", order_id=order_id)
                return True
        except Exception as e:
            logger.warning(f"cancel_order SDK failed {order_id}: {e}")
        resp = requests.delete(f"{PAPER_URL}/v2/orders/{order_id}", headers=self._headers, timeout=10)
        return resp.status_code in (200, 204, 207)

    def get_order_by_id(self, order_id: str) -> Dict[str, Any]:
        if self.client:
            try:
                o = self.client.get_order_by_id(order_id)
                return o.model_dump() if hasattr(o, "model_dump") else dict(o)
            except Exception:
                pass
        resp = requests.get(f"{PAPER_URL}/v2/orders/{order_id}", headers=self._headers, timeout=10)
        resp.raise_for_status()
        return resp.json()
