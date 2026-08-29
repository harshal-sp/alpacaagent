"""Vega — autonomous aggressive options alpha agent — main loop (Fireworks AI, parallel)."""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

from src.config import UNIVERSE, RISK_CONFIG, PROJECT_ROOT, APCA_PAPER, APCA_API_KEY_ID
from src.data.alpaca_client import AlpacaClient
from src.data.earnings import get_upcoming_earnings
from src.data.news import get_news_sentiment
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
  ╚═══╝  ╚══════╝ ╚═════╝ ╚═╝  ╚═╝  Fireworks AI • 0-7 DTE • Parallel
"""

def _evaluate_one(symbol: str, client: AlpacaClient, earnings: List[Dict], account: Dict[str, Any], positions: List[Dict], orders: List[Dict], bp: float) -> Dict[str, Any]:
    """Evaluate single symbol — isolated for threading."""
    try:
        bars = client.get_bars(symbol, days=60)
        chain = client.get_option_chain(symbol)
        features = compute_features(symbol, bars, chain)
        try:
            sentiment = get_news_sentiment(symbol)
            features["news_sentiment"] = sentiment.get("sentiment")
            features["news_confidence"] = sentiment.get("confidence")
            features["news_headline"] = sentiment.get("headline", "")[:120]
        except Exception:
            features["news_sentiment"] = "neutral"

        if not chain:
            return {"symbol": symbol, "features": features, "decision": {"strategy": "NO_TRADE", "confidence": 0, "rationale": "no chain"}, "proposal": None, "risk_passed": False, "risk_reasons": ["no option chain"], "score": -1}

        decision = classify(features, earnings)
        try:
            if features.get("news_sentiment") == decision.get("bias") and features.get("news_sentiment") != "neutral":
                decision["confidence"] = min(0.95, decision.get("confidence", 0.5) + 0.07)
                decision["rationale"] = (decision.get("rationale", "") + f" + news {features['news_sentiment']}")[:220]
        except Exception:
            pass

        # Diversification dampener
        existing_underlyings = {p.get("symbol","")[:4] for p in positions}
        if symbol[:3] in {u[:3] for u in existing_underlyings} and len(positions) >= 2:
            decision["confidence"] = max(0.1, decision.get("confidence",0) * 0.85)

        conf = decision.get("confidence", 0)
        edge = decision.get("edge_bps", 0)
        score = conf + edge/10000.0

        if decision.get("strategy") == "NO_TRADE":
            return {"symbol": symbol, "features": features, "decision": decision, "proposal": None, "risk_passed": False, "risk_reasons": ["NO_TRADE"], "score": score, "conf": conf}

        spot = features.get("last", 500)
        proposal = build_legs(decision, spot, chain, bp)
        if not proposal.get("legs"):
            return {"symbol": symbol, "features": features, "decision": decision, "proposal": proposal, "risk_passed": False, "risk_reasons": ["no legs"], "score": score, "conf": conf}

        passed, reasons = validate_trade(account, positions, orders, {"symbol": symbol, **proposal}, chain, initial_equity=100000)
        # stash chain for later double-gate if needed
        return {"symbol": symbol, "features": features, "decision": decision, "proposal": {**proposal, "symbol": symbol}, "risk_passed": passed, "risk_reasons": reasons, "score": score, "conf": conf, "chain": chain}
    except Exception as e:
        logger.error(f"Eval {symbol} failed: {e}", exc_info=True)
        return {"symbol": symbol, "error": str(e), "risk_passed": False, "risk_reasons": [str(e)], "score": -1, "conf": -1}


def run_cycle(dry_run: bool = False, force: bool = False, symbol_filter: str | None = None) -> Dict[str, Any]:
    print(BANNER)
    logger.info(f"Starting Vega cycle dry_run={dry_run} force={force} paper={APCA_PAPER} key={APCA_API_KEY_ID[:6]}...")

    if not APCA_PAPER:
        raise RuntimeError("BLOCKED: Live trading detected — aborting.")

    client = AlpacaClient(paper=True)
    executor = OrderExecutor(client)

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
        account = {"equity": "100000", "buying_power": "400000", "cash": "100000", "options_buying_power": "200000", "status": "ACTIVE", "trading_blocked": False}
        positions = []
        orders = []

    equity = float(account.get("equity", 100000) or 100000)
    bp = float(account.get("buying_power", 400000) or 0)
    logger.info(f"Account equity=${equity:,.2f} BP=${bp:,.2f} positions={len(positions)} open_orders={len(orders)}")

    try:
        earnings = get_upcoming_earnings(UNIVERSE, days_ahead=7)
        logger.info(f"Earnings watch: {earnings}")
    except Exception as e:
        logger.warning(f"earnings fetch failed: {e}")
        earnings = []

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

    universe = [symbol_filter] if symbol_filter else UNIVERSE
    all_evals: List[Dict[str, Any]] = []

    # Parallel evaluation (6-8 workers balances API rate vs speed)
    max_workers = min(8, len(universe))
    logger.info(f"Evaluating {len(universe)} symbols in parallel (workers={max_workers})")
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {pool.submit(_evaluate_one, sym, client, earnings, account, positions, orders, bp): sym for sym in universe}
        for fut in as_completed(future_map):
            sym = future_map[fut]
            try:
                rec = fut.result()
                # strip chain before storing to keep JSON light, but keep proposal
                chain_tmp = rec.pop("chain", None)
                # log per-symbol
                if "decision" in rec:
                    logger.info(f"LLM decision {sym}: {rec.get('decision')}")
                    log_event("symbol_eval", symbol=sym, features=rec.get("features"), decision=rec.get("decision"))
                all_evals.append(rec)
            except Exception as e:
                logger.error(f"Future {sym} crashed: {e}", exc_info=True)
                all_evals.append({"symbol": sym, "error": str(e), "risk_passed": False, "risk_reasons": [str(e)], "score": -1})

    # Deterministic ordering for dashboard/logs
    all_evals.sort(key=lambda x: x.get("symbol", ""))

    # Pick best by score then confidence among risk-passed
    candidates = [ev for ev in all_evals if ev.get("risk_passed") and ev.get("proposal")]
    best = None
    if candidates:
        # highest score, tie-break by confidence
        candidates.sort(key=lambda x: (x.get("score", -1), x.get("conf", -1)), reverse=True)
        best = candidates[0]

    if not best:
        logger.info("No valid proposal passed risk gates — standing aside this cycle.")
        log_event("cycle_no_trade", evals=all_evals)
        cycle_record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "status": "no_trade",
            "account": {"equity": equity, "bp": bp},
            "evals": all_evals,
        }
        _save_cycle(cycle_record)
        print("\n--- CYCLE RESULT: NO_TRADE (all symbols filtered by risk or no edge) ---")
        for ev in all_evals:
            strat = ev.get("decision", {}).get("strategy") if ev.get("decision") else ev.get("error")
            print(f"  {ev.get('symbol')}: {strat} conf={ev.get('decision',{}).get('confidence') if ev.get('decision') else '-'} risk={ev.get('risk_passed')}")
        return cycle_record

    best_proposal = best["proposal"]
    best_decision = best["decision"]
    best_features = best.get("features")
    best_confidence = best.get("conf", 0)

    logger.info(f"\n★ BEST PROPOSAL: {best_decision} => {best_proposal['strategy']} on {best_proposal['symbol']} qty={best_proposal['qty']}")
    log_event("best_proposal", decision=best_decision, proposal=best_proposal, features=best_features)

    preview = executor.preview(best_proposal["legs"], account, best_proposal)
    print("\n" + preview + "\n")

    # Final risk re-check (double gate) — refetch chain for underlying
    try:
        chain = client.get_option_chain(best_proposal["symbol"])
    except Exception:
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
    log_file = PROJECT_ROOT / "logs" / "cycles.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")

def main():
    parser = argparse.ArgumentParser(description="Vega autonomous options alpha agent (Fireworks AI)")
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
