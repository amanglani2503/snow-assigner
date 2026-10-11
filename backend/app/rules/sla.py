from datetime import datetime, time, timedelta

from .clock import IST

SERVICE_START = time(9, 0)
SERVICE_END = time(22, 0)
SLA_MINUTES = 30


def _at(d, t) -> datetime:
    return datetime.combine(d, t, tzinfo=IST)


def is_working_day(dt: datetime) -> bool:
    return dt.astimezone(IST).weekday() < 5


def is_serviceable(arrival: datetime) -> bool:
    t = arrival.astimezone(IST)
    return is_working_day(t) and SERVICE_START <= t.time() < SERVICE_END


def next_service_start(dt: datetime) -> datetime:
    """Earliest moment >= dt when the SLA clock is running."""
    t = dt.astimezone(IST)
    if is_serviceable(t):
        return t
    if is_working_day(t) and t.time() < SERVICE_START:
        return _at(t.date(), SERVICE_START)
    d = t.date() + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return _at(d, SERVICE_START)


def sla_deadline(arrival: datetime) -> datetime:
    """Arrival + 30 working minutes. The clock pauses outside 09:00-22:00 and weekends."""
    start = next_service_start(arrival)
    remaining = timedelta(minutes=SLA_MINUTES)
    end_today = _at(start.date(), SERVICE_END)
    if start + remaining <= end_today:
        return start + remaining
    remaining -= end_today - start
    return next_service_start(end_today) + remaining