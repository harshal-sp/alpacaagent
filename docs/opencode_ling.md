# Running Vega with Ling 3.0 Flash Fin via Opencode

Ling 3.0 Flash Fin is InclusionAI's finance-native 124B MoE (5.1B active, 256K context, 1000 tok/s) — tuned on FinFIRST, FinSearchComp, FinanceAgent, APEX-Agents, SpreadsheetBench, τ³-Banking. Free through **Sep 25 2026** via Vercel AI Gateway + OpenRouter (NovitaAI).

Vega uses it as **primary brain** (`src/brain/llm.py:44` `call_ling_fin_flash`) → Featherless → OpenAI → rules.

## Option A — Opencode (recommended, no user key needed)

Opencode proxies free models via `opencode/*`. Vega's `opencode.jsonc` already sets:

```json
{
  "model": "opencode/ling-3.0-flash-fin-free",
  "small_model": "opencode/ling-3.0-flash-fin-free"
}
```

Run the agent *through* Opencode so it inherits the proxied key:

```bash
# From project root (where opencode.jsonc lives)
opencode run -m opencode/ling-3.0-flash-fin-free "Run Vega cycle: python -m src.agent --dry-run --force"
# Or interactive TUI with Ling as default
opencode
# then in TUI: /model opencode/ling-3.0-flash-fin-free  then ask to run agent
```

The trading agent itself will still call OpenRouter; but when launched via `opencode run`, Opencode injects `OPENCODE_API_KEY` so the HTTP call to OpenRouter succeeds even without your own key (falls back to rules if still missing).

## Option B — Direct HTTP (for standalone `python -m src.agent`)

Set one of these in `.env` (any one):

```bash
# OpenRouter free (recommended, get key at https://openrouter.ai/keys → free)
OPENROUTER_API_KEY=sk-or-v1-...
LING_MODEL=inclusionai/ling-3.0-flash-fin:free
LING_BASE_URL=https://openrouter.ai/api/v1

# Or Vercel AI Gateway
AI_GATEWAY_API_KEY=...
LING_MODEL=inclusionai/ling-3.0-flash-fin
LING_BASE_URL=https://ai-gateway.vercel.sh/v1

# Or Ant Ling direct
ANT_LING_API_KEY=...
```

Then:

```bash
source .venv/bin/activate
python -m src.agent --dry-run --force # now source=ling-fin-flash in logs
grep ling-fin-flash logs/vega.jsonl | head
```

**Verify Ling is used:**

```bash
python -m src.agent --dry-run --force --symbol SPY 2>&1 | grep "LLM decision"
# Should show: "source": "ling-fin-flash" (vs "rules" when no key)
```

If no key is set, Vega gracefully falls back to `rules_classifier` (deterministic, same thresholds) — paper P&L still works.

## What Ling Adds vs Generic LLM

- **Finance-native reasoning:** Knows earnings → cheap gamma logic, IV rank + ATR + BB width for squeeze vs trending, fee-aware edge (`edge_bps` field).
- **Long context 256K:** Can ingest 60-day bars + chain + news headlines without truncation (generic 8K would cut).
- **Tool-calling stable:** Trained on 10k+ agentic envs, reliable JSON `{regime, confidence, strategy}` with function calling.

## Make It More Better — v2 Upgrades

Beyond Ling, Vega v2 includes:

- **Enhanced indicators `src/features/indicators.py:1`:** BB width/pct_B, MACD hist, VWAP distance, volume ratio, S/R — regime `range_high_iv` now requires BB width <4% (tight squeeze), `volatile` flagged when volume_ratio >1.5 + IVR>30.
- **News sentiment `src/data/news.py:1`:** yfinance headline + Ling sentiment classifier (bullish/bearish/neutral) → boosts confidence +0.07 if aligns.
- **Portfolio-aware `src/agent.py:110`:** Avoids same underlying as existing positions (diversification), scores by `confidence + edge_bps/10000`.
- **Fee-aware `src/utils/fees.py:1`:** $0.15/contract + $0.02 reg + 5bps slip, 8th risk gate, preview net P&L.
- **Balanced chain scoring `src/data/alpaca_client.py:289`:** Requires ≥5 calls + ≥5 puts per expiry, avoids 0-call 09-08 bug.
- **Dashboard v2 `src/dashboard/app.py:1`:** Fees panel, news, LL trace.

Run `streamlit run src/dashboard/app.py` to see all.
