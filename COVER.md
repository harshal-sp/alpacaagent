# Cover Image Brief — Vega

**Generate/Export:** 1600×900 (16:9), dark trading dashboard style

**Layout:**
- Background: deep navy #0B1220 with subtle grid, faint SPY candlesticks
- Left 60%: Large text
  - Headline: **VEGA** (bold, 120px, white) + subtitle *Autonomous Options Alpha Agent* (24px, cyan #22D3EE)
  - Badge: `Alpaca Paper Trading • 0–7 DTE • MCP + CLI • Featherless 70B` (pill, border)
  - Bottom: `lablab.ai × Alpaca — Aug 28–Sep 4 2026`
- Right 40%: Preview card
  - Mock order preview table (as in `src/execution/orders.py:preview()`)
  - Mini equity curve (up-right, green)
  - Small logos: Alpaca, lablab.ai, Featherless
- Corner watermark: `PAPER TRADING ONLY — NOT INVESTMENT ADVICE` (8px, muted)

**Quick generate:** Screenshot dashboard `http://localhost:8501` (Overview tab) + overlay title in Figma/Canva — or run `python scripts/generate_cover.py` (if added).

**Save as:** `cover.png` / `cover.jpg` at repo root for lablab submission.
