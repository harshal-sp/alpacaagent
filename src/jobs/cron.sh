#!/bin/bash
# Vega autonomous cron wrapper — paper-only guard
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJECT_DIR"

# Paper guard — literal check per alpaca-skills § Phase 8
if ! grep -q 'APCA_PAPER=true' .env 2>/dev/null && ! grep -q 'APCA_PAPER="true"' .env 2>/dev/null; then
  echo "ERROR: APCA_PAPER must be true in .env — aborting live trade prevention" >&2
  exit 1
fi

# Ensure paper API is reachable
if ! python3 -c "from src.config import APCA_PAPER; assert APCA_PAPER, 'not paper'" 2>/dev/null; then
  echo "ERROR: Paper guard failed — APCA_PAPER not true in config" >&2
  exit 1
fi

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Vega cycle start — paper verified"
# Run single cycle — respects market hours internally
python3 -m src.agent "$@" >> logs/cron.log 2>&1
EXIT_CODE=$?
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Vega cycle exit=$EXIT_CODE"
exit $EXIT_CODE
