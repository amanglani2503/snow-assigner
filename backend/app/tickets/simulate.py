import random
from datetime import datetime, time, timedelta

from app.roster.models import GROUPS
from app.rules.clock import IST

from .generator import build_ticket

SCENARIOS = ["normal", "out_of_hours", "weekend", "edge_2150", "burst", "holiday", "duplicate"]


def next_day_at(store, now, hour, minute, weekday=None, skip_holidays=True):
    """First day on/after today at hour:minute. weekday=None means any Mon-Fri.
    Company-wide holidays are skipped unless skip_holidays is False."""
    d = now.astimezone(IST).date()
    for _ in range(120):
        when = datetime.combine(d, time(hour, minute), tzinfo=IST)
        right_day = d.weekday() < 5 if weekday is None else d.weekday() == weekday
        if right_day and not (skip_holidays and store.is_org_holiday(when)):
            return when
        d += timedelta(days=1)
    raise ValueError("No suitable day found in the next 120 days")


def next_company_holiday(store, now, hour=11, minute=0):
    d = now.astimezone(IST).date()
    for _ in range(62):
        when = datetime.combine(d, time(hour, minute), tzinfo=IST)
        if d.weekday() < 5 and store.is_org_holiday(when):
            return when
        d += timedelta(days=1)
    raise ValueError("No company holiday found in the next 60 days. "
                     "Generate a roster with company holidays first.")


def simulate(store, scenario="normal", count=None, group=None, seed=None):
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario}")
    rng = random.Random(seed)
    now = store.clock.now()

    target = {
        "out_of_hours": lambda: next_day_at(store, now, 23, 15),
        "weekend": lambda: next_day_at(store, now, 11, 0, weekday=5, skip_holidays=False),
        "edge_2150": lambda: next_day_at(store, now, 21, 50, weekday=4),
        "burst": lambda: next_day_at(store, now, 14, 0),
        "holiday": lambda: next_company_holiday(store, now),
    }.get(scenario)
    if target:
        store.clock.set(target())
        now = store.clock.now()

    store.ensure_roster(now)

    if scenario == "burst":
        group = group or "NGA"

    n = count or {"normal": 3, "burst": 20}.get(scenario, 1)
    created, rejected = [], 0

    def add(t):
        nonlocal rejected
        if store.add_ticket(t):
            created.append(t)
            if t.status == "QUEUED":
                store.log(now, t, "QUEUED", t.reason)
        else:
            rejected += 1

    if scenario == "duplicate":
        t = build_ticket(store, group or rng.choice(GROUPS), now, rng)
        add(t)
        add(t)
    else:
        for _ in range(n):
            add(build_ticket(store, group or rng.choice(GROUPS), now, rng))

    return {"scenario": scenario, "created": created,
            "duplicates_rejected": rejected, "clock": now}