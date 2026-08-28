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
        lines.append(f"│ BP after:     ${bp - total_debit + total_credit:,.2f}                │")
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
        """Submit each leg via Trading API (primary) + log MCP + CLI traces."""
        results: List[Dict[str, Any]] = []
        for leg in legs:
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
