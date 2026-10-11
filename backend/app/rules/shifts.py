from datetime import datetime, time

from .clock import IST

SHIFT_HOURS = {
    "M": (time(9, 0), time(18, 30)),
    "E": (time(12, 30), time(22, 0)),
}


def is_on_shift(code: str, now: datetime) -> bool:
    """code is a roster code: M, E, OFF or LEAVE."""
    if code not in SHIFT_HOURS:
        return False
    start, end = SHIFT_HOURS[code]
    return start <= now.astimezone(IST).time() < end