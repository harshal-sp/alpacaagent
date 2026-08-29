"""Central config for Vega — aggressive options alpha agent."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# --- Alpaca ---
APCA_API_KEY_ID = os.getenv("APCA_API_KEY_ID", "")
APCA_API_SECRET_KEY = os.getenv("APCA_API_SECRET_KEY", "")
APCA_PAPER = os.getenv("APCA_PAPER", "true").lower() in ("1", "true", "yes")
ALPACA_PAPER_URL = "https://paper-api.alpaca.markets"
ALPACA_DATA_URL = "https://data.alpaca.markets"

# Safety: hard fail if paper is false
if not APCA_PAPER:
    raise RuntimeError("APCA_PAPER must be true — live trading is blocked in this skill.")

# --- LLM — Ling 3.0 Flash Fin (primary finance-native) → Featherless → OpenAI → Rules ---
# Ling 3.0 Flash Fin via Opencode/Vercel AI Gateway/OpenRouter (free through Sep 25 2026)
LING_API_KEY = os.getenv("LING_API_KEY", "") or os.getenv("OPENROUTER_API_KEY", "") or os.getenv("AI_GATEWAY_API_KEY", "")
LING_BASE_URL = os.getenv("LING_BASE_URL", "https://openrouter.ai/api/v1")  # or https://ai-gateway.vercel.sh/v1 or https://api.opencode.ai/v1
LING_MODEL = os.getenv("LING_MODEL", "inclusionai/ling-3.0-flash-fin:free")  # free alias for inclusionai/ling-3.0-flash-fin
# Opencode direct (uses opencode's proxy, no user key needed when running via `opencode run -m opencode/ling-3.0-flash-fin-free`)
OPENCODE_LING_MODEL = os.getenv("OPENCODE_LING_MODEL", "opencode/ling-3.0-flash-fin-free")
FEATHERLESS_API_KEY = os.getenv("FEATHERLESS_API_KEY", "")
FEATHERLESS_BASE_URL = os.getenv("FEATHERLESS_BASE_URL", "https://api.featherless.ai/v1")
FEATHERLESS_MODEL = os.getenv("FEATHERLESS_MODEL", "meta-llama/Meta-Llama-3.1-70B-Instruct")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
# Vercel AI Gateway alternative
AI_GATEWAY_API_KEY = os.getenv("AI_GATEWAY_API_KEY", "")
AI_GATEWAY_BASE_URL = os.getenv("AI_GATEWAY_BASE_URL", "https://ai-gateway.vercel.sh/v1")

# --- Universe (High-Performing Stocks & Liquid Options Presets) ---
INDEX_ETFS = ["SPY", "QQQ", "IWM", "SMH"]
MAG_SEVEN = ["AAPL", "NVDA", "MSFT", "AMZN", "GOOGL", "META", "TSLA"]
AI_AND_SEMIS = ["NVDA", "AMD", "AVGO", "TSM", "MU", "ARM"]
HIGH_BETA_MOMENTUM = ["PLTR", "COIN", "NFLX", "UBER", "CRM"]

UNIVERSE_PRESETS = {
    "CORE": ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "MSFT", "META", "AMD", "AMZN", "GOOGL", "PLTR", "AVGO", "COIN", "NFLX", "IWM", "SMH"],
    "MAG_SEVEN": MAG_SEVEN,
    "INDEX_ETFS": INDEX_ETFS,
    "AI_AND_SEMIS": AI_AND_SEMIS,
    "HIGH_BETA_MOMENTUM": HIGH_BETA_MOMENTUM,
    "TECH": ["QQQ", "AAPL", "NVDA", "MSFT", "AMZN", "GOOGL", "META", "AMD", "PLTR", "AVGO", "TSM", "MU", "ARM", "SMH"],
    "ALL": list(dict.fromkeys(INDEX_ETFS + MAG_SEVEN + AI_AND_SEMIS + HIGH_BETA_MOMENTUM + ["DELL", "HPQ"])),
}

def parse_universe(env_val: str | None = None) -> list:
    val = (env_val if env_val is not None else os.getenv("TRADING_UNIVERSE", "")).strip()
    if not val:
        return UNIVERSE_PRESETS["CORE"]
    val_upper = val.upper()
    if val_upper in UNIVERSE_PRESETS:
        return UNIVERSE_PRESETS[val_upper]
    symbols = [s.strip().upper() for s in val.split(",") if s.strip()]
    return symbols if symbols else UNIVERSE_PRESETS["CORE"]

UNIVERSE = parse_universe()

# Earnings calendar for high-beta / catalyst watchlist
EARNINGS_WATCH = ["NVDA", "CRM", "DELL", "HPQ", "AVGO", "PLTR", "AMD", "COIN", "MU", "ARM", "TSM"]

# --- Risk — aggressive profile ---
RISK_CONFIG = {
    "max_buying_power_pct_per_trade": 20.0,  # aggressive: 20% per entry
    "max_total_buying_power_pct": 85.0,
    "max_positions": 6,
    "max_contracts_per_leg": 5,
    "stop_loss_pct_per_position": 25.0,
    "take_profit_pct_per_position": 40.0,
    "daily_loss_halt_pct": 3.0,
    "weekly_loss_halt_pct": 6.0,
    "max_concentration_pct": 30.0,
    "max_portfolio_delta": 60,  # aggressive allows more directional delta
    "min_days_to_expiry": 0,  # allow 0DTE for income
    "max_days_to_expiry": 7,
}

# --- Strategy thresholds ---
STRATEGY_CONFIG = {
    "iv_rank_high_threshold": 30,  # above -> short premium favored
    "iv_rank_low_threshold": 15,   # below -> long gamma cheap
    "atr_volatility_threshold": 1.5,  # ATR vs SMA
    "rsi_overbought": 70,
    "rsi_oversold": 30,
    "earnings_days_ahead": 3,  # consider straddles within 3 days of earnings
    "income_target_premium_pct": 0.8,  # target 0.8% premium on condor
}

# --- Fees & Transaction Costs (paper vs live gap) ---
# Alpaca Paper: $0 commission (simulated fill). Live: ~$0.15-$0.65/contract + ~$0.02 regulatory.
# We use $0.15 paper-sim to ensure paper P&L would survive live costs. Tune via env if needed.
FEE_CONFIG = {
    "options_commission_per_contract": float(os.getenv("OPTIONS_FEE_PER_CONTRACT", "0.15")),
    "regulatory_per_contract": float(os.getenv("OPTIONS_REG_FEE", "0.02")),
    "stock_commission_per_share": float(os.getenv("STOCK_FEE_PER_SHARE", "0.0")),
    "slippage_bps": float(os.getenv("SLIPPAGE_BPS", "5")),  # 5 bps = 0.05% slip on fill
    "min_net_credit_per_share": 0.10,  # Reject credit spreads where net after fees < $0.10/share
    "min_net_debit_edge_pct": 5.0,
}

# --- Execution ---
EXECUTION_CONFIG = {
    "poll_interval_seconds": 15,
    "order_timeout_seconds": 60,
    "max_slippage_pct": 1.5,
    "use_limit_orders": True,
    "confirmation_mode": os.getenv("CONFIRMATION_MODE", "off"),  # aggressive: off
}

# --- Scheduling ---
SCHEDULE_CONFIG = {
    "cycle_interval_minutes": 15,
    "market_open": "09:30",
    "market_close": "16:00",
    "timezone": "America/New_York",
}

# --- Paths ---
LOG_DIR = PROJECT_ROOT / "logs"
RUNS_DIR = PROJECT_ROOT / "runs"
DATA_CACHE_DIR = PROJECT_ROOT / "data_cache"
for p in [LOG_DIR, RUNS_DIR, DATA_CACHE_DIR]:
    p.mkdir(parents=True, exist_ok=True)
