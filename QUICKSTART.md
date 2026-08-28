# Vega Quickstart — 5 minute judge run

## 1) Install
```bash
git clone https://github.com/<you>/vega-options-alpha-agent && cd vega-options-alpha-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 2) Configure — paper only
```bash
cp .env.example .env
# edit .env with your PAPER keys from https://app.alpaca.markets/paper/dashboard/overview
# IMPORTANT: For final judging, create a FRESH $100,000 paper account (see below)
nano .env
```

**.env must contain:**
```
APCA_API_KEY_ID=PK...
APCA_API_SECRET_KEY=...
APCA_PAPER=true
FEATHERLESS_API_KEY=... # optional, falls back to rules
```

## 3) Verify — dry run
```bash
python -m src.agent --dry-run --force
# Should print ORDER PREVIEW (PAPER) + MCP traces to logs/mcp_trace.jsonl and logs/cli_trace.jsonl
cat logs/mcp_trace.jsonl | head
cat logs/cli_trace.jsonl | head
```

## 4) Live paper cycle (submits real paper orders)
```bash
python -m src.agent --force
# Or continuous:
python -m src.agent --loop --interval 900
```

## 5) Dashboard
```bash
streamlit run src/dashboard/app.py
# http://localhost:8501
# Tabs: Overview (equity, IV rank), Cycles & Trades, Greeks & Strategy, Logs (MCP/CLI), About
```

## 6) Create Fresh $100k Judging Account (Required)
1. https://app.alpaca.markets → Switch to Paper Trading
2. Settings → Create New Paper Account → Starting Balance **$100,000.00**
3. Generate API Key → copy `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` into a new `.env.judging`
4. Run one cycle to prove P&L:
```bash
APCA_API_KEY_ID=... APCA_API_SECRET_KEY=... python -m src.agent --force
```
5. Copy `Account ID` (PA...) from dashboard into lablab submission form.

## 7) Autonomous cron (optional, local)
```bash
chmod +x src/jobs/cron.sh
# Edit cron:
crontab -e
# Add:
# */15 9-16 * * 1-5 /home/harshal/fun/lablabai/src/jobs/cron.sh >> /home/harshal/fun/lablabai/logs/cron.log 2>&1
```

## 8) Tests
```bash
python -m pytest tests/ -v
```

## MCP Setup (for ChatGPT / Claude / Cursor)
```json
// ~/.cursor/mcp.json
{
  "mcpServers": {
    "alpaca-paper-trading": {
      "command": "uvx",
      "args": ["alpaca-mcp-server"],
      "env": {
        "ALPACA_API_KEY": "PAPER_KEY",
        "ALPACA_SECRET_KEY": "PAPER_SECRET",
        "ALPACA_PAPER_TRADE": "true"
      }
    }
  }
}
```
Then in Cursor: `GetDynamicTools pattern "alpaca"` → `place_option_order` etc. Vega already logs these traces for you.

## Troubleshooting
- `403 Forbidden` → insufficient buying power → reduce `max_buying_power_pct_per_trade` in `src/config.py`
- `422 Unprocessable` → check symbol TIF matrix (options `day` only, see `alpaca-skills` docs)
- `401 Unauthorized` → wrong keys or live keys — regenerate paper keys
- `No option chain` → yfinance throttle → wait 30s or uses synthetic fallback automatically
