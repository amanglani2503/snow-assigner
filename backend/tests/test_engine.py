from collections import Counter
from datetime import datetime, timedelta

from app.agent.assign import eligible_members
from app.agent.engine import tick
from app.roster.generator import days_in_month
from app.roster.models import GROUPS
from app.rules.clock import IST
from app.tickets.repository import Store
from app.tickets.simulate import simulate


def dt(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=IST)


def fresh():
    s = Store()
    s.load_roster("2026-10")                    # no leave by default
    s.clock.set(dt(2026, 10, 12, 10))           # Monday 10:00
    return s


def test_normal_ticket_assigned_to_member_on_shift():
    s = fresh()
    t = simulate(s, "normal", count=1, group="IDAHO")["created"][0]
    now = s.clock.now()
    tick(s, now)
    assert t.status == "ASSIGNED"
    ok = {m.id for m in eligible_members("IDAHO", now, s.members, s.roster)}
    assert t.assigned_to in ok
    assert not t.breached


def test_tick_is_idempotent():
    s = fresh()
    t = simulate(s, "normal", count=1, group="IDAHO")["created"][0]
    tick(s, s.clock.now())
    first = (t.assigned_to, t.assigned_at, dict(s.load()))
    added = tick(s, s.clock.now() + timedelta(minutes=5))
    assert (t.assigned_to, t.assigned_at, dict(s.load())) == first
    assert added == []


def test_burst_spreads_evenly():
    s = fresh()
    simulate(s, "burst", count=20, group="NGA")
    tick(s, s.clock.now())
    counts = Counter(t.assigned_to for t in s.tickets.values())
    assert sum(counts.values()) == 20
    assert None not in counts
    assert len(counts) == 3
    assert max(counts.values()) - min(counts.values()) <= 1


def test_out_of_hours_is_queued_and_assigned_when_the_shift_starts():
    s = fresh()
    t = simulate(s, "out_of_hours", group="EMP")["created"][0]   # Mon 23:15
    assert t.status == "QUEUED" and not t.in_service_hours
    assert t.release_at == dt(2026, 10, 13, 9, 0) and t.deadline == dt(2026, 10, 13, 9, 30)
    tick(s, s.clock.now())
    assert t.status == "QUEUED" and t.assigned_to is None
    tick(s, dt(2026, 10, 13, 9, 0))
    assert t.status == "ASSIGNED" and not t.breached


def test_weekend_ticket_is_queued_until_monday():
    s = fresh()
    t = simulate(s, "weekend", group="DBE")["created"][0]        # Sat 17 Oct 11:00
    assert t.status == "QUEUED" and t.release_at == dt(2026, 10, 19, 9, 0)
    tick(s, dt(2026, 10, 18, 12))                                 # Sunday
    assert t.status == "QUEUED"
    tick(s, dt(2026, 10, 19, 9, 0))
    assert t.status == "ASSIGNED"


def test_edge_2150_assigned_immediately():
    s = fresh()
    t = simulate(s, "edge_2150", group="IDAHO")["created"][0]    # Fri 16 Oct 21:50
    assert t.in_service_hours
    assert t.deadline == dt(2026, 10, 16, 22, 20)
    tick(s, s.clock.now())
    assert t.status == "ASSIGNED" and not t.breached


def test_duplicate_ticket_rejected():
    s = fresh()
    res = simulate(s, "duplicate")
    assert res["duplicates_rejected"] == 1
    assert len(s.tickets) == 1


def test_every_group_covered_all_service_hours():
    s = fresh()
    for d in days_in_month("2026-10"):
        if d.weekday() >= 5:
            continue
        for minutes in range(9 * 60, 22 * 60, 30):
            now = dt(d.year, d.month, d.day, minutes // 60, minutes % 60)
            for g in GROUPS:
                assert eligible_members(g, now, s.members, s.roster), (g, now)