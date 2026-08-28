from datetime import datetime
import pytz

NY = pytz.timezone("America/New_York")

def is_market_open(now: datetime | None = None) -> bool:
    if now is None:
        now = datetime.now(NY)
    else:
        if now.tzinfo is None:
            now = NY.localize(now)
        else:
            now = now.astimezone(NY)
    # weekdays only
    if now.weekday() >= 5:
        return False
    # 09:30 - 16:00 ET
    open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_time = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_time <= now <= close_time

def next_open(now: datetime | None = None) -> datetime:
    if now is None:
        now = datetime.now(NY)
    return now  # placeholder for display
