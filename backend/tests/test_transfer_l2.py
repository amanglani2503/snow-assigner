from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.agent.assign import eligible_members
from app.agent.engine import (
    AssignmentError, escalate_to_l2, mark_member_unavailable, tick, transfer_ticket,
)
from app.main import app
from app.roster.generator import days_in_month, generate_roster, org_holiday_dates
from app.roster.models import GROUPS, L2_GROUP, MEMBERS
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


def assigned_ticket(s, group="IDAHO"):
    t = simulate(s, "normal", count=1, group=group)["created"][0]
    tick(s)
    assert t.status == "ASSIGNED"
    return t


def on_shift(s, group):
    return eligible_members(group, s.clock.now(), s.members, s.roster)


def events(s, name):
    return [a for a in s.audit if a["event"] == name]


# ---------- transfer within the same group ----------

def test_transfer_to_another_member_on_shift():
    s = fresh()
    t = assigned_ticket(s)
    first_at, deadline, old = t.assigned_at, t.deadline, t.assigned_to
    target = [m for m in on_shift(s, "IDAHO") if m.id != old][0]
    transfer_ticket(s, t, target.id, s.clock.now(), "lunch break", actor="user")
    assert t.assigned_to == target.id and t.status == "ASSIGNED"
    assert t.previous_assignees == [old]
    assert t.assigned_at == first_at and t.deadline == deadline and not t.breached
    assert t.reason.startswith("Transferred from") and "lunch break" in t.reason
    assert events(s, "TRANSFERRED")[-1]["actor"] == "user"
    assert s.load()[target.id] == 1 and s.load().get(old, 0) == 0


def test_transfer_rejections():
    s = fresh()
    t = assigned_ticket(s)
    now = s.clock.now()
    on_ids = {m.id for m in on_shift(s, "IDAHO")}
    off = [m for m in s.members if m.group == "IDAHO" and m.id not in on_ids][0]
    with pytest.raises(AssignmentError, match="already assigned to that member"):
        transfer_ticket(s, t, t.assigned_to, now)
    with pytest.raises(AssignmentError, match="not in group"):
        transfer_ticket(s, t, "nga-1", now)
    with pytest.raises(AssignmentError, match="not on shift"):
        transfer_ticket(s, t, off.id, now)
    with pytest.raises(AssignmentError, match="Unknown"):
        transfer_ticket(s, t, "nobody", now)
    pending = simulate(s, "normal", count=1, group="EMP")["created"][0]
    with pytest.raises(AssignmentError, match="Only assigned"):
        transfer_ticket(s, pending, "emp-1", now)


# ---------- escalation to L2 ----------

def test_l2_team_exists_and_gets_no_direct_tickets():
    l2 = [m for m in MEMBERS if m.group == L2_GROUP]
    assert len(l2) == 3 and L2_GROUP not in GROUPS
    s = fresh()
    simulate(s, "normal", count=30, group=None, seed=3)
    tick(s)
    for t in s.tickets.values():
        owner = next(m for m in s.members if m.id == t.assigned_to)
        assert owner.group == t.assignment_group and owner.group != L2_GROUP


def test_escalation_moves_ticket_to_an_l2_member_on_shift():
    s = fresh()
    t = assigned_ticket(s, "IDAHO")
    first_at, old = t.assigned_at, t.assigned_to
    res = escalate_to_l2(s, t, s.clock.now(), "needs DB expertise")
    assert res["l2_covered"] is True and t.status == "PENDING" and t.reassign_pending
    assert t.assignment_group == L2_GROUP and t.level == "L2" and t.escalated_from == "IDAHO"
    tick(s)
    owner = next(m for m in s.members if m.id == t.assigned_to)
    assert t.status == "ASSIGNED" and owner.group == L2_GROUP
    assert t.previous_assignees == [old] and t.assigned_at == first_at and not t.breached
    assert t.reason.startswith("Escalated to L2 from")
    kinds = [a["event"] for a in s.audit if a["ticket"] == t.number]
    assert kinds[-2:] == ["ESCALATED_L2", "REASSIGNED"]
    assert s.load().get(old, 0) == 0


def test_escalation_rejections():
    s = fresh()
    pending = simulate(s, "normal", count=1, group="IDAHO")["created"][0]
    with pytest.raises(AssignmentError, match="Only assigned"):
        escalate_to_l2(s, pending, s.clock.now())
    tick(s)
    escalate_to_l2(s, pending, s.clock.now())
    tick(s)
    with pytest.raises(AssignmentError, match="already with L2"):
        escalate_to_l2(s, pending, s.clock.now())


def test_escalation_waits_when_nobody_in_l2_is_on_shift():
    s = fresh()
    t = assigned_ticket(s, "EMP")
    saved = {m.id: s.roster[m.id]["2026-10-12"] for m in s.members if m.group == L2_GROUP}
    for mid in saved:
        s.roster[mid]["2026-10-12"] = "PL"
    res = escalate_to_l2(s, t, s.clock.now())
    assert res["l2_covered"] is False and len(events(s, "NO_COVERAGE")) == 1
    tick(s)
    assert t.status == "PENDING" and t.assignment_group == L2_GROUP
    for mid, code in saved.items():
        s.roster[mid]["2026-10-12"] = code           # L2 comes back
    tick(s)
    assert t.status == "ASSIGNED"


def test_transfer_and_leave_work_inside_l2():
    s = fresh()
    t = assigned_ticket(s, "NGA")
    escalate_to_l2(s, t, s.clock.now())
    tick(s)
    l2_on = on_shift(s, L2_GROUP)
    assert len(l2_on) >= 2
    other = [m for m in l2_on if m.id != t.assigned_to][0]
    transfer_ticket(s, t, other.id, s.clock.now(), actor="user")
    assert t.assigned_to == other.id
    mark_member_unavailable(s, other.id, s.clock.now())
    tick(s)
    assert t.status == "ASSIGNED" and t.assigned_to != other.id
    assert next(m for m in s.members if m.id == t.assigned_to).group == L2_GROUP


def test_every_group_including_l2_keeps_morning_and_evening_cover():
    groups = set(GROUPS) | {L2_GROUP}
    for seed in range(1, 21):
        r = generate_roster("2026-10", MEMBERS, leave_prob=0.3, ch_prob=0.3, org_holidays=2, seed=seed)
        holidays = org_holiday_dates(r, MEMBERS)
        for d in days_in_month("2026-10"):
            key = d.isoformat()
            if d.weekday() >= 5 or key in holidays:
                continue
            for g in groups:
                codes = {r[m.id][key] for m in MEMBERS if m.group == g}
                assert {"M", "E"} <= codes, (seed, key, g)


# ---------- API ----------

def test_api_transfer_and_escalate_flow():
    app_store.reset()
    app_store.clock.reset()
    app_store.mode = "rules"
    c = TestClient(app)
    c.put("/clock", json={"time": "2026-10-12T10:00:00"})
    c.post("/tickets/simulate", json={"scenario": "normal", "count": 2, "group": "IDAHO"})
    c.post("/tick")
    tickets = c.get("/tickets").json()
    t = tickets[0]
    load = c.get("/metrics").json()["load_per_member"]
    assert any(x["group"] == L2_GROUP for x in load)
    target = [x for x in load if x["group"] == "IDAHO" and x["on_shift"] and x["id"] != t["assigned_to"]][0]

    r = c.post(f"/tickets/{t['sys_id']}/transfer", json={"member_id": target["id"], "reason": "balance"})
    assert r.status_code == 200 and r.json()["assigned_to"] == target["id"]
    assert c.post(f"/tickets/{t['sys_id']}/transfer", json={"member_id": "nga-1"}).status_code == 400
    assert c.post("/tickets/nope/transfer", json={"member_id": "x"}).status_code == 404

    r = c.post(f"/tickets/{t['sys_id']}/escalate-l2")             # no body is fine
    assert r.status_code == 200 and r.json()["l2_covered"] is True
    c.post("/tick")
    after = next(x for x in c.get("/tickets").json() if x["sys_id"] == t["sys_id"])
    assert after["level"] == "L2" and after["assignment_group"] == L2_GROUP and after["status"] == "ASSIGNED"
    assert after["escalated_from"] == "IDAHO" and len(after["previous_assignee_names"]) == 2
    assert c.post(f"/tickets/{t['sys_id']}/escalate-l2").status_code == 400
    app_store.reset()
    app_store.clock.reset()