# Vega — Autonomous Options Alpha Agent

> **Alpaca AI Trading Agents Hackathon — lablab.ai × Alpaca — Aug 28–Sep 4 2026**  
> Track: **Options Alpha Agents** — Fully autonomous, options-required, paper trading, MCP or CLI required.

Vega is an **aggressive** autonomous agent that trades **0–7 DTE options** on `SPY, QQQ, AAPL, NVDA, TSLA, MSFT, META, AMD` via Alpaca's Paper Trading environment. It uses **Ling 3.0 Flash Fin (finance-native 124B MoE, 5.1B active, 256K context, free via Opencode/Vercel AI Gateway/OpenRouter through Sep 25)** → Featherless Llama 3.1 70B → OpenAI → rules to classify regime and select the optimal defined-risk options structure, then enforces 8 deterministic risk gates (incl. fee gate) before executing via **Trading API + MCP + CLI** (both, for max Technology score).

```
Alpaca Data API → Feature Engine (RSI/EMA/BB/MACD/VWAP/IV Rank/vol) → LLM Brain (Ling Fin Flash → Featherless → OpenAI → Rules) → Strategy Selector → Risk Gates → Execution (Trading API + MCP + CLI traces) → Dashboard
```

**Opencode:** Run with Ling via `opencode -m opencode/ling-3.0-flash-fin-free` (see `opencode.jsonc` + `docs/opencode_ling.md`).

---

## Quickstart (3 minutes)

```bash
git clone <this-repo> && cd lablabai
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: add your PAPER keys from https://app.alpaca.markets/paper/dashboard/overview
# APCA_API_KEY_ID=PK...
# APCA_API_SECRET_KEY=...
# APCA_PAPER=true (must stay true)
# LING — pick one (free through Sep 25):
# OPENROUTER_API_KEY=sk-or-v1-... LING_MODEL=inclusionai/ling-3.0-flash-fin:free
# or AI_GATEWAY_API_KEY=... or via `opencode run -m opencode/ling-3.0-flash-fin-free` (no key needed, proxied)
# FEATHERLESS_API_KEY=... (optional secondary, $25 credits) / OPENAI_API_KEY=... (fallback)

# 1) Dry-run cycle — previews without submitting
python -m src.agent --dry-run --force

# 2) Live paper cycle (submits real paper orders, respects market hours)
python -m src.agent --force

# 3) Dashboard
streamlit run src/dashboard/app.py
# open http://localhost:8501

# 4) Autonomous loop
python -m src.agent --loop --interval 900
# or cron: crontab -e → */15 9-16 * * 1-5 /home/harshal/fun/lablabai/src/jobs/cron.sh
```

**Security:** Never commit `.env`. The repo includes `.gitignore` for it. Rotate keys if ever pasted in chat.

---

## Architecture

| Layer | File | Description |
|-------|------|-------------|
| **Data** | `src/data/alpaca_client.py` | `TradingClient(paper=True)` literal, `StockHistoricalDataClient`, balanced chain scoring (≥5 calls+puts, ≥8 near-money) via yfinance fallback + synthetics. Paper guard hard-fails if `APCA_PAPER != true`. |
| **Features** | `src/features/indicators.py` | RSI, EMA20/50/SMA20, ATR%, 20d vol, IV rank, **BB width/pct_B, MACD hist, VWAP dist, volume ratio, S/R** — regime uses BB width <4% + vol >1.5. |
| **News** | `src/data/news.py` | yfinance headlines + Ling Fin Flash sentiment (bullish/bearish/neutral) → boosts confidence +0.07 if aligns. |
| **Greeks** | `src/features/greeks.py` | Black-Scholes delta/gamma/theta/vega (sigma clamped 0.1-0.8) + liquid mid fallback. |
| **LLM Brain** | `src/brain/llm.py` | **Ling 3.0 Flash Fin finance prompt** (256K, FinFIRST) → Featherless → OpenAI → `rules_classifier`. `edge_bps` + `expected_move_pct`. Strict JSON. |
| **Strategy** | `src/strategy/selector.py` | 8 structures: `IRON_CONDOR`, `BULL_PUT_SPREAD`, `BEAR_CALL_SPREAD`, `LONG_STRADDLE`, `LONG_STRANGLE`, `LONG_CALL`, `LONG_PUT`, `NO_TRADE`. Fee-aware sizing, liquid-mid, balanced expiries. |
| **Risk** | `src/risk/gates.py` | **8 gates:** BP, position count, concentration (30%), daily −3% / weekly −6% halt, portfolio delta ≤60, expiry 0–7 DTE, open orders <8, **fee gate (net credit ≥$0.10/share, fees ≤60% gross)**. |
| **Fees** | `src/utils/fees.py` | `$0.15/contract + $0.02 reg + 5bps slip` → net open/r/t P&L, blocks fee-eaten spreads. |
| **Execution** | `src/execution/orders.py` | Preview table + `submit_legs` (limit orders, idempotent `client_order_id`). Manages take-profit +40% / stop −25% via `close_position`. |
| **MCP/CLI** | `src/execution/mcp_trace.py` | `GetDynamicTools` discovery + `place_option_order` + `alpaca order submit` traces to `logs/mcp_trace.jsonl` / `logs/cli_trace.jsonl`. |
| **Agent** | `src/agent.py` | `run_cycle()` — clock check, account/positions, earnings calendar, position management, universe eval, best-confidence pick, double risk gate, submit, log to `runs/`. |
| **Dashboard** | `src/dashboard/app.py` | Streamlit + Plotly: equity curve, regime snapshot, Greeks explorer, cycle history, MCP/CLI trace viewers. |
| **Jobs** | `src/jobs/cron.sh` | Paper-verified wrapper for cron/systemd. |

**Universe:** `SPY, QQQ` for income + `AAPL, NVDA, TSLA, MSFT, META, AMD` for momentum/earnings. Earnings watch dynamically via `yfinance` + static `NVDA/CRM/AVGO` window for Sep 1–4.

---

## MCP & CLI Compliance (Judging: Technology Implementation)

This project **implements both** (requirement is either):

- **MCP Server** — config at `config/mcp.json.example` (`uvx alpaca-mcp-server` with `ALPACA_PAPER_TRADE=true`). Agent calls `GetDynamicTools` → discovers `place_option_order`, `get_account_info`, `get_all_positions`, etc., and logs every call to `logs/mcp_trace.jsonl` per `alpaca-skills/skills/trading-api/paper-trading-mcp/SKILL.md` §4.

- **CLI** — Every order preview shows byte-identical `alpaca order submit --symbol ... --side ... --qty ... --type limit --limit-price ... --time-in-force day --client-order-id ...` and wrapper verifies `alpaca doctor` resolves to `https://paper-api.alpaca.markets` before any submit. Logs to `logs/cli_trace.jsonl` per `paper-trading-cli/SKILL.md` §4.

Both traces are viewable in the Dashboard `Logs (MCP/CLI)` tab for judges.

---

## Risk Gates (for Writeup)

- **Max 20% BP per trade, 85% total** — aggressive but capped.
- **Max 6 positions, 5 contracts/leg, no naked options** — defined-risk only.
- **Stop −25% / Take-profit +40% per position** — auto-closed via `close_position`.
- **Daily halt −3%, Weekly halt −6%** — circuit breaker.
- **Expiry 0–7 DTE, concentration 30%, delta ≤60** — prevents over-exposure.

All gates in `src/risk/gates.py:14` — see `validate_trade()`.

---

## Paper Trading Safety

- `src/config.py:14` raises if `APCA_PAPER != true`.
- `src/data/alpaca_client.py:31` `AlpacaPaperGuard.assert_paper()` before any account/order call, and `TradingClient(..., paper=True)` is a **literal**, not an env var per skill anti-pattern.
- `.env` is gitignored; run folder redacts keys.
- Wrapper `src/jobs/cron.sh` greps `.env` for `APCA_PAPER=true` and aborts otherwise.

---

## Alpaca Skills Used

Installed from https://github.com/alpacahq/alpaca-skills :

- `skills/trading-api/paper-trading/SKILL.md` — generic paper-trading workflow (preview → confirm → submit → monitor → portfolio impact).
- `skills/trading-api/paper-trading-mcp/SKILL.md` — MCP tool discovery, param shape, paper gate via config file single-field extract.
- `skills/trading-api/paper-trading-cli/SKILL.md` — CLI `alpaca doctor` endpoint verification, `--dry-run` preview, `--jq` filtering.

Reference patterns adapted from https://github.com/huygiatrng/AlpacaTradingAgent (`tradingagents/default_config.py:1`, dataflows, risk, graph) for config shape and indicator conventions.

---

## Submission Checklist

- [x] Public GitHub with MIT license
- [x] `src/` autonomous agent + 7 risk gates
- [x] Options structures (iron condor, spreads, straddles, long calls/puts) — defined-risk
- [x] Trading API + MCP + CLI (all three) with log evidence
- [x] Streamlit dashboard + `notebooks/backtest.ipynb`
- [x] `docs/one_pager.md` (AI logic, risk gates, infra implementation)
- [ ] Fresh **$100k** PAPER account ID for judging (replace `PA3G3L5J7O8V` before final submit)
- [ ] Demo video 2–3 min (run `python -m src.agent --dry-run --force` + dashboard)
- [ ] Social posts (tag @lablabai @AlpacaHQ, up to 5 links)

**Create fresh judging account:** https://app.alpaca.markets → Paper Trading → Generate new keys → set **Buying Power $100,000** → copy `ACCOUNT_ID` (like `PAxxxxxxxx`) into submission form. Do not reuse dev account.

---

## Disclosure

> This material is for informational, educational, and research purposes only. Not investment advice, recommendation, offer, or solicitation to buy/sell securities, options, or crypto. All trading involves risk, including possible loss of principal. Paper trading is simulated and may differ from live in fills, market impact, liquidity, fees, latency. Options involve significant risk; you can lose entire premium on longs. Review https://alpaca.markets/disclosures. Paper results do not guarantee future results.

---

## License

MIT — see LICENSE.

Built for Alpaca AI Trading Agents Hackathon — lablab.ai × Alpaca — Aug 28–Sep 4 2026.
