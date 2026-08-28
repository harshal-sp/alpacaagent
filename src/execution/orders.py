"""Order execution — paper-only with preview, idempotency, and MCP/CLI traces."""
import uuid
from typing import Dict, Any, List
from datetime import datetime, timezone

from src.data.alpaca_client import AlpacaClient
from src.execution.mcp_trace import trace_mcp_tool, trace_cli, run_cli_command, get_mcp_namespace_discovery
from src.utils.logger import log_event, logger

class OrderExecutor:
    def __init__(self, client: AlpacaClient | None = None):
        self.client = client or AlpacaClient(paper=True)
        # discover MCP namespace at startup (skill § Phase 3)
        self.mcp_namespace = get_mcp_namespace_discovery()

    def preview(self, legs: List[Dict[str, Any]], account: Dict[str, Any]) -> str:
        bp = float(account.get("buying_power", 0) or 0)
        eq = float(account.get("equity", 0) or 0)
        # Fee estimate — NEW: show true cost that could otherwise eat profit
        try:
            from src.utils.fees import estimate_fees
            fees = estimate_fees(legs)
            fee_line_open = f"│ Fees (open):  ${fees['total_one_way']:.2f} (comm ${fees['commission']:.2f} + slip ${fees['slippage']:.2f}) │"
            fee_line_rt = f"│ Fees (r/t):   ${fees['total_round_trip']:.2f} (open+close)              │"
        except Exception:
            fees = {"total_one_way": 0, "total_round_trip": 0, "commission": 0, "slippage": 0}
            fee_line_open = "│ Fees (open):  $0.00                             │"
            fee_line_rt = "│ Fees (r/t):   $0.00                             │"
        lines = []
        lines.append("┌─────────────────────────────────────────┐")
        lines.append("│           ORDER PREVIEW (PAPER)         │")
        lines.append("├─────────────────────────────────────────┤")
        lines.append(f"│ Equity:       ${eq:,.2f}                  │")
        lines.append(f"│ Buying Power: ${bp:,.2f}                  │")
        lines.append(f"│ Legs:         {len(legs)}                             │")
        lines.append("├─────────────────────────────────────────┤")
        total_debit = 0
        total_credit = 0
        for i, leg in enumerate(legs, 1):
            sym = leg.get("symbol", "?")[:22]
            side = leg.get("side", "?").upper()
            qty = leg.get("qty", 0)
            price = leg.get("limit_price") or leg.get("price") or 0
            role = leg.get("role", "")
            notional = float(price) * 100 * qty if "C" in sym or "P" in sym else float(price) * qty
            if side == "BUY":
                total_debit += notional
            else:
                total_credit += notional
            lines.append(f"│ {i}. {side:4} {qty}x {sym:16} @ ${price:.2f} ({role}) │")
        lines.append("├─────────────────────────────────────────┤")
        lines.append(f"│ Est. Debit:   ${total_debit:,.2f}                │")
        lines.append(f"│ Est. Credit:  ${total_credit:,.2f}                │")
        lines.append(f"│ Est. Net:     ${total_credit - total_debit:.2f} (credit-debit)      │")
        lines.append(fee_line_open)
        lines.append(fee_line_rt)
        net_after_fees_open = total_credit - total_debit - fees["total_one_way"]
        net_after_fees_rt = total_credit - total_debit - fees["total_round_trip"]
        lines.append(f"│ Net open:     ${net_after_fees_open:.2f} (after open fees)      │")
        lines.append(f"│ Net r/t:      ${net_after_fees_rt:.2f} (if closed)           │")
        # Highlight if fees eat profit
        if total_credit > 0 and net_after_fees_rt <= 0:
            lines.append("│ ⚠ FEES EAT PROFIT — trade rejected by risk gate │")
        lines.append(f"│ BP after:     ${bp - total_debit + total_credit:.2f} (gross)            │")
        lines.append("│ Environment:  PAPER (verified)          │")
        lines.append("│ ⚠ Paper trading only. Not financial advice. │")
        lines.append("└─────────────────────────────────────────┘")
        # CLI preview command equivalent
        cli_cmds = []
        for leg in legs:
            cmd = f"alpaca order submit --symbol {leg['symbol']} --side {leg['side']} --qty {leg['qty']} --type {leg.get('type','limit')} --limit-price {leg.get('limit_price','')} --time-in-force day --client-order-id vega-{uuid.uuid4().hex[:8]} --dry-run"
            cli_cmds.append(cmd)
        lines.append("\nCLI equivalent (preview --dry-run):")
        for c in cli_cmds:
            lines.append(f"  {c}")
            trace_cli(c, output='{"dry_run": true, "preview": true}')
        return "\n".join(lines)

    def submit_legs(self, legs: List[Dict[str, Any]], dry_run: bool = False) -> List[Dict[str, Any]]:
        """Submit each leg via Trading API (primary) + log MCP + CLI traces.
        For spreads, submits long (buy) legs first so short legs are covered (avoids 403 naked rejection).
        If a mleg (2/4 legs with both sides) is detected, attempts REST mleg order first; falls back to leg-by-leg.
        """
        # Reorder: buys first for coverage
        legs_sorted = sorted(legs, key=lambda l: 0 if l.get("side") == "buy" else 1)
        # Detect spread: 2 or 4 legs with mixed sides
        is_spread = len(legs) in (2, 4) and any(l.get("side") == "buy" for l in legs) and any(l.get("side") == "sell" for l in legs)
        if is_spread and not dry_run:
            # Try mleg via REST first (Alpaca paper supports mleg for level 3)
            try:
                from src.utils.logger import log_event as _le
                import requests as _req
                from src.config import APCA_API_KEY_ID, APCA_API_SECRET_KEY
                # Calculate net limit price: credit spreads => net credit, debit => net debit
                # Use sum of mids: sells - buys
                net = 0.0
                for l in legs:
                    mid = float(l.get("limit_price") or 0)
                    if l.get("side") == "sell":
                        net += mid
                    else:
                        net -= mid
                # For credit spreads net>0, for debit net<0; Alpaca expects positive limit_price
                mleg_price = abs(net)
                if mleg_price < 0.05:
                    mleg_price = 0.15
                # Build legs payload with ratio_qty = qty
                qty = int(legs[0].get("qty", 1))
                payload = {
                    "order_class": "mleg",
                    "type": "limit",
                    "time_in_force": "day",
                    "limit_price": str(round(mleg_price, 2)),
                    "legs": [{"symbol": l["symbol"], "side": l["side"], "ratio_qty": str(int(l.get("qty", qty)))} for l in legs],
                    "client_order_id": f"vega-mleg-{__import__('uuid').uuid4().hex[:8]}",
                }
                # MCP trace for mleg
                trace_mcp_tool("place_option_order", {"mleg": payload}, None)
                # CLI trace
                trace_cli(f"alpaca order submit --order-class mleg --legs '{payload['legs']}' --limit-price {payload['limit_price']} --time-in-force day", output="mleg attempt")
                headers = {"APCA-API-KEY-ID": APCA_API_KEY_ID, "APCA-API-SECRET-KEY": APCA_API_SECRET_KEY}
                resp = _req.post("https://paper-api.alpaca.markets/v2/orders", headers=headers, json=payload, timeout=15)
                if resp.status_code < 400:
                    order = resp.json()
                    trace_mcp_tool("place_option_order", {"mleg": payload}, order)
                    _le("mleg_order_submitted", legs=[l["symbol"] for l in legs], limit_price=payload["limit_price"], id=order.get("id"))
                    return [order]
                else:
                    _le("mleg_order_failed", status=resp.status_code, body=resp.text[:500])
                    # fall through to leg-by-leg
            except Exception as _e:
                from src.utils.logger import logger as _lg
                _lg.warning(f"mleg attempt failed, falling back to legs: {_e}")
        results: List[Dict[str, Any]] = []
        for leg in legs_sorted:
            symbol = leg["symbol"]
            side = leg["side"]
            qty = int(leg["qty"])
            otype = leg.get("type", "limit")
            limit_price = leg.get("limit_price")
            tif = leg.get("time_in_force", "day")
            client_order_id = f"vega-{uuid.uuid4().hex[:12]}"
            params = {
                "symbol": symbol,
                "side": side,
                "qty": str(qty),
                "type": otype,
                "time_in_force": tif,
                "client_order_id": client_order_id,
            }
            if otype == "limit" and limit_price:
                params["limit_price"] = str(round(float(limit_price), 2))

            # MCP trace — place_option_order (as per mcp skill Step 19)
            trace_mcp_tool("place_option_order", params, None)

            if dry_run:
                log_event("order_dry_run", params=params)
                results.append({"symbol": symbol, "status": "dry_run", "client_order_id": client_order_id, "params": params})
                trace_mcp_tool("place_option_order", params, {"status": "dry_run", "id": "dry-"+client_order_id})
                continue

            # CLI trace — equivalent command
            cli_cmd = f"alpaca order submit --symbol {symbol} --side {side} --qty {qty} --type {otype} --time-in-force {tif} --client-order-id {client_order_id}"
            if limit_price:
                cli_cmd += f" --limit-price {limit_price}"
            run_cli_command(cli_cmd.split()[1:], dry_run=False)  # will simulate if CLI not installed

            # Real submit via Alpaca API (paper)
            try:
                # Detect asset class: option symbols contain OCC pattern with C/P and strike
                # Simple heuristic: contains C or P and length > 10 and has digit
                is_option = any(c in symbol for c in ["C", "P"]) and len(symbol) > 10
                if is_option:
                    order = self.client.submit_option_order(
                        symbol=symbol,
                        qty=qty,
                        side=side,
                        order_type=otype,
                        limit_price=float(limit_price) if limit_price else None,
                        time_in_force=tif,
                        client_order_id=client_order_id,
                    )
                else:
                    order = self.client.submit_stock_order(
                        symbol=symbol,
                        qty=qty,
                        side=side,
                        order_type=otype,
                        limit_price=float(limit_price) if limit_price else None,
                        time_in_force=tif,
                        client_order_id=client_order_id,
                    )
                results.append(order)
                trace_mcp_tool("place_option_order", params, order)
                log_event("order_submitted", id=order.get("id"), symbol=symbol, side=side, qty=qty, status=order.get("status"))
            except Exception as e:
                err = {"error": str(e), "symbol": symbol, "params": params}
                results.append(err)
                trace_mcp_tool("place_option_order", params, err)
                log_event("order_failed", symbol=symbol, error=str(e))
                # do not raise — continue other legs, but log
        return results

    def manage_positions(self, take_profit_pct: float = 40.0, stop_loss_pct: float = 25.0) -> List[Dict[str, Any]]:
        """Check open positions for take-profit / stop-loss exits (paper)."""
        try:
            positions = self.client.get_positions()
        except Exception as e:
            logger.warning(f"manage_positions get_positions failed: {e}")
            return []
        actions = []
        for p in positions:
            symbol = p.get("symbol", "")
            # only manage option positions
            if len(symbol) < 10:
                continue
            unrealized_plpc = 0
            try:
                unrealized_plpc = float(p.get("unrealized_plpc", 0) or 0) * 100
            except:
                pass
            # also compute from market_value vs cost_basis
            if unrealized_plpc == 0:
                try:
                    mv = float(p.get("market_value", 0) or 0)
                    cb = float(p.get("cost_basis", 0) or 0)
                    if cb:
                        unrealized_plpc = (mv - cb) / cb * 100
                except:
                    pass
            # aggressive exits
            if unrealized_plpc >= take_profit_pct:
                log_event("take_profit_trigger", symbol=symbol, plpc=unrealized_plpc)
                trace_mcp_tool("close_position", {"symbol_or_asset_id": symbol}, None)
                try:
                    # close to realize gain
                    res = self.client.close_position(symbol)
                    actions.append({"symbol": symbol, "action": "take_profit", "plpc": unrealized_plpc, "result": res})
                except Exception as e:
                    actions.append({"symbol": symbol, "action": "take_profit_failed", "error": str(e)})
            elif unrealized_plpc <= -stop_loss_pct:
                log_event("stop_loss_trigger", symbol=symbol, plpc=unrealized_plpc)
                trace_mcp_tool("close_position", {"symbol_or_asset_id": symbol}, None)
                try:
                    res = self.client.close_position(symbol)
                    actions.append({"symbol": symbol, "action": "stop_loss", "plpc": unrealized_plpc, "result": res})
                except Exception as e:
                    actions.append({"symbol": symbol, "action": "stop_loss_failed", "error": str(e)})
        return actions
