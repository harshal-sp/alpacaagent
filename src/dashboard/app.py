"""Vega Dashboard — Streamlit P&L + greeks + trade rationale viewer."""
import json
import os
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import numpy as np
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from src.config import UNIVERSE, UNIVERSE_PRESETS, INDEX_ETFS, MAG_SEVEN, AI_AND_SEMIS, HIGH_BETA_MOMENTUM

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_FILE = PROJECT_ROOT / "logs" / "vega.jsonl"
CYCLES_FILE = PROJECT_ROOT / "logs" / "cycles.jsonl"
RUNS_DIR = PROJECT_ROOT / "runs"

st.set_page_config(page_title="Vega — Options Alpha Agent", layout="wide", initial_sidebar_state="expanded")
st.markdown("# VEGA — Autonomous Options Alpha Agent")
st.caption("Aggressive | Paper-Only | Alpaca Trading API + MCP + CLI | Hackathon Aug 28–Sep 4 2026")

# Sidebar — account snapshot
with st.sidebar:
    st.header("Paper Account")
    try:
        from src.data.alpaca_client import AlpacaClient
        client = AlpacaClient(paper=True)
        acct = client.get_account()
        st.metric("Equity", f"${float(acct.get('equity',0)):,.2f}")
        st.metric("Buying Power", f"${float(acct.get('buying_power',0)):,.2f}")
        st.metric("Cash", f"${float(acct.get('cash',0)):,.2f}")
        st.write(f"Status: {acct.get('status')} | Options Lvl: {acct.get('options_trading_level')}")
        # positions
        positions = client.get_positions()
        st.write(f"Open Positions: {len(positions)}")
        if positions:
            dfp = pd.DataFrame(positions)
            # keep key cols
            cols = [c for c in ["symbol","qty","market_value","unrealized_pl","unrealized_plpc","cost_basis"] if c in dfp.columns]
            st.dataframe(dfp[cols].head(20), use_container_width=True)
        orders = client.get_orders(status="open")
        st.write(f"Open Orders: {len(orders)}")
        if orders:
            dfo = pd.DataFrame(orders)
            cols = [c for c in ["symbol","side","qty","type","limit_price","status","created_at"] if c in dfo.columns]
            st.dataframe(dfo[cols].head(20), use_container_width=True)
        # refresh
        if st.button("Refresh"):
            st.rerun()
    except Exception as e:
        st.warning(f"Live account fetch failed (check .env): {e}")
        st.info("Showing demo data from logs instead")

    st.divider()
    st.header("Agent Controls")
    st.code("python -m src.agent --dry-run --force", language="bash")
    st.code("python -m src.agent --loop --interval 900", language="bash")
    st.caption("Cron: */15 9-16 * * 1-5 src/jobs/cron.sh")

tabs = st.tabs(["Overview", "Cycles & Trades", "Greeks & Strategy", "Logs (MCP/CLI)", "About"])

with tabs[0]:
    col1, col2 = st.columns([2,1])
    with col1:
        st.subheader("Equity Curve (from cycles)")
        # synthesize from cycles or logs
        cycles = []
        if CYCLES_FILE.exists():
            for line in open(CYCLES_FILE):
                try:
                    cycles.append(json.loads(line))
                except: pass
        if cycles:
            # extract equity if present
            rows = []
            for c in cycles:
                ts = c.get("ts")
                eq = c.get("account_before", {}).get("equity") or c.get("account", {}).get("equity")
                if eq and ts:
                    rows.append({"ts": pd.to_datetime(ts), "equity": float(eq)})
            if rows:
                df_eq = pd.DataFrame(rows).sort_values("ts")
                fig = px.line(df_eq, x="ts", y="equity", title="Paper Equity Over Cycles")
                fig.update_layout(height=350)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No equity history yet — run a cycle: `python -m src.agent --dry-run --force`")
                # demo spark
                demo = pd.DataFrame({"ts": pd.date_range(end=datetime.now(timezone.utc), periods=20, freq="2H"), "equity": 100000 + pd.Series(range(20)).cumsum()*12 - 50})
                fig = px.line(demo, x="ts", y="equity", title="Demo Equity (run cycles to populate real)")
                st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No cycles yet. Run: `python -m src.agent --dry-run --force` then refresh.")
            st.image("https://via.placeholder.com/800x300?text=Run+a+cycle+to+see+equity", caption="Placeholder")

        st.subheader("Universe Regime Snapshot")
        try:
            from src.data.alpaca_client import AlpacaClient
            from src.features.indicators import compute_features

            col_u1, col_u2 = st.columns([1, 2])
            with col_u1:
                selected_preset = st.selectbox("Preset View", list(UNIVERSE_PRESETS.keys()), index=0)
            with col_u2:
                default_symbols = UNIVERSE_PRESETS.get(selected_preset, UNIVERSE)
                symbols_to_scan = st.multiselect("Active Symbols", options=UNIVERSE, default=default_symbols[:8])

            client = AlpacaClient(paper=True)
            rows = []
            for sym in (symbols_to_scan or UNIVERSE[:8]):
                try:
                    bars = client.get_bars(sym, days=60)
                    chain = client.get_option_chain(sym)
                    feats = compute_features(sym, bars, chain)
                    rows.append(feats)
                except Exception as e:
                    rows.append({"symbol": sym, "error": str(e)})
            df_reg = pd.DataFrame(rows)
            st.dataframe(df_reg, use_container_width=True)
            if "iv_rank" in df_reg.columns:
                fig2 = px.bar(df_reg, x="symbol", y="iv_rank", color="regime_hint", title="IV Rank by Symbol")
                st.plotly_chart(fig2, use_container_width=True)
        except Exception as e:
            st.warning(f"Regime snapshot failed: {e}")

    with col2:
        st.subheader("Agent Status")
        # last cycle
        if RUNS_DIR.exists():
            runs = sorted(RUNS_DIR.glob("*.json"), reverse=True)
            if runs:
                last = json.loads(open(runs[0]).read())
                st.json({k: v for k, v in last.items() if k not in ("evals", "preview")})
                if last.get("preview"):
                    with st.expander("Last Preview (incl. fees)"):
                        st.text(last["preview"])
            else:
                st.info("No runs yet")
        st.subheader("Fees & Costs Model")
        st.markdown("""
        **Transaction Cost Realism:**
        - **Commission:** `$0.15/contract` per leg
        - **Regulatory:** `$0.02/contract` (OCC/ORF)
        - **Slippage:** `5 bps` modeled on notional
        - **Gate 8 (Fee Gate):** Rejects trades if net credit < `$0.10/share` or fees eat >60% of gross credit.
        - **Idempotency:** Every order tracked with unique `client_order_id`.
        """)
        st.subheader("8-Gate Risk Architecture")
        st.markdown("""
        1. **Buying Power:** Max 20% BP/trade, 85% total account
        2. **Concentration:** Max 30% equity in single ticker
        3. **Position Cap:** Max 6 concurrent open positions
        4. **Circuit Breakers:** Daily −3%, Weekly −6% loss halt
        5. **Greeks Limit:** Net portfolio Delta ≤ 60
        6. **Expiry Window:** 0–7 DTE defined-risk sprint
        7. **Order Throttling:** Max 8 pending orders
        8. **Fee Gate:** Minimum net profit buffer after all friction
        """)

with tabs[1]:
    st.subheader("Cycle History")
    if CYCLES_FILE.exists():
        cycles = [json.loads(l) for l in open(CYCLES_FILE) if l.strip()]
        if cycles:
            dfc = pd.DataFrame([{
                "ts": c.get("ts"),
                "status": c.get("status"),
                "symbol": (c.get("proposal") or {}).get("symbol"),
                "strategy": (c.get("proposal") or {}).get("strategy") or (c.get("decision") or {}).get("strategy"),
                "qty": (c.get("proposal") or {}).get("qty"),
                "source": (c.get("decision") or {}).get("source"),
                "confidence": (c.get("decision") or {}).get("confidence"),
                } for c in cycles])
            st.dataframe(dfc.sort_values("ts", ascending=False), use_container_width=True)
            for c in reversed(cycles[-5:]):
                with st.expander(f"{c.get('ts')} — {c.get('status')} — { (c.get('proposal') or {}).get('strategy','NO_TRADE')}"):
                    st.json(c)
        else:
            st.info("No cycles logged yet")
    else:
        st.info("No cycles.jsonl yet")

    st.subheader("Raw Run Files")
    if RUNS_DIR.exists():
        runs = sorted(RUNS_DIR.glob("*.json"), reverse=True)[:20]
        if runs:
            for r in runs:
                with st.expander(r.name):
                    st.json(json.loads(open(r).read()))
        else:
            st.write("No run files")

with tabs[2]:
    st.subheader("Greeks & Strategy Payoff Visualizer")
    col_g1, col_g2 = st.columns([1, 1])
    with col_g1:
        sym = st.selectbox("Underlying", UNIVERSE, index=0)
        tag = ("📊 Index ETF" if sym in INDEX_ETFS
               else "🎯 Mag 7" if sym in MAG_SEVEN
               else "🤖 AI & Semiconductor" if sym in AI_AND_SEMIS
               else "🚀 High-Beta Momentum" if sym in HIGH_BETA_MOMENTUM
               else "📈 Growth Equity")
        st.caption(f"Category: **{tag}**")
    with col_g2:
        strat_demo = st.selectbox("Payoff Simulation", ["IRON_CONDOR", "BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "LONG_STRADDLE", "LONG_STRANGLE"], index=0)

    try:
        from src.data.alpaca_client import AlpacaClient
        from src.features.greeks import describe_chain_greeks
        client = AlpacaClient(paper=True)
        bars = client.get_bars(sym, days=5)
        spot = float(bars.iloc[-1]["close"]) if not bars.empty else 500.0
        chain = client.get_option_chain(sym)
        enriched = describe_chain_greeks(spot, chain, T_days=2)
        dfg = pd.DataFrame(enriched)

        if not dfg.empty:
            st.metric("Current Spot", f"${spot:.2f}")

            # Plot Payoff Curve for the selected strategy
            s_range = np.linspace(spot * 0.90, spot * 1.10, 100)
            payoff = np.zeros_like(s_range)

            if strat_demo == "IRON_CONDOR":
                # Simulated Condor: Short Put @ 98%, Long Put @ 96%, Short Call @ 102%, Long Call @ 104%, credit = $1.20
                kp1, kp2 = spot * 0.96, spot * 0.98
                kc1, kc2 = spot * 1.02, spot * 1.04
                cr = spot * 0.005
                payoff = np.where(s_range <= kp1, -(kp2 - kp1 - cr) * 100,
                         np.where(s_range < kp2, (s_range - kp2 + cr) * 100,
                         np.where(s_range <= kc1, cr * 100,
                         np.where(s_range < kc2, (kc1 - s_range + cr) * 100, -(kc2 - kc1 - cr) * 100))))
            elif strat_demo == "BULL_PUT_SPREAD":
                kp1, kp2 = spot * 0.96, spot * 0.98
                cr = spot * 0.004
                payoff = np.where(s_range >= kp2, cr * 100,
                         np.where(s_range <= kp1, -(kp2 - kp1 - cr) * 100, (s_range - kp2 + cr) * 100))
            elif strat_demo == "BEAR_CALL_SPREAD":
                kc1, kc2 = spot * 1.02, spot * 1.04
                cr = spot * 0.004
                payoff = np.where(s_range <= kc1, cr * 100,
                         np.where(s_range >= kc2, -(kc2 - kc1 - cr) * 100, (kc1 - s_range + cr) * 100))
            elif strat_demo == "LONG_STRADDLE":
                deb = spot * 0.015
                payoff = (np.abs(s_range - spot) - deb) * 100
            elif strat_demo == "LONG_STRANGLE":
                kp, kc = spot * 0.98, spot * 1.02
                deb = spot * 0.008
                payoff = (np.maximum(0, kp - s_range) + np.maximum(0, s_range - kc) - deb) * 100

            fig_payoff = go.Figure()
            fig_payoff.add_trace(go.Scatter(x=s_range, y=payoff, mode="lines", name=f"{strat_demo} Payoff", line=dict(color="#00D26A" if np.max(payoff) > 0 else "#FF4B4B", width=3)))
            fig_payoff.add_hline(y=0, line_dash="dot", line_color="gray")
            fig_payoff.add_vline(x=spot, line_dash="dash", line_color="#3B82F6", annotation_text=f"Spot ${spot:.2f}")
            fig_payoff.update_layout(title=f"Defined-Risk Expiration Payoff Profile — {strat_demo} on {sym}", xaxis_title="Underlying Price ($)", yaxis_title="Profit / Loss ($)", height=380)
            st.plotly_chart(fig_payoff, use_container_width=True)

            st.dataframe(dfg[["symbol","strike","type","bid","ask","mid","iv","delta","gamma","theta","vega"]].sort_values("strike"), use_container_width=True, height=350)
        else:
            st.info("No chain data available for this symbol")
    except Exception as e:
        st.error(f"Greeks analysis failed: {e}")

with tabs[3]:
    st.subheader("MCP Trace (Judging Evidence)")
    mcp_file = PROJECT_ROOT / "logs" / "mcp_trace.jsonl"
    cli_file = PROJECT_ROOT / "logs" / "cli_trace.jsonl"
    col1, col2 = st.columns(2)
    with col1:
        st.caption("MCP tools: GetDynamicTools → place_option_order → get_account_info → get_all_positions")
        if mcp_file.exists():
            lines = open(mcp_file).read().strip().split("\n")[-50:]
            recs = []
            for l in lines:
                try:
                    recs.append(json.loads(l))
                except: pass
            if recs:
                st.dataframe(pd.DataFrame([{"ts": r.get("ts"), "tool": r.get("tool"), "params": str(r.get("params"))[:120]} for r in recs]), use_container_width=True, height=300)
                with st.expander("Raw MCP trace"):
                    st.code("\n".join(lines[-20:]), language="json")
            else:
                st.info("No MCP traces yet — run a cycle")
        else:
            st.info("No mcp_trace.jsonl yet")
    with col2:
        st.caption("CLI commands: alpaca order submit --dry-run / alpaca doctor / alpaca account get")
        if cli_file.exists():
            lines = open(cli_file).read().strip().split("\n")[-50:]
            recs = []
            for l in lines:
                try:
                    recs.append(json.loads(l))
                except: pass
            if recs:
                st.dataframe(pd.DataFrame([{"ts": r.get("ts"), "command": r.get("command","")[:100], "exit_code": r.get("exit_code")} for r in recs]), use_container_width=True, height=300)
                with st.expander("Raw CLI trace"):
                    st.code("\n".join(lines[-20:]), language="json")
            else:
                st.info("No CLI traces yet")
        else:
            st.info("No cli_trace.jsonl yet")
    st.info("Both traces satisfy hackathon requirement: 'MCP or CLI' — Vega implements BOTH for max Technology score.")

with tabs[4]:
    st.markdown("""
    ### Vega — Autonomous Long-Gamma & Income Agent

    **Thesis:** For a 7-day hackathon window, pure theta or pure gamma underperforms. Vega uses an LLM regime classifier (Featherless Llama 3.1 70B) to pick the *right* options structure per symbol per cycle, then enforces deterministic risk gates before paper execution.

    **Pipeline:** `Alpaca Data API → Feature Engine (RSI/EMA/ATR/IV Rank) → LLM Brain (Featherless/OpenAI/Rules fallback) → Strategy Selector (8 structures) → Risk Gates (7 checks) → Execution (Trading API + MCP + CLI traces) → Dashboard`

    **Options structures (all defined-risk):**
    - Income: Iron Condor, Bull Put Spread, Bear Call Spread
    - Gamma: Long Straddle, Long Strangle
    - Directional: Long Call, Long Put

    **Safety:**
    - Hard paper-only guard (`paper=True` literal, not env-configurable)
    - Max loss per position known before entry (width - credit or premium)
    - Daily -3% halt, weekly -6% halt
    - No naked options, max 6 positions, 5 contracts/leg

    **Disclosures:**
    > Paper trading is simulated and may differ from live trading in fills, market impact, liquidity. Not investment advice. Options involve significant risk; you can lose entire premium on longs. Review https://alpaca.markets/disclosures.

    **Submission:**
    - Public GitHub with MIT license
    - Demo video (3 min) showing autonomous cycle
    - `docs/one_pager.md` with AI logic, risk gates, infra
    - Fresh $100k paper account for judging (not the dev PA3G3L5J7O8V)
    """)
    st.divider()
    st.caption("Built for Alpaca AI Trading Agents Hackathon — lablab.ai × Alpaca — Aug 28–Sep 4 2026")
    if st.button("Run Dry-Run Cycle Now (demo)"):
        try:
            from src.agent import run_cycle
            with st.spinner("Running cycle..."):
                rec = run_cycle(dry_run=True, force=True)
            st.success(f"Cycle {rec.get('status')} — {rec.get('proposal',{}).get('strategy') or 'NO_TRADE'}")
            st.json(rec)
        except Exception as e:
            st.error(f"Cycle failed: {e}")
