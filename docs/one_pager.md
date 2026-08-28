# Vega — Autonomous Options Alpha Agent — One-Page Writeup

**Hackathon:** lablab.ai × Alpaca AI Trading Agents Hackathon — Options Alpha Agents — Aug 28–Sep 4 2026  
**Account (judging):** Fresh PAPER account — $100,000 starting balance (dev: PA3G3L5J7O8V, will replace for submission)  
**Repo:** `lablabai` — Python 3.10+, `alpaca-py`, `Featherless AI`, `Streamlit`  
**Team:** Pantherzz — solo aggressive entry

---

## 1. AI Logic

**Pipeline:** `Alpaca Data API → Feature Engine → LLM Brain → Strategy Selector → Risk Gates → Execution`

- **Feature Engine** (`src/features/indicators.py`): Real-time bars (Alpaca Data API, fallback `yfinance`) compute RSI, EMA20/50, ATR%, 20d realized vol, IV rank (from live option chain mid IVs), trend. Example: `SPY 580, RSI 54, ATR 0.9%, IVR 34 → regime_hint=range_high_iv`.

- **LLM Brain** (`src/brain/llm.py`): Primary `Featherless — meta-llama/Meta-Llama-3.1-70B-Instruct` via OpenAI-compatible API (`$25` credits, pay-per-request). System prompt encodes 8 strategies with regime mapping. Output **strict JSON** `{regime, confidence, strategy, rationale, bias}`. Fallback chain: Featherless → `OPENAI gpt-4o-mini` → deterministic `rules_classifier` (identical thresholds) so agent never stalls. All calls logged to `logs/mcp_trace.jsonl` and `logs/vega.jsonl`.

- **Strategy Selector** (`src/strategy/selector.py`): Maps strategy to concrete legs with Greeks via Black-Scholes (`src/features/greeks.py`):
  - `HIGH_IV + sideways` → **Iron Condor** (4 legs, ~15Δ shorts, ~5Δ wings, credit, max loss = width−credit)
  - `HIGH_IV + trend up/down` → **Bull Put Spread / Bear Call Spread** (2 legs, aggressive 18–20% BP sizing, max 5 contracts/leg)
  - `LOW_IV + earnings ≤3d` → **Long Straddle/Strangle** (ATM/OTM call+put, cheap gamma, max loss = premium)
  - `TRENDING + high ATR` → **Long Call/Put** (0.35Δ, momentum, premium-defined)

Aggressive sizing: `qty = min(5, (BP*0.18)//(cost*100))` — targets 15–20% buying power per trade, 85% total cap.

## 2. Risk Gates (Deterministic, Independent of LLM)

All 7 gates in `src/risk/gates.py:validate_trade()` must pass before submission; any failure logs reason and blocks trade:

1. **Buying Power** — `est_cost ≤ options_buying_power` and ≤ `20% BP` per trade (paper `options_buying_power` checked).
2. **Position Count** — max 6 open positions.
3. **Concentration** — per-underlying ≤30% equity.
4. **Daily/Weekly Halt** — halt if `daily ≤−3%` or `total ≤−6%` vs $100k initial (aggressive but bounded).
5. **Greeks** — |portfolio delta| ≤60 (allows directional but caps).
6. **Expiry** — 0–7 DTE only (0DTE allowed for income).
7. **Order Load** — <8 open orders (rate-limit protection).

Position management (`src/execution/orders.py:manage_positions`) runs each cycle: take-profit +40%, stop-loss −25% → `close_position` via Alpaca API. No naked options — all spreads or long premium, max loss known at entry.

8. **Fee Gate** — `src/risk/gates.py:173` + `src/utils/fees.py:1` + `src/config.py:63` ensures profit survives live costs. Every proposal estimates `FEE_CONFIG`: `$0.15/contract` commission + `$0.02` regulatory + `5 bps` slippage. Example: 5× bear call spread (1.26–0.43 = 0.83 credit): gross $415 → open fees $2.10 → net $0.776/share; if net < $0.10/share or fees >60% of gross, trade blocked. Preview `src/execution/orders.py:16` shows `Net open / Net r/t`; close fees reserved. Prevents penny-spread erosion where fees would take all profit.

## 3. Alpaca Infrastructure Implementation

- **Trading API** (`src/data/alpaca_client.py`): `TradingClient(paper=True)` literal (skill §Phase 8 — not env-configurable), `StockHistoricalDataClient`, `OptionHistoricalDataClient` fallback to `yfinance` options chain. Endpoints: `GET /v2/account`, `/v2/positions`, `/v2/orders`, `GET /v2/assets/{symbol}`, `POST /v2/orders` (per-leg limit orders, idempotent `client_order_id=vega-{uuid}`).

- **MCP Server** (`src/execution/mcp_trace.py`): Implements full MCP skill — `GetDynamicTools` discovery (`alpaca-paper-trading` namespace, tools `get_account_info`, `place_option_order`, `get_all_positions`, etc.), traces every tool call to `logs/mcp_trace.jsonl` with namespace + params + result for judging proof. Config example `config/mcp.json.example` sets `ALPACA_PAPER_TRADE=true`.

- **CLI** (same file + `src/jobs/cron.sh`): Every order emits equivalent `alpaca order submit --symbol ... --side ... --qty ... --type limit --limit-price ... --time-in-force day --client-order-id ... --dry-run` preview and real command; `alpaca doctor` paper-endpoint guard in wrapper (`grep paper-api.alpaca.markets`). Logs to `logs/cli_trace.jsonl`.

- **Autonomy:** `src/agent.py:run_cycle()` runs every 15m via `src/jobs/cron.sh` (cron `*/15 9-16 * * 1-5`) or `python -m src.agent --loop`. Handles market-hours check (`GET /v2/clock`), earnings calendar (`yfinance` + static watch), position management, universe evaluation, best-confidence selection, double risk gate, submission, and cycles log (`logs/cycles.jsonl`, `runs/YYYYMMDD-HHMMSS-paper-trading.json`).

- **Dashboard** (`src/dashboard/app.py` — Streamlit + Plotly): Equity curve, IV rank, Greeks explorer (Black-Scholes delta/gamma/theta/vega), cycle history, MCP/CLI trace viewers, one-click dry-run.

**Paper-only proof:** `src/config.py` raises on `APCA_PAPER != true`; `AlpacaPaperGuard.assert_paper()` before any order; wrapper aborts if `.env` not paper. All orders include `Environment: PAPER (verified)` preview.

## 4. Why This Wins (P&L + Tech + Creativity)

For a 7-day window, Vega avoids single-regime fragility: in range-bound high IV it harvests theta (iron condors), into earnings it owns convexity (straddles), in trends it follows momentum with defined-risk spreads — LLM chooses, rules enforce. Aggressive sizing targets +7–10% week with −3% daily stop, backtested illustratively in `notebooks/backtest.ipynb`.

**Reproducibility:** `pip install -r requirements.txt`, `cp .env.example .env` (add paper keys), `python -m src.agent --dry-run --force`, `streamlit run src/dashboard/app.py`.

> **Disclosure:** Paper trading is simulated; fills may differ from live. Not investment advice. Options involve significant risk — you can lose entire premium on longs; spreads limit loss to width−credit. Review https://alpaca.markets/disclosures.

