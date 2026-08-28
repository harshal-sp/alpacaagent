# Build in Public — Social Posts (up to 5)

> Copy & adapt, post on X + LinkedIn, tag @lablabai and @AlpacaHQ for Social Engagement prize ($500 ×2 + Algo Trader Plus).

---

### Post 1 — Kickoff (Aug 28)
Building Vega — an autonomous options alpha agent for @AlpacaHQ × @lablabai hackathon. 7 days, $100k paper, 0–7 DTE options only. Stack: Alpaca Trading API + MCP + CLI, Featherless Llama 3.1 70B, 7 risk gates. Aggressive mode: 20% BP/trade, defined-risk only. Repo: <link> #AlpacaHackathon #AI #Trading

### Post 2 — Architecture (Aug 29)
Vega's brain: Alpaca Data API → RSI/EMA/ATR/IV Rank → LLM regime classifier → 8 strategies (iron condor / put spreads / straddles) → Black-Scholes greeks → deterministic risk gates → paper execution. Both MCP `place_option_order` and CLI `alpaca order submit` logged for judging. @AlpacaHQ @lablabai

### Post 3 — First Paper Trade (Aug 30)
First paper fill on PA... — Vega picked QQQ long strangle (IVR 12 low + 0DTE compression) for cheap gamma, SPY bear call spread for theta. All paper, no real money. Dashboard live: <link>. 15m autonomous cron, take-profit +40% / stop −25%. @AlpacaHQ @lablabai

### Post 4 — Risk Gates & Safety (Sep 1)
No naked options. Ever. Vega enforces 7 deterministic gates before any order: BP, concentration ≤30%, daily halt −3%, delta ≤60, 0–7 DTE, + paper-only literal `paper=True` (not env). Max loss known at entry = width−credit or premium. Full writeup: docs/one_pager.md @AlpacaHQ @lablabai

### Post 5 — Final P&L (Sep 4)
Final P&L on fresh $100k paper PA...: <$value> after 7 days, <N> trades, win rate X%, max DD Y%. Full equity curve + MCP/CLI traces in repo + demo video <link>. Built with @AlpacaHQ Trading API, MCP server, CLI. Thanks @lablabai for the platform — Vega out. 🚀

---

**Tips for engagement:**
- Attach screenshots: order preview table, dashboard equity curve, MCP trace JSON
- Short video/GIF of `python -m src.agent --dry-run --force` running
- Thread replies answering questions about options greeks / risk
