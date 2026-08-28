# Vega — Autonomous Options Alpha Agent — Slide Deck

---

## Slide 1 — Title
**VEGA — Autonomous Options Alpha Agent**  
*Code the next generation of algorithmic trading*  
Alpaca AI Trading Agents Hackathon — lablab.ai × Alpaca — Aug 28–Sep 4 2026  
$100k Paper Trading • 0–7 DTE Options • MCP + CLI • Featherless Llama 3.1 70B

---

## Slide 2 — Problem: 7 Days to P&L
- P&L judged on **fresh $100k paper account** over 5 trading days.
- Must use **options** (not just equities) and **MCP or CLI**.
- Pure income (iron condors) dies in trending markets; pure gamma (straddles) bleeds theta.
- **Vega thesis:** Hybrid regime agent — *right structure for right regime* — wins any week.

---

## Slide 3 — Architecture (5 Layers)
```
Alpaca Data API
  ↓
Feature Engine (RSI, EMA20/50, ATR%, Realized Vol, IV Rank from live chain)
  ↓
LLM Brain (Featherless Llama 3.1 70B → OpenAI gpt-4o-mini → Rules fallback)
  ↓
Strategy Selector (8 defined-risk structures, Black-Scholes greeks)
  ↓
Risk Gates (7 deterministic checks)
  ↓
Execution (Trading API + MCP + CLI traces) → Dashboard + Logs
```

Autonomous every 15m via `cron` / `python -m src.agent --loop`.

---

## Slide 4 — LLM Brain
- **Primary:** Featherless `meta-llama/Meta-Llama-3.1-70B-Instruct` ($25 credits, OpenAI-compatible, `FEATHERLESS_API_KEY` in `.env`).
- **Prompt:** System encodes 8 strategies with regime mapping, strict JSON output `{regime, confidence, strategy, rationale, bias}`.
- **Fallback chain:** Featherless → OpenAI → `rules_classifier` (identical thresholds) — never stalls.
- **Evidence:** Every call logged to `logs/mcp_trace.jsonl` + `logs/vega.jsonl` for video.

---

## Slide 5 — Strategy Selector (All Defined-Risk)
| Regime | Structure | Legs | Max Loss |
|--------|-----------|------|----------|
| High IV + sideways | **Iron Condor** | 4 (put spread + call spread) | width − credit |
| High IV + uptrend | **Bull Put Spread** | 2 | width − credit |
| High IV + downtrend | **Bear Call Spread** | 2 | width − credit |
| Low IV + earnings ≤3d | **Long Straddle** | 2 (ATM call+put) | premium |
| Low IV + compression | **Long Strangle** | 2 (OTM) | premium |
| Momentum | **Long Call / Put** | 1 | premium |

Sizing: `qty = min(5, (BP×0.18)//(cost×100))` — aggressive 15–20% BP/trade, max 5 contracts/leg.

---

## Slide 6 — Risk Gates (7, Deterministic)
1. **Buying Power** ≤ options_buying_power & ≤20% BP/trade
2. **Position Count** <6
3. **Concentration** ≤30% equity per underlying
4. **Daily Halt** −3%, **Weekly Halt** −6% (vs $100k)
5. **Portfolio Delta** ≤60
6. **Expiry** 0–7 DTE only
7. **Order Load** <8 open orders
+ Take-profit +40% / Stop-loss −25% auto-close via `close_position`.
No naked options. Ever.

---

## Slide 7 — Alpaca Infra (Why Tech Score Max)
- **Trading API:** `TradingClient(paper=True)` literal (not env var), `GET /v2/account`, `/v2/positions`, `/v2/orders`, `POST /v2/orders` with idempotent `client_order_id=vega-{uuid}`.
- **MCP Server:** `GetDynamicTools` discovery (`alpaca-paper-trading` → `place_option_order`, `get_account_info`, `get_all_positions`, …) + trace to `logs/mcp_trace.jsonl`.
- **CLI:** Every order shows `alpaca order submit --symbol … --dry-run` preview + `alpaca doctor` paper-endpoint guard; logged to `logs/cli_trace.jsonl`.
- **Paper-Only Proof:** `APCA_PAPER=true` literal + `AlpacaPaperGuard.assert_paper()` before any submit + wrapper `grep paper-api.alpaca.markets`.

---

## Slide 8 — Demo Flow (60 sec)
1. `python -m src.agent --dry-run --force` → prints ORDER PREVIEW (PAPER) + CLI equivalent
2. `logs/mcp_trace.jsonl` shows `place_option_order` JSON
3. `streamlit run src/dashboard/app.py` → Overview (equity curve), Greeks explorer, MCP/CLI tab
4. Live: `python -m src.agent --force` → real paper orders → Alpaca Dashboard shows positions & P&L

---

## Slide 9 — P&L Strategy for 5 Trading Days
- Aggressive sizing targets **+7–10% week** with −3% daily stop.
- Backtest notebook (`notebooks/backtest.ipynb`) simulates 60-day SPY/QQQ with synthetic BS pricing.
- Real forward test: 1 dry-run cycle picks best-confidence symbol (e.g., SPY bear call spread, QQQ strangle) respecting risk — illustrative but deterministic.
- Social Build-in-Public: 5 posts tagging @lablabai @AlpacaHQ (use `docs/social_posts.md` template).

---

## Slide 10 — Submission Pack
- [x] Public GitHub (MIT) — `README.md`, `QUICKSTART.md`, `.env.example`
- [x] `docs/one_pager.md` (AI logic, risk gates, infra)
- [x] `docs/slides.md` (this deck → export PDF)
- [x] `src/dashboard/app.py` + `notebooks/backtest.ipynb`
- [ ] Fresh **$100k PAPER `PA...` account ID** (replace dev PA3G3L5J7O8V)
- [ ] Demo video 2–3 min (Loom: 30s intro, 60s dry-run, 60s dashboard, 30s risk)
- [ ] Cover image 16:9 (export from dashboard screenshot)

---

## Slide 11 — Safety & Disclosures
> Paper trading is simulated; fills may differ from live (market impact, liquidity, fees, latency). Not investment advice. Options involve significant risk — you can lose entire premium on longs; spreads limit loss to width−credit. Review https://alpaca.markets/disclosures. Past paper performance ≠ future.

---

## Slide 12 — Team & Next Steps
**Pantherzz — Solo — Aggressive**  
Next: deploy to fresh judging account Sep 1–4, enable Featherless $25 credits, schedule cron, record video, submit `PA...` + GitHub + demo URL.

*Thank you — Vega trades, you watch.*
