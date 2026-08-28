"""Vega — autonomous aggressive options alpha agent — main loop."""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

from src.config import UNIVERSE, RISK_CONFIG, PROJECT_ROOT, APCA_PAPER, APCA_API_KEY_ID
from src.data.alpaca_client import AlpacaClient
from src.data.earnings import get_upcoming_earnings
from src.features.indicators import compute_features
from src.brain.llm import classify
from src.strategy.selector import build_legs
from src.risk.gates import validate_trade
from src.execution.orders import OrderExecutor
from src.utils.logger import log_event, logger
from src.utils.market_hours import is_market_open

BANNER = """
██╗   ██╗███████╗ ██████╗  █████╗
██║   ██║██╔════╝██╔════╝ ██╔══██╗
██║   ██║█████╗  ██║  ███╗███████║  Vega — Autonomous Options Alpha Agent
╚██╗ ██╔╝██╔══╝  ██║   ██║██╔══██║  Aggressive | Paper-Only | MCP+CLI
 ╚████╔╝ ███████╗╚██████╔╝██║  ██║  Alpaca AI Trading Agents Hackathon
  ╚═══╝  ╚══════╝ ╚═════╝ ╚═╝  ╚═╝
"""

def run_cycle(dry_run: bool = False, force: bool = False, symbol_filter: str | None = None) -> Dict[str, Any]:
    print(BANNER)
    logger.info(f"Starting Vega cycle dry_run={dry_run} force={force} paper={APCA_PAPER} key={APCA_API_KEY_ID[:6]}...")
    if not APCA_PAPER:
        raise RuntimeError("BLOCKED: Live trading detected — aborting.")

    client = AlpacaClient(paper=True)
    executor = OrderExecutor(client)

    # Clock & account
    try:
        clock = client.get_clock()
        is_open = clock.get("is_open", is_market_open())
        next_open = clock.get("next_open", "")
        logger.info(f"Market clock: is_open={is_open} next_open={next_open}")
    except Exception as e:
        logger.warning(f"clock fetch failed: {e}")
        is_open = is_market_open()

    if not is_open and not force and not dry_run:
        msg = "Market closed — skipping trading cycle (use --force to override, --dry-run for simulation)"
        logger.info(msg)
        log_event("cycle_skipped_market_closed", reason=msg)
        return {"status": "skipped", "reason": "market_closed"}

    try:
        account = client.get_account()
        positions = client.get_positions()
        orders = client.get_orders(status="open")
    except Exception as e:
        logger.error(f"Failed to fetch account/positions: {e}")
        # synthetic account for dry-run
        account = {"equity": "100000", "buying_power": "400000", "cash": "100000", "options_buying_power": "200000", "status": "ACTIVE", "trading_blocked": False}
        positions = []
        orders = []

    equity = float(account.get("equity", 100000) or 100000)
    bp = float(account.get("buying_power", 400000) or 0)
    logger.info(f"Account equity=${equity:,.2f} BP=${bp:,.2f} positions={len(positions)} open_orders={len(orders)}")

    # Earnings calendar
    try:
        earnings = get_upcoming_earnings(UNIVERSE, days_ahead=7)
        logger.info(f"Earnings watch: {earnings}")
    except Exception as e:
        logger.warning(f"earnings fetch failed: {e}")
        earnings = []

    # Manage existing positions — take profit / stop loss
    if not dry_run and is_open:
        try:
            actions = executor.manage_positions(
                take_profit_pct=RISK_CONFIG["take_profit_pct_per_position"],
                stop_loss_pct=RISK_CONFIG["stop_loss_pct_per_position"],
            )
            if actions:
                logger.info(f"Position management actions: {actions}")
                log_event("position_management", actions=actions)
        except Exception as e:
            logger.warning(f"position management failed: {e}")

    # Evaluate universe
    universe = [symbol_filter] if symbol_filter else UNIVERSE
    best_proposal = None
    best_confidence = -1
    best_features = None
    best_decision = None
    all_evals: List[Dict[str, Any]] = []

    for symbol in universe:
        logger.info(f"\n── Evaluating {symbol} ──")
        try:
            bars = client.get_bars(symbol, days=60)
            chain = client.get_option_chain(symbol)
            features = compute_features(symbol, bars, chain)
            logger.info(f"Features {symbol}: {features}")

            # Skip if no chain
            if not chain:
                logger.warning(f"{symbol} no option chain — skip")
                continue

            decision = classify(features, earnings)
            logger.info(f"LLM decision {symbol}: {decision}")
            log_event("symbol_eval", symbol=symbol, features=features, decision=decision)

            # Track best by confidence (and strategy != NO_TRADE)
            conf = decision.get("confidence", 0)
            if decision.get("strategy") != "NO_TRADE" and conf > best_confidence:
                # Build legs to validate feasibility
                spot = features.get("last", 500)
                proposal = build_legs(decision, spot, chain, bp)
                if proposal.get("legs"):
                    # risk gate preview
                    passed, reasons = validate_trade(account, positions, orders, {"symbol": symbol, **proposal}, chain, initial_equity=100000)
                    eval_rec = {
                        "symbol": symbol,
                        "features": features,
                        "decision": decision,
                        "proposal": proposal,
                        "risk_passed": passed,
                        "risk_reasons": reasons,
                    }
                    all_evals.append(eval_rec)
                    if passed and conf > best_confidence:
                        best_confidence = conf
                        best_proposal = proposal
                        best_proposal["symbol"] = symbol
                        best_features = features
                        best_decision = decision
                    else:
                        logger.info(f"{symbol} risk failed: {reasons}")
                else:
                    logger.warning(f"{symbol} build_legs empty: {proposal}")
                    all_evals.append({"symbol": symbol, "decision": decision, "proposal": proposal, "risk_passed": False, "risk_reasons": ["no legs"]})
            else:
                all_evals.append({"symbol": symbol, "features": features, "decision": decision, "proposal": None, "risk_passed": False, "risk_reasons": ["NO_TRADE or low confidence"]})

        except Exception as e:
            logger.error(f"Eval {symbol} failed: {e}", exc_info=True)
            all_evals.append({"symbol": symbol, "error": str(e)})
            continue

        # rate limiting — small delay
        time.sleep(0.4)

    # No trade case
    if not best_proposal:
        logger.info("No valid proposal passed risk gates — standing aside this cycle.")
        log_event("cycle_no_trade", evals=all_evals)
        # still log cycle for dashboard
        cycle_record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "status": "no_trade",
            "account": {"equity": equity, "bp": bp},
            "evals": all_evals,
        }
        _save_cycle(cycle_record)
        print("\n--- CYCLE RESULT: NO_TRADE (all symbols filtered by risk or no edge) ---")
        for ev in all_evals:
            print(f"  {ev.get('symbol')}: {ev.get('decision',{}).get('strategy') if ev.get('decision') else ev.get('error')} conf={ev.get('decision',{}).get('confidence')} risk={ev.get('risk_passed')}")
        return cycle_record

    # We have best proposal — preview + execute
    logger.info(f"\n★ BEST PROPOSAL: {best_decision} => {best_proposal['strategy']} on {best_proposal['symbol']} qty={best_proposal['qty']}")
    log_event("best_proposal", decision=best_decision, proposal=best_proposal, features=best_features)

    preview = executor.preview(best_proposal["legs"], account)
    print("\n" + preview + "\n")

    # Final risk re-check (double gate)
    # need chain for underlying again
    try:
        chain = client.get_option_chain(best_proposal["symbol"])
    except:
        chain = []
    passed, reasons = validate_trade(account, positions, orders, best_proposal, chain, initial_equity=100000)
    if not passed and not dry_run:
        logger.warning(f"Final risk gate FAILED — aborting trade: {reasons}")
        log_event("final_risk_failed", reasons=reasons, proposal=best_proposal)
        cycle_record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "status": "risk_blocked",
            "proposal": best_proposal,
            "decision": best_decision,
            "reasons": reasons,
            "evals": all_evals,
        }
        _save_cycle(cycle_record)
        return cycle_record

    print("Reasons:")
    for r in reasons:
        print(f"  {r}")

    # Submit
    results = executor.submit_legs(best_proposal["legs"], dry_run=dry_run)

    cycle_record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "status": "submitted" if not dry_run else "dry_run",
        "proposal": best_proposal,
        "decision": best_decision,
        "features": best_features,
        "preview": preview,
        "risk_reasons": reasons,
        "order_results": results,
        "account_before": {"equity": equity, "bp": bp},
        "evals": all_evals,
    }
    _save_cycle(cycle_record)

    # Human summary
    print("\n--- CYCLE RESULT ---")
    print(f"Strategy: {best_proposal['strategy']} on {best_proposal['symbol']}")
    print(f"Rationale: {best_decision.get('rationale')}")
    print(f"Source: {best_decision.get('source')} confidence {best_decision.get('confidence')}")
    if dry_run:
        print("DRY RUN — no orders submitted")
    else:
        for r in results:
            print(f"  -> {r.get('symbol')}: {r.get('status') or r.get('error')} id={r.get('id','-')}")

    log_event("cycle_complete", status=cycle_record["status"], symbol=best_proposal["symbol"], strategy=best_proposal["strategy"])
    return cycle_record

def _save_cycle(record: Dict[str, Any]):
    run_dir = PROJECT_ROOT / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    fname = run_dir / f"{ts}-paper-trading.json"
    with open(fname, "w") as f:
        json.dump(record, f, indent=2, default=str)
    # also append to portfolio log
    log_file = PROJECT_ROOT / "logs" / "cycles.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")

def main():
    parser = argparse.ArgumentParser(description="Vega autonomous options alpha agent")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without submitting orders")
    parser.add_argument("--force", action="store_true", help="Run even if market closed")
    parser.add_argument("--symbol", type=str, default=None, help="Filter to single symbol")
    parser.add_argument("--loop", action="store_true", help="Run forever every 15m")
    parser.add_argument("--interval", type=int, default=900, help="Loop interval seconds")
    args = parser.parse_args()

    if args.loop:
        logger.info(f"Loop mode: every {args.interval}s")
        while True:
            try:
                run_cycle(dry_run=args.dry_run, force=args.force, symbol_filter=args.symbol)
            except Exception as e:
                logger.error(f"Loop cycle error: {e}", exc_info=True)
            logger.info(f"Sleeping {args.interval}s...")
            time.sleep(args.interval)
    else:
        run_cycle(dry_run=args.dry_run, force=args.force, symbol_filter=args.symbol)

if __name__ == "__main__":
    main()
