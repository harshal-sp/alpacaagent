# Vega — Autonomous Options Alpha Agent

[![Track](https://img.shields.io/badge/Hackathon-Alpaca%20AI%20Trading%20Agents-blue?style=for-the-badge&logo=alpaca)](https://lablab.ai/event/alpaca-ai-trading-agents-hackathon)
[![Options Alpha](https://img.shields.io/badge/Track-Options%20Alpha%20Agents-9cf?style=for-the-badge)](https://lablab.ai/event/alpaca-ai-trading-agents-hackathon)
[![LLM Brain](https://img.shields.io/badge/LLM-Ling%203.0%20Flash%20Fin%20(124B%20MoE)-purple?style=for-the-badge)](https://openrouter.ai/models/inclusionai/ling-3.0-flash-fin:free)
[![Environment](https://img.shields.io/badge/Trading-Alpaca%20Paper%20Trading%20Only-green?style=for-the-badge)](https://alpaca.markets)
[![Technology](https://img.shields.io/badge/Technology-Trading%20API%20%2B%20MCP%20%2B%20CLI-orange?style=for-the-badge)](#mcp--cli-compliance)
[![License](https://img.shields.io/badge/License-MIT-black?style=for-the-badge)](LICENSE)

> **Alpaca AI Trading Agents Hackathon — lablab.ai × Alpaca — Aug 28–Sep 4 2026**  
> **Track:** *Options Alpha Agents* — Fully autonomous, options-required, paper-only trading, implementing both MCP tool discovery and CLI execution traces.

---

## ⚡ Executive Summary

**Vega** is a high-performance, autonomous options quantitative trading agent designed specifically for short-horizon **0–7 DTE defined-risk structures** on high-liquidity underlyings (`SPY`, `QQQ`, `AAPL`, `NVDA`, `TSLA`, `MSFT`, `META`, `AMD`). 

Vega combines quantitative volatility modeling (**Volatility Risk Premium (VRP)**, **Black-Scholes Greeks**, **Bollinger Squeeze detection**, and **IV Skew**) with a finance-native **124B MoE LLM (Ling 3.0 Flash Fin)**. It systematically selects between **8 defined-risk options strategies**, evaluates proposals through an uncompromising **8-Gate Deterministic Risk Barrier** (including realistic commissions, regulatory fees, and slippage gating), and submits orders via **Alpaca Trading API** with synchronized **MCP Server** and **CLI** audit traces.

```
Alpaca Data API ──► Feature & Greeks Engine ──► Ling Fin Flash LLM Brain ──► Strategy Selector ──► 8 Deterministic Risk Gates ──► Order Execution (API + MCP + CLI) ──► Real-Time Dashboard
```

---

## 📐 System Architecture

### 1. End-to-End Autonomous Trading Pipeline

```mermaid
flowchart TD
    subgraph MarketData ["1. Market Data Layer"]
        A1["Alpaca Stock & Option API"]
        A2["yfinance Fallback Chain"]
        A3["Earnings Calendar & News"]
        A1 --> D1["OHLCV Bars + Option Chain"]
        A2 --> D1
        A3 --> D1
    end

    subgraph FeatureEngine ["2. Quantitative Feature & Greeks Engine"]
        D1 --> F1["Technical Indicators (RSI, ATR, EMA 20/50, BB, MACD, VWAP)"]
        D1 --> F2["Black-Scholes Greeks (Delta, Gamma, Theta, Vega)"]
        D1 --> F3["Volatility Analytics (IV Rank, Skew Ratio, Volatility Risk Premium VRP)"]
        F1 --> F4["Unified Feature State Vector"]
        F2 --> F4
        F3 --> F4
    end

    subgraph DecisionBrain ["3. Multi-Tier Intelligent Decision Brain"]
        F4 --> B1{"Primary: Ling 3.0 Flash Fin\n(124B MoE Finance-Native)"}
        B1 -- "Fail / No Key" --> B2{"Secondary: Featherless\n(Llama 3.1 70B)"}
        B2 -- "Fail" --> B3{"Tertiary: OpenAI\n(GPT-4o-mini)"}
        B3 -- "Fail" --> B4["Deterministic Quantitative Rules Classifier"]
        B1 -- "Success" --> DEC["Trade Proposal & Rationale"]
        B2 -- "Success" --> DEC
        B3 -- "Success" --> DEC
        B4 --> DEC
    end

    subgraph StrategySelection ["4. Dynamic Strategy Construction"]
        DEC --> S1{"Structure Selector"}
        S1 -->|"Range + High IV/VRP"| S_IC["Iron Condor (4 Legs)"]
        S1 -->|"Uptrend + Moderate IV"| S_BP["Bull Put Spread (2 Legs)"]
        S1 -->|"Downtrend + Moderate IV"| S_BC["Bear Call Spread (2 Legs)"]
        S1 -->|"Pre-Earnings Catalyst"| S_ST["Long Straddle (2 Legs)"]
        S1 -->|"Bollinger Squeeze"| S_SG["Long Strangle (2 Legs)"]
        S1 -->|"Breakout Momentum"| S_DIR["Long Call / Long Put (1 Leg)"]
        S1 -->|"No Statistical Edge"| S_NT["NO_TRADE"]
    end

    subgraph RiskEngine ["5. 8-Gate Deterministic Risk Barrier"]
        S_IC & S_BP & S_BC & S_ST & S_SG & S_DIR --> R0["Trade Candidate Payload"]
        R0 --> G1["Gate 1: Buying Power (<20% BP / trade)"]
        G1 --> G2["Gate 2: Concentration (<30% per symbol)"]
        G2 --> G3["Gate 3: Position Cap (<6 concurrent)"]
        G3 --> G4["Gate 4: Drawdown Breaker (Daily -3%, Weekly -6%)"]
        G4 --> G5["Gate 5: Portfolio Greeks (Delta ≤ 60)"]
        G5 --> G6["Gate 6: Expiry Window (0–7 DTE)"]
        G6 --> G7["Gate 7: Order Throttling (<8 open)"]
        G7 --> G8["Gate 8: Fee Gate (Net credit ≥ $0.10/sh, Fees ≤ 60%)"]
    end

    subgraph ExecutionLayer ["6. Triple-Protocol Execution & Tracing"]
        G8 -->|"All 8 Gates Passed"| EX1["Trading API (REST / SDK)\nAtomic MLEG / Buy-First Legs"]
        G8 -->|"All 8 Gates Passed"| EX2["MCP Server Trace\nlogs/mcp_trace.jsonl"]
        G8 -->|"All 8 Gates Passed"| EX3["Alpaca CLI Trace\nlogs/cli_trace.jsonl"]
        G8 -.->|"Any Gate Failed"| BLK["Risk Blocked / Stand Aside"]
    end

    subgraph Monitoring ["7. Observability & Dashboard"]
        EX1 & EX2 & EX3 --> LOG["Run Artifacts & JSONL Logs"]
        LOG --> DB["Streamlit UI (Plotly Payoff Curves, Greeks, Audit Trails)"]
    end

    classDef data fill:#1e293b,stroke:#3b82f6,stroke-width:2px,color:#fff;
    classDef feature fill:#0f172a,stroke:#06b6d4,stroke-width:2px,color:#fff;
    classDef brain fill:#311042,stroke:#a855f7,stroke-width:2px,color:#fff;
    classDef strat fill:#1c1917,stroke:#f59e0b,stroke-width:2px,color:#fff;
    classDef risk fill:#450a0a,stroke:#ef4444,stroke-width:2px,color:#fff;
    classDef exec fill:#064e3b,stroke:#10b981,stroke-width:2px,color:#fff;
    classDef mon fill:#1e1b4b,stroke:#6366f1,stroke-width:2px,color:#fff;

    class A1,A2,A3,D1 data;
    class F1,F2,F3,F4 feature;
    class B1,B2,B3,B4,DEC brain;
    class S1,S_IC,S_BP,S_BC,S_ST,S_SG,S_DIR,S_NT strat;
    class R0,G1,G2,G3,G4,G5,G6,G7,G8,BLK risk;
    class EX1,EX2,EX3 exec;
    class LOG,DB mon;
```

---

### 2. Multi-Tier LLM Fallback Hierarchy

Vega is resilient against API rate limits, network outages, and credential changes through a 4-tier hierarchical brain:

```mermaid
graph TD
    REQ["Symbol Features + Greeks + News + Catalyst Context"] --> LING["Tier 1: Ling 3.0 Flash Fin\n(124B MoE, 5.1B Active, 256K Context)\n• Finance-native reasoning (FinFIRST & FinanceAgent benchmarked)\n• Free via Opencode / OpenRouter / AI Gateway"]
    
    LING -->|"HTTP 200 + Valid JSON"| OUTPUT["Standardized Decision JSON\n{regime, strategy, confidence, rationale, bias, edge_bps}"]
    LING -->|"Timeout / 429 / Auth Error"| FEATH["Tier 2: Featherless.ai\n(Meta-Llama-3.1-70B-Instruct)\n• High reasoning depth fallback"]
    
    FEATH -->|"HTTP 200 + Valid JSON"| OUTPUT
    FEATH -->|"Rate Limited / No Key"| OPENAI["Tier 3: OpenAI Fallback\n(GPT-4o-mini)\n• Structured schema enforcement"]
    
    OPENAI -->|"HTTP 200 + Valid JSON"| OUTPUT
    OPENAI -->|"Unavailable"| RULES["Tier 4: Quantitative Rules Engine\n• Volatility Risk Premium (VRP)\n• Bollinger Bandwidth Squeeze\n• IV Rank & Skew Breakout thresholds"]
    
    RULES --> OUTPUT
```

---

### 3. The 8-Gate Deterministic Risk Barrier

No order reaches Alpaca Paper without strictly satisfying all 8 deterministic checkpoints:

```mermaid
flowchart LR
    P["Proposed Trade"] --> G1{"1. Buying Power\n<20% BP trade\n<85% BP total"}
    G1 -- Pass --> G2{"2. Concentration\n<30% equity in\nsingle ticker"}
    G2 -- Pass --> G3{"3. Concurrency\n<6 active open\npositions"}
    G3 -- Pass --> G4{"4. Circuit Breakers\nDaily loss > -3%\nWeekly loss > -6%"}
    G4 -- Pass --> G5{"5. Portfolio Greeks\nNet Delta ≤ 60\nDefined Gamma"}
    G5 -- Pass --> G6{"6. Expiry Window\n0 to 7 DTE\nDefined-risk"}
    G6 -- Pass --> G7{"7. Rate Throttle\n<8 pending open\norders"}
    G7 -- Pass --> G8{"8. Fee Gate\nNet Credit ≥ $0.10/sh\nFees ≤ 60% Gross"}
    G8 -- Pass --> SUBMIT["✅ Execute Order\n(Trading API + MCP + CLI)"]
    
    G1 -- Fail --> REJ["🚫 Trade Aborted\n(Logged in runs/ & cycles.jsonl)"]
    G2 -- Fail --> REJ
    G3 -- Fail --> REJ
    G4 -- Fail --> REJ
    G5 -- Fail --> REJ
    G6 -- Fail --> REJ
    G7 -- Fail --> REJ
    G8 -- Fail --> REJ
```

---

### 4. Strategy Selection Decision Matrix

```mermaid
graph TD
    ROOT["Market Regime & Volatility Analysis"] --> COND_VRP{"Volatility State"}
    
    COND_VRP -->|"High IVR (>25) OR High VRP (>3.5%)"| VOL_HIGH["Short Premium Domain"]
    COND_VRP -->|"Low IVR (<22) OR BB Squeeze (<3.5%)"| VOL_LOW["Long Gamma Domain"]
    COND_VRP -->|"Strong Trend + High ATR"| VOL_TREND["Directional Momentum Domain"]
    
    VOL_HIGH --> S_SIDE{"Trend Assessment"}
    S_SIDE -->|"Sideways / Range"| STRAT_IC["IRON CONDOR\n• Sell ~15Δ Put, Buy ~5Δ Put\n• Sell ~15Δ Call, Buy ~5Δ Call\n• Target: Maximum Theta Decay"]
    S_SIDE -->|"Uptrend Bias"| STRAT_BPS["BULL PUT SPREAD\n• Sell ~20Δ Put, Buy ~7Δ Put\n• Target: Bullish Theta Inflow"]
    S_SIDE -->|"Downtrend Bias"| STRAT_BCS["BEAR CALL SPREAD\n• Sell ~20Δ Call, Buy ~7Δ Call\n• Target: Bearish Theta Inflow"]
    
    VOL_LOW --> S_EVENT{"Catalyst State"}
    S_EVENT -->|"Earnings in ≤3 Days"| STRAT_STRAD["LONG STRADDLE\n• Buy ATM Call + Buy ATM Put\n• Target: Pre-Earnings Vol Runup"]
    S_EVENT -->|"No Imminent Event"| STRAT_STRANG["LONG STRANGLE\n• Buy 25Δ OTM Call + Buy -25Δ OTM Put\n• Target: Breakout from Squeeze"]
    
    VOL_TREND --> S_DIR{"Directional Tilt"}
    S_DIR -->|"Bullish Breakout"| STRAT_LC["LONG CALL (35Δ)\n• Defined-risk Upside Acceleration"]
    S_DIR -->|"Bearish Breakdown"| STRAT_LP["LONG PUT (-35Δ)\n• Defined-risk Downside Acceleration"]
```

---

### 5. Execution & Trace Lifecycle (API + MCP + CLI)

```mermaid
sequenceDiagram
    autonumber
    participant Agent as Vega Autonomous Loop
    participant Risk as 8-Gate Risk Engine
    participant API as Alpaca Trading API
    participant MCP as MCP Trace System
    participant CLI as Alpaca CLI Wrapper
    participant Dash as Streamlit Dashboard

    Agent->>Agent: Evaluate Universe (SPY, QQQ, AAPL, NVDA, TSLA, MSFT, META, AMD)
    Agent->>Risk: validate_trade(account, positions, orders, proposal)
    Risk-->>Agent: Passed (all 8 gates verified)
    Agent->>Agent: Generate Idempotent client_order_id (e.g. vega-mleg-4f89a1c2)
    
    par Dual Trace & Execution Logging
        Agent->>MCP: trace_mcp_tool("place_option_order", payload)
        MCP->>MCP: Write to logs/mcp_trace.jsonl
    and
        Agent->>CLI: trace_cli("alpaca order submit --symbol ... --limit-price ...")
        CLI->>CLI: Write to logs/cli_trace.jsonl
    and
        Agent->>API: submit_legs(mleg_payload / sequential_covered_legs)
        API-->>Agent: Order Accepted & Filled in Paper Account
    end
    
    Agent->>Agent: Record cycle state to runs/ & logs/cycles.jsonl
    Dash->>Agent: Stream metrics, equity curve & live Greeks
```

---

## 📊 Quantitative Mechanics & Financial Edge

### 1. Volatility Risk Premium (VRP) & Skew
Vega quantifies the structural mispricing between implied and realized volatility:
$$\text{VRP} = \text{IV}_{\text{annualized}} - \text{RV}_{20\text{d annualized}}$$
$$\text{Skew Ratio} = \frac{\text{IV}_{\text{OTM Put (25}\Delta\text{)}}}{\text{IV}_{\text{OTM Call (25}\Delta\text{)}}}$$
- **$\text{VRP} > +3.5\%$:** Markets are overpaying for tail risk $\rightarrow$ Edge in selling defined-risk credit spreads & iron condors.
- **$\text{VRP} < +1.0\%$ with Bollinger Squeeze ($BB_{\text{width}} < 3.5\%$):** Implied volatility is underpriced $\rightarrow$ Edge in buying long gamma (straddles/strangles).

### 2. Black-Scholes Greeks Engine
Every strike in the universe is mapped in real-time with continuous yield Black-Scholes pricing:
$$\Delta = e^{-q T} N(d_1) \quad (\text{Call}), \quad \Delta = e^{-q T} (N(d_1) - 1) \quad (\text{Put})$$
$$\Gamma = \frac{e^{-q T} \phi(d_1)}{S \sigma \sqrt{T}}, \quad \Theta = -\frac{S e^{-q T} \phi(d_1) \sigma}{2 \sqrt{T}} - r K e^{-r T} N(d_2), \quad \nu = \frac{S e^{-q T} \phi(d_1) \sqrt{T}}{100}$$

### 3. Realistic Friction & Fee Model
Paper trading accounts typically model $0 commission and perfect mid fills. Vega bridges the paper-to-live performance gap by calculating transaction drag before every entry:
$$\text{Fees}_{\text{Total}} = 2 \times \left( \sum_{\text{legs}} (\text{Comm} + \text{Reg}) \times \text{Qty} + \text{Notional} \times 0.0005 \right)$$
- **Commission:** $\$0.15$ / contract / leg
- **Regulatory (OCC / ORF):** $\$0.02$ / contract
- **Modeled Slippage:** $5\text{ bps}$ ($0.05\%$) on gross notional
- **Fee Gate:** If $\text{Net Credit} < \$0.10/\text{share}$ or $\text{Fees} > 60\%$ of gross credit, the trade is rejected.

---

## 🛠 Supported Options Strategies

| Strategy | Legs & Structure | Delta Wing Target | Market Condition | Max Profit | Max Loss |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Iron Condor** | 4 legs: Short Put Spread + Short Call Spread | $15\Delta$ Short / $5\Delta$ Long | High IV, Sideways ($VRP > 3.5\%$) | Net Credit Collected | Wing Width $-$ Net Credit |
| **Bull Put Spread** | 2 legs: Sell OTM Put + Buy Lower Put | $20\Delta$ Short / $7\Delta$ Long | Uptrend, Moderate/High IV | Net Credit Collected | Strike Width $-$ Net Credit |
| **Bear Call Spread** | 2 legs: Sell OTM Call + Buy Higher Call | $20\Delta$ Short / $7\Delta$ Long | Downtrend, Moderate/High IV | Net Credit Collected | Strike Width $-$ Net Credit |
| **Long Straddle** | 2 legs: Buy ATM Call + Buy ATM Put | $50\Delta$ Call / $-50\Delta$ Put | Low IV, Earnings Catalyst $\le 3\text{d}$ | Unlimited | Total Debit Paid |
| **Long Strangle** | 2 legs: Buy OTM Call + Buy OTM Put | $25\Delta$ Call / $-25\Delta$ Put | Bollinger Squeeze, Low IV | Unlimited | Total Debit Paid |
| **Long Call** | 1 leg: Buy Directional OTM Call | $35\Delta$ Call | Strong Bullish Breakout | Unlimited | Total Debit Paid |
| **Long Put** | 1 leg: Buy Directional OTM Put | $-35\Delta$ Put | Strong Bearish Breakdown | Strike $-$ Premium | Total Debit Paid |

---

## 🏆 MCP & CLI Compliance (Hackathon Technology Requirements)

Vega implements **both** the Model Context Protocol (MCP) and Alpaca CLI execution pathways for maximum Technology scoring.

### 1. Model Context Protocol (MCP) Server
- **Configuration:** `config/mcp.json.example` (`uvx alpaca-mcp-server` with `ALPACA_PAPER_TRADE=true`).
- **Dynamic Tool Discovery:** Agent executes `GetDynamicTools` at initialization to discover tools (`place_option_order`, `get_account_info`, `get_all_positions`, `close_position`, etc.).
- **Audit Logging:** Every tool call and response is recorded in `logs/mcp_trace.jsonl` matching the Alpaca MCP Skills specification.

### 2. Alpaca CLI Integration
- **Zero-Drift Command Generation:** Every preview produces byte-identical CLI execution commands:
  ```bash
  alpaca order submit --symbol NVDA260904C00125000 --side buy --qty 2 --type limit --limit-price 3.45 --time-in-force day --client-order-id vega-9a8b7c6d --dry-run
  ```
- **Endpoint Verification:** Verifies `alpaca doctor` points to `https://paper-api.alpaca.markets`.
- **Audit Logging:** All CLI invocations recorded in `logs/cli_trace.jsonl`.

---

## 🚀 Quickstart Guide

### 1. Clone & Setup Environment

```bash
# Clone the repository
git clone <your-repo-url> && cd lablabai

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Credentials

Copy the environment template:
```bash
cp .env.example .env
```

Edit `.env` with your Alpaca **PAPER** keys (ensure `APCA_PAPER=true`):
```ini
# Alpaca Paper Keys (from https://app.alpaca.markets/paper/dashboard/overview)
APCA_API_KEY_ID=PK********************
APCA_API_SECRET_KEY=****************************************
APCA_PAPER=true

# Ling 3.0 Flash Fin LLM (Free through Sep 25 2026)
OPENROUTER_API_KEY=sk-or-v1-****************************************
LING_MODEL=inclusionai/ling-3.0-flash-fin:free

# Optional Fallback LLMs
FEATHERLESS_API_KEY=
OPENAI_API_KEY=
```

### 3. Run Autonomous Cycle

```bash
# Dry-run cycle: evaluates universe, calculates Greeks, runs risk gates, previews orders without submitting
.venv/bin/python -m src.agent --dry-run --force

# Live paper cycle: evaluates market, enforces 8 risk gates, and submits paper orders
.venv/bin/python -m src.agent --force

# Run specific single ticker evaluation
.venv/bin/python -m src.agent --dry-run --force --symbol NVDA

# Autonomous 15-minute scheduled loop
.venv/bin/python -m src.agent --loop --interval 900
```

### 4. Launch Real-Time Dashboard

```bash
.venv/bin/streamlit run src/dashboard/app.py
```
Open **`http://localhost:8501`** in your browser to view:
- **Overview:** Paper account metrics, equity curve, universe regime snapshot with VRP & IV skew.
- **Cycles & Trades:** Full historical log of all cycles and decisions.
- **Greeks & Payoff Visualizer:** Interactive Plotly payoff profiles and live chain Greeks explorer.
- **Logs (MCP/CLI):** Live evidence viewer for hackathon judges showing MCP and CLI traces.

---

## 🛡 Paper Trading Safety & Hard Guards

- **Hard-Coded Literal:** `AlpacaClient(paper=True)` enforces `paper=True` as a code literal, not an easily misconfigured environment toggle.
- **`AlpacaPaperGuard`:** Validates `APCA_PAPER=true` before every account, position, and order operation.
- **Automated P&L Exits:** `OrderExecutor.manage_positions` automatically triggers take-profit at `+40%` and stop-loss at `-25%`.
- **Zero Naked Exposure:** All strategies enforce defined-risk wings.

---

## 📋 Hackathon Submission Checklist

- [x] **Public GitHub Repo** with MIT License
- [x] **Autonomous Agent Implementation** (`src/agent.py`) with 8 deterministic risk gates
- [x] **Options Alpha Track Requirements:** Defined-risk options structures (Iron Condor, Spreads, Straddles, Strangles, Directional)
- [x] **Technology Score (Both Implemented):** Alpaca Trading API + MCP Server + CLI Traces
- [x] **LLM Intelligence:** Finance-native Ling 3.0 Flash Fin MoE + Multi-tier fallbacks
- [x] **Observability:** Streamlit Dashboard with Plotly strategy payoff diagrams and live Greeks
- [x] **Documentation:** `README.md`, `docs/one_pager.md`, `docs/opencode_ling.md`
- [ ] **Judging Account:** Fresh $100k paper account generated for final judging review
- [ ] **Demo Video:** 2–3 min walkthrough of autonomous dry-run cycle & dashboard

---

## ⚖️ Disclosure & Disclaimer

> This repository is for **informational, educational, and research purposes only**. It is not investment advice, a recommendation, offer, or solicitation to buy or sell securities, options, or other financial instruments. Trading options involves substantial risk of loss and is not suitable for every investor. Paper trading simulates order execution and may not reflect real-market slippage, liquidity constraints, or execution latency. Please review [Alpaca's Disclosures](https://alpaca.markets/disclosures) before trading.

---

## 📄 License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for more information.

*Built with precision for the Alpaca AI Trading Agents Hackathon — lablab.ai × Alpaca — Aug 28–Sep 4 2026.*
