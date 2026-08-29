#!/usr/bin/env bash
# Vega VPS Automated Fast-Setup Script
# Compatible with Ubuntu 22.04 / 24.04, Debian 11/12
set -euo pipefail

echo "=========================================================="
echo "   🚀 VEGA OPTIONS ALPHA AGENT — FAST VPS SETUP"
echo "=========================================================="

# 1. Install System Prerequisites
echo "[1/5] Installing system packages (python3, pip, venv, git, tmux)..."
if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-pip python3-venv git tmux curl
elif command -v yum &>/dev/null; then
    sudo yum install -y python3 python3-pip git tmux curl
fi

# 2. Virtual Environment
echo "[2/5] Creating Python virtual environment..."
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate

# 3. Install Requirements
echo "[3/5] Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

# 4. Environment Configuration
echo "[4/5] Checking configuration (.env)..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "⚠️  Created default .env file from .env.example."
    echo "👉 Please edit .env with your Alpaca Paper Keys:"
    echo "   nano .env"
else
    echo "✓ Existing .env file found."
fi

# 5. Verification Dry-Run
echo "[5/5] Testing autonomous agent dry-run..."
.venv/bin/python -m src.agent --dry-run --force || true

echo "=========================================================="
echo "   ✅ FAST SETUP COMPLETE!"
echo "=========================================================="
echo ""
echo "Quick Commands:"
echo "1. Run 1-cycle test:     .venv/bin/python -m src.agent --force"
echo "2. Run 15-min loop:      .venv/bin/python -m src.agent --loop --interval 900"
echo "3. Start Dashboard:      .venv/bin/streamlit run src/dashboard/app.py --server.port 8501 --server.address 0.0.0.0"
echo "4. Run in tmux session:  tmux new -s vega '.venv/bin/python -m src.agent --loop --interval 900'"
echo ""
