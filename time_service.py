"""Time utility helpers for blind-box plugin."""

import time
from typing import Tuple


def utc8_date_hour() -> Tuple[str, int]:
    ts = time.time() + 8 * 3600
    t = time.gmtime(ts)
    return time.strftime("%Y-%m-%d", t), t.tm_hour
