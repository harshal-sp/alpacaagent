# Vega — Autonomous Options Alpha Agent — One-Page Writeup

**Hackathon:** lablab.ai × Alpaca AI Trading Agents Hackathon — Options Alpha Agents — Aug 28–Sep 4 2026  
**Account (judging):** Fresh PAPER account — $100,000 starting balance  
**Repo:** `lablabai` — Python 3.10+, `alpaca-py`, `Ling 3.0 Flash Fin`, `Streamlit`, `Plotly`  
**Team:** Pantherzz — Options Alpha Entry

---

## 1. AI Logic & Financial Engineering

**Pipeline:** `Alpaca Data API → Feature Engine (VRP, Skew, Squeeze) → Ling Fin Flash 124B MoE → Strategy Selector → 8 Risk Gates → Execution (API + MCP + CLI) → Dashboard`

- **Feature Engine** (`src/features/indicators.py`): Real-time bars (Alpaca Data API, fallback `yfinance`) compute RSI, EMA20/50, ATR%, 20d realized vol, IV rank, **Volatility Risk Premium (VRP = IV − RV)**, **IV Skew (Put/Call ratio)**, and **Bollinger Squeeze**. Example: `SPY 580, RSI 54, ATR 0.9%, IVR 34, VRP +7.0% → regime_hint=range_high_iv`.

- **LLM Brain** (`src/brain/llm.py`): Primary **Ling 3.0 Flash Fin (124B MoE, 5.1B active, 256K context)** finance-tuned model. System prompt encodes 8 strategies with regime mapping and VRP edges. Output is **strict JSON** `{regime, confidence, strategy, rationale, bias, edge_bps}` with robust regex sanitization. Fallback hierarchy: Ling Fin Flash → `Featherless Llama 3.1 70B` → `OpenAI GPT-4o-mini` → deterministic quantitative rules classifier.

- **Strategy Selector** (`src/strategy/selector.py`): Maps strategy to concrete legs with Net Greeks computed via Black-Scholes (`src/features/greeks.py`):
  - `HIGH_IV / High VRP + sideways` → **Iron Condor** (4 legs, ~15Δ shorts, ~5Δ wings, credit, max loss = width−credit)
  - `HIGH_IV + trend up/down` → **Bull Put Spread / Bear Call Spread** (2 legs, dynamic Delta-aware sizing, max 5 contracts/leg)
  - `LOW_IV + earnings ≤3d` → **Long Straddle** (ATM call+put, cheap gamma, max loss = premium)
  - `Bollinger Squeeze / Low IV` → **Long Strangle** (OTM call+put, explosive breakout setup)
  - `TRENDING + high ATR` → **Long Call/Put** (0.35Δ, momentum continuation)

## 2. 8-Gate Deterministic Risk Barrier (Independent of LLM)

All 8 gates in `src/risk/gates.py:validate_trade()` must pass before submission; any failure logs reasons and stands aside:

1. **Gate 1: Buying Power** — `est_cost ≤ options_buying_power` and ≤ `20% BP` per trade.
2. **Gate 2: Concentration** — per-underlying ≤30% total portfolio equity.
3. **Gate 3: Position Count** — max 6 concurrent open positions.
4. **Gate 4: Drawdown Breakers** — automatic halt if `daily ≤−3%` or `total ≤−6%` vs $100k initial.
5. **Gate 5: Portfolio Greeks** — Net portfolio Delta ≤ 60 and defined Gamma boundaries.
6. **Gate 6: Expiry Window** — 0–7 DTE defined-risk sprint only.
7. **Gate 7: Order Throttling** — <8 pending open orders.
8. **Gate 8: Fee Gate** — `src/utils/fees.py`: ensures profit survives realistic friction ($0.15/contract commission + $0.02 regulatory + 5 bps slippage). Rejects trades if net credit < $0.10/share or fees >60% of gross credit.

Position management (`src/execution/orders.py:manage_positions`) runs each cycle: take-profit +40%, stop-loss −25% → `close_position` via Alpaca API.

## 3. Alpaca Infrastructure Implementation

- **Trading API** (`src/data/alpaca_client.py`): `TradingClient(paper=True)` literal guard, `StockHistoricalDataClient`, `OptionHistoricalDataClient` with fallback to scored option chains. Atomic `mleg` submission with automatic fallback to covered sequential legs.

- **MCP Server** (`src/execution/mcp_trace.py`): Full MCP protocol implementation with `GetDynamicTools` discovery (`alpaca-paper-trading` namespace), tracing every tool call to `logs/mcp_trace.jsonl` with namespace + params + result.

- **CLI** (`src/execution/mcp_trace.py` + `src/jobs/cron.sh`): Every order preview emits byte-identical `alpaca order submit --symbol ... --side ... --qty ... --type limit --limit-price ... --time-in-force day --client-order-id ... --dry-run` commands. Logs to `logs/cli_trace.jsonl`.

- **Autonomy & Dashboard:** Runs 15-minute loop via `src/jobs/cron.sh` or `python -m src.agent --loop`. Observability via Streamlit (`src/dashboard/app.py`) featuring interactive Plotly payoff profiles, live Greeks surfaces, and real-time trace log viewers.

## 4. Why This Wins

For a 7-day hackathon window, Vega balances income harvesting (Iron Condors / Credit Spreads) with explosive gamma convexity (Straddles into earnings / Strangles on squeezes) — governed by deterministic math and risk barriers.

> **Disclosure:** Paper trading is simulated. Not investment advice. Review https://alpaca.markets/disclosures.


