import asyncio
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.agent.assign import eligible_members
from app.agent.dispatcher import dispatch
from app.agent.engine import (
    AssignmentError, assign_to_member, mark_member_unavailable, restore_member, tick,
)
from app.main import app
from app.rules.clock import IST
from app.state import store as app_store
from app.tickets.repository import Store
from app.tickets.simulate import simulate


def dt(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=IST)


def fresh():
    s = Store()
    s.load_roster("2026-10")
    s.clock.set(dt(2026, 10, 12, 10))      # Monday 10:00
    return s


def tickets(s, n, group="IDAHO"):
    return simulate(s, "normal", count=n, group=group)["created"]


def on_shift(s, group="IDAHO"):
    return eligible_members(group, s.clock.now(), s.members, s.roster)


def events(s, name):
    return [a for a in s.audit if a["event"] == name]


def test_open_tickets_move_to_another_member_on_shift():
    s = fresh()
    ts = tickets(s, 4)
    tick(s)
    on = on_shift(s)
    assert len(on) == 2
    leaver, other = on
    mine = [t for t in ts if t.assigned_to == leaver.id]
    assert mine

    res = mark_member_unavailable(s, leaver.id, s.clock.now(), "family emergency")
    assert sorted(res["tickets_queued"]) == sorted(t.number for t in mine)
    assert res["group_covered"] is True
    assert all(t.status == "PENDING" and t.reassign_pending and t.assigned_to is None for t in mine)

    tick(s)
    for t in mine:
        assert t.status == "ASSIGNED" and t.assigned_to == other.id
        assert t.previous_assignees == [leaver.id] and not t.reassign_pending
        assert t.reason.startswith(f"Reassigned from {leaver.name}")
    assert s.load().get(leaver.id, 0) == 0
    assert {"MEMBER_LEFT", "REASSIGN_QUEUED", "REASSIGNED"} <= {a["event"] for a in s.audit}


def test_departed_member_gets_no_new_tickets():
    s = fresh()
    leaver, other = on_shift(s)
    mark_member_unavailable(s, leaver.id, s.clock.now())
    assert leaver.id not in {m.id for m in on_shift(s)}
    new = tickets(s, 3)
    tick(s)
    assert all(t.assigned_to == other.id for t in new)


def test_reassignment_does_not_change_the_sla_result():
    s = fresh()
    t = tickets(s, 1)[0]
    tick(s)
    first_at, leaver = t.assigned_at, t.assigned_to
    s.clock.set(dt(2026, 10, 12, 11, 30))          # an hour later, long past the 30-minute deadline
    mark_member_unavailable(s, leaver, s.clock.now())
    tick(s)
    assert t.status == "ASSIGNED" and t.assigned_to != leaver
    assert t.assigned_at == first_at and not t.breached
    assert events(s, "BREACHED") == []


def test_only_members_on_shift_can_leave_and_unknown_is_rejected():
    s = fresh()
    on_ids = {m.id for m in on_shift(s)}
    off = [m for m in s.members if m.group == "IDAHO" and m.id not in on_ids][0]
    with pytest.raises(AssignmentError, match="not on shift"):
        mark_member_unavailable(s, off.id, s.clock.now())
    with pytest.raises(AssignmentError, match="Unknown"):
        mark_member_unavailable(s, "nobody", s.clock.now())


def test_nobody_else_on_shift_waits_until_someone_starts():
    s = fresh()
    a, b = on_shift(s)
    s.roster[b.id]["2026-10-12"] = "PL"            # the other morning person is on planned leave
    t = tickets(s, 1)[0]
    tick(s)
    assert t.assigned_to == a.id

    res = mark_member_unavailable(s, a.id, s.clock.now())
    assert res["group_covered"] is False
    assert len(events(s, "NO_COVERAGE")) == 1
    tick(s)
    assert t.status == "PENDING" and t.reassign_pending

    s.clock.set(dt(2026, 10, 12, 13, 0))           # the evening person starts at 12:30
    tick(s)
    evening = [m for m in s.members if m.group == "IDAHO" and m.id not in (a.id, b.id)][0]
    assert t.status == "ASSIGNED" and t.assigned_to == evening.id


def test_member_can_be_marked_back_on_shift():
    s = fresh()
    a = on_shift(s)[0]
    mark_member_unavailable(s, a.id, s.clock.now())
    assert a.id not in {m.id for m in on_shift(s)}
    restore_member(s, a.id, s.clock.now())
    assert a.id in {m.id for m in on_shift(s)}
    with pytest.raises(AssignmentError, match="not marked"):
        restore_member(s, a.id, s.clock.now())


def test_agent_mode_does_not_call_the_model_when_nobody_is_on_shift():
    s = fresh()
    s.mode = "agent"
    for m in s.members:
        if m.group == "IDAHO":
            s.roster[m.id]["2026-10-12"] = "PL"
    t = tickets(s, 1)[0]
    calls = []

    async def spy(ticket):
        calls.append(ticket.number)

    asyncio.run(dispatch(s, runner_fn=spy))
    assert calls == [] and t.status == "PENDING"


def test_agent_mode_reassigns_through_the_agent():
    s = fresh()
    s.mode = "agent"
    t = tickets(s, 1)[0]
    on = on_shift(s)
    assign_to_member(s, t, on[0].id, s.clock.now(), "first", actor="agent")
    mark_member_unavailable(s, on[0].id, s.clock.now())

    async def fake(ticket):
        pick = on_shift(s)[0]
        assign_to_member(s, ticket, pick.id, s.clock.now(), "agent picked", actor="agent")

    async def go():
        tasks = await dispatch(s, runner_fn=fake)
        await asyncio.gather(*tasks)

    asyncio.run(go())
    assert t.status == "ASSIGNED" and t.assigned_to != on[0].id
    assert events(s, "REASSIGNED")[-1]["actor"] == "agent"
    assert s.in_flight == set()


def test_api_leave_and_back_flow():
    app_store.reset()
    app_store.clock.reset()
    app_store.mode = "rules"
    c = TestClient(app)
    c.put("/clock", json={"time": "2026-10-12T10:00:00"})
    c.post("/tickets/simulate", json={"scenario": "normal", "count": 4, "group": "IDAHO"})
    c.post("/tick")

    load = c.get("/metrics").json()["load_per_member"]
    busy = [x for x in load if x["group"] == "IDAHO" and x["open"] > 0][0]
    assert busy["on_shift"] is True and busy["code"] in ("M", "E")

    r = c.post(f"/members/{busy['id']}/leave-now")          # no body is fine
    assert r.status_code == 200 and len(r.json()["tickets_queued"]) == busy["open"]
    waiting = [t for t in c.get("/tickets").json() if t["reassign_pending"]]
    assert waiting and all(t["seconds_to_deadline"] is None for t in waiting)

    c.post("/tick")
    moved = [t for t in c.get("/tickets").json() if t["previous_assignee_names"]]
    assert len(moved) == busy["open"] and all(t["status"] == "ASSIGNED" for t in moved)
    assert busy["name"] in moved[0]["previous_assignee_names"]

    after = {x["id"]: x for x in c.get("/metrics").json()["load_per_member"]}
    assert after[busy["id"]]["code"] == "UL" and after[busy["id"]]["on_shift"] is False
    assert after[busy["id"]]["open"] == 0

    assert c.post(f"/members/{busy['id']}/leave-now").status_code == 400    # already left
    assert c.post("/members/nobody/leave-now").status_code == 400
    assert c.post(f"/members/{busy['id']}/back").status_code == 200
    assert c.post(f"/members/{busy['id']}/back").status_code == 400
    app_store.reset()
    app_store.clock.reset()