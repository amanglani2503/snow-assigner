from datetime import datetime

import pytest

from app.agent.assign import eligible_members
from app.agent.engine import AssignmentError, assign_to_member, tick
from app.rules.clock import IST
from app.tickets.repository import Store
from app.tickets.simulate import simulate


def dt(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=IST)


def fresh():
    s = Store()
    s.load_roster("2026-10")
    s.clock.set(dt(2026, 10, 12, 10))   # Monday 10:00
    return s


def one(s, scenario="normal", group="IDAHO"):
    return simulate(s, scenario, count=1, group=group)["created"][0]


def test_member_from_other_group_rejected():
    s = fresh()
    t = one(s)
    with pytest.raises(AssignmentError, match="not in group"):
        assign_to_member(s, t, "nga-1", s.clock.now(), "test", actor="agent")
    assert t.status == "PENDING"


def test_member_off_shift_rejected():
    s = fresh()
    t = one(s)
    now = s.clock.now()
    ok = {m.id for m in eligible_members("IDAHO", now, s.members, s.roster)}
    off = [m for m in s.members if m.group == "IDAHO" and m.id not in ok]
    assert off, "expected one IDAHO member to be off shift at Mon 10:00"
    with pytest.raises(AssignmentError, match="not on shift"):
        assign_to_member(s, t, off[0].id, now, "test", actor="agent")


def test_already_assigned_rejected():
    s = fresh()
    t = one(s)
    now = s.clock.now()
    tick(s, now)
    with pytest.raises(AssignmentError, match="already assigned"):
        assign_to_member(s, t, t.assigned_to, now, "again", actor="agent")


def test_ignored_ticket_rejected():
    s = fresh()
    t = one(s, "out_of_hours", "EMP")
    with pytest.raises(AssignmentError, match="outside service hours"):
        assign_to_member(s, t, "emp-1", s.clock.now(), "test", actor="agent")


def test_valid_agent_assignment_is_logged_with_actor():
    s = fresh()
    t = one(s)
    now = s.clock.now()
    pick = eligible_members("IDAHO", now, s.members, s.roster)[0]
    assign_to_member(s, t, pick.id, now, "Agent chose lightest load", actor="agent")
    assert t.status == "ASSIGNED" and t.assigned_to == pick.id
    assert s.audit[-1]["actor"] == "agent"


def test_tick_skips_in_flight_tickets():
    s = fresh()
    t = one(s)
    s.in_flight.add(t.sys_id)
    tick(s, s.clock.now())
    assert t.status == "PENDING"
    s.in_flight.discard(t.sys_id)
    tick(s, s.clock.now())
    assert t.status == "ASSIGNED"



def test_agent_must_pick_least_loaded():
    s = fresh()
    now = s.clock.now()
    on = eligible_members("IDAHO", now, s.members, s.roster)
    assert len(on) >= 2
    a, b = one(s), one(s)
    assign_to_member(s, a, on[0].id, now, "first", actor="agent")
    with pytest.raises(AssignmentError, match="not the least loaded"):
        assign_to_member(s, b, on[0].id, now, "again", actor="agent")
    assert b.status == "PENDING"
    assign_to_member(s, b, on[1].id, now, "other", actor="agent")
    assert b.assigned_to == on[1].id


def test_rules_actor_not_restricted_by_balance():
    s = fresh()
    now = s.clock.now()
    on = eligible_members("IDAHO", now, s.members, s.roster)
    a, b = one(s), one(s)
    assign_to_member(s, a, on[0].id, now, "first", actor="rules")
    assign_to_member(s, b, on[0].id, now, "manual lead choice", actor="rules")
    assert b.assigned_to == on[0].id