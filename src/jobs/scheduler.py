"""Local scheduler helper for hackathon demo."""
# Example crontab entries (install via `crontab -e`):
# ALPACA_PAPER=true is already verified inside cron.sh — do not bypass wrapper
# Every 15m during market hours Mon-Fri 09:30-16:00 ET (14:30-21:00 IST) — run wrapper
# */15 9-16 * * 1-5 /home/harshal/fun/lablabai/src/jobs/cron.sh
# Or using IST cron (server in IST): 0,15,30,45 19-23 * * 1-5  ... and 0 0-1 * * 2-6 for overlap
#
# For demo without cron: python -m src.agent --loop --interval 900 --dry-run
CRON_EXAMPLE = """
# Vega — Alpaca Paper Trading (aggressive)
# Add to crontab -e :
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
ALPACA_PAPER=true
# Run every 15 minutes Mon-Fri during NY market hours
*/15 9-16 * * 1-5 /home/harshal/fun/lablabai/src/jobs/cron.sh >> /home/harshal/fun/lablabai/logs/cron.log 2>&1
# Alternative IST server:
# */15 19-23 * * 1-5 /home/harshal/fun/lablabai/src/jobs/cron.sh
# 0,15,30,45 0-1 * * 2-6 /home/harshal/fun/lablabai/src/jobs/cron.sh
"""
