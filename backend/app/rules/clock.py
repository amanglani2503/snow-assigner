from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class Clock:
    """Simulated clock. Real time plus an adjustable offset (time travel)."""

    def __init__(self):
        self._offset = timedelta(0)

    def now(self) -> datetime:
        return (datetime.now(IST) + self._offset).replace(microsecond=0)

    def set(self, target: datetime) -> None:
        if target.tzinfo is None:
            target = target.replace(tzinfo=IST)
        self._offset = target - datetime.now(IST)

    def reset(self) -> None:
        self._offset = timedelta(0)