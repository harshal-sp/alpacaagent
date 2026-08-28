import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from rich.logging import RichHandler

from src.config import LOG_DIR

LOG_FILE = LOG_DIR / "vega.jsonl"

def get_logger(name: str = "vega") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = RichHandler(rich_tracebacks=True, show_time=True)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    # also file handler
    fh = logging.FileHandler(LOG_DIR / "vega.log")
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    logger.addHandler(fh)
    return logger

logger = get_logger()

def log_event(event: str, **kwargs):
    """Structured JSONL log for audit + MCP/CLI traces."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **kwargs,
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")
    logger.info(f"[{event}] {json.dumps(kwargs, default=str)}")
