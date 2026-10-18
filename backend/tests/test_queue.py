import asyncio
from collections import Counter
from datetime import datetime

import pytest

from app.agent.dispatcher import dispatch
from app.agent.engine import AssignmentError, assign_to_member, tick
from app.rules.clock import IST
from app.tickets.repository import Store
from app.tickets.simulate import simulate


def dt(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=IST)


def fresh():
    s = Store()
    s.load_roster("2026-10")
    s.clock.set(dt(2026, 10, 12, 10))      # Monday 10:00
    return s


def events(s, name):
    return [a for a in s.audit if a["event"] == name]


# ---------- when does the service window next open? ----------

def test_next_service_start_cases():
    s = fresh()
    assert s.next_service_start(dt(2026, 10, 12, 10)) == dt(2026, 10, 12, 10)       # open now
    assert s.next_service_start(dt(2026, 10, 12, 7, 40)) == dt(2026, 10, 12, 9)     # before opening
    assert s.next_service_start(dt(2026, 10, 12, 22, 0)) == dt(2026, 10, 13, 9)     # exactly at close
    assert s.next_service_start(dt(2026, 10, 12, 23, 15)) == dt(2026, 10, 13, 9)    # evening
    assert s.next_service_start(dt(2026, 10, 16, 23, 0)) == dt(2026, 10, 19, 9)     # Friday night -> Monday
    assert s.next_service_start(dt(2026, 10, 17, 11)) == dt(2026, 10, 19, 9)        # Saturday
    assert s.next_service_start(dt(2026, 10, 18, 11)) == dt(2026, 10, 19, 9)        # Sunday


def test_next_service_start_skips_company_holidays():
    s = fresh()
    for m in s.members:
        s.roster[m.id]["2026-10-13"] = "CH"      # Tuesday is a company-wide holiday
    assert s.next_service_start(dt(2026, 10, 12, 23, 15)) == dt(2026, 10, 14, 9)


# ---------- queued tickets ----------

def test_out_of_hours_ticket_is_queued_with_sla_clock_starting_at_opening():
    s = fresh()
    t = simulate(s, "out_of_hours", group="EMP")["created"][0]      # Monday 23:15
    assert t.status == "QUEUED" and not t.in_service_hours
    assert t.release_at == dt(2026, 10, 13, 9) and t.deadline == dt(2026, 10, 13, 9, 30)
    assert "outside 09:00-22:00" in t.reason
    assert len(events(s, "QUEUED")) == 1


def test_queue_reasons_for_weekend_and_holiday():
    s = fresh()
    sat = simulate(s, "weekend", group="EMP")["created"][0]
    assert "weekend" in sat.reason and sat.release_at == dt(2026, 10, 19, 9)
    for m in s.members:
        s.roster[m.id]["2026-10-13"] = "CH"
    s.clock.set(dt(2026, 10, 13, 11))
    hol = simulate(s, "normal", count=1, group="EMP")["created"][0]
    assert "company holiday" in hol.reason and hol.release_at == dt(2026, 10, 14, 9)


def test_queued_ticket_waits_then_is_released_and_assigned_when_shift_starts():
    s = fresh()
    t = simulate(s, "out_of_hours", group="IDAHO")["created"][0]
    tick(s, dt(2026, 10, 13, 8, 59))
    assert t.status == "QUEUED" and t.assigned_to is None
    tick(s, dt(2026, 10, 13, 9, 0))
    assert t.status == "ASSIGNED" and not t.breached
    assert t.assigned_to in {m.id for m in s.members if m.group == "IDAHO"}
    kinds = [a["event"] for a in s.audit if a["ticket"] == t.number]
    assert kinds == ["QUEUED", "RELEASED", "ASSIGNED"]


def test_queued_ticket_cannot_be_assigned_before_release():
    s = fresh()
    t = simulate(s, "out_of_hours", group="IDAHO")["created"][0]
    with pytest.raises(AssignmentError, match="queued until"):
        assign_to_member(s, t, "idaho-1", dt(2026, 10, 13, 8, 0), "early", actor="agent")


def test_late_release_counts_as_sla_breach():
    s = fresh()
    t = simulate(s, "out_of_hours", group="IDAHO")["created"][0]     # due Tue 09:30
    tick(s, dt(2026, 10, 13, 9, 45))
    assert t.status == "ASSIGNED" and t.breached
    assert len(events(s, "BREACHED")) == 1


def test_weekend_queue_is_spread_evenly_on_monday_morning():
    s = fresh()
    simulate(s, "weekend", count=6, group="NGA")                      # Saturday 11:00
    assert all(t.status == "QUEUED" for t in s.tickets.values())
    tick(s, dt(2026, 10, 19, 9, 0))
    counts = Counter(t.assigned_to for t in s.tickets.values())
    assert sum(counts.values()) == 6 and None not in counts
    assert max(counts.values()) - min(counts.values()) <= 1
    assert all(not t.breached for t in s.tickets.values())


def test_in_hours_tickets_are_unaffected():
    s = fresh()
    t = simulate(s, "normal", count=1, group="IDAHO")["created"][0]
    assert t.status == "PENDING" and t.release_at is None
    tick(s)
    assert t.status == "ASSIGNED"


def test_agent_mode_only_sees_tickets_after_release():
    s = fresh()
    s.mode = "agent"
    t = simulate(s, "out_of_hours", group="EMP")["created"][0]
    calls = []

    async def spy(ticket):
        calls.append(ticket.number)

    asyncio.run(dispatch(s, runner_fn=spy))
    assert calls == [] and t.status == "QUEUED"
    s.clock.set(dt(2026, 10, 13, 9, 0))

    async def go():
        tasks = await dispatch(s, runner_fn=spy)
        await asyncio.gather(*tasks)

    asyncio.run(go())
    assert calls == [t.number]                  # only now is the agent asked
    assert len(events(s, "RELEASED")) == 1