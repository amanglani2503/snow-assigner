import asyncio
from datetime import datetime

import httpx
import pytest

from app.agent.assign import eligible_members
from app.main import app
from app.rules.clock import IST
from app.snow.client import ServiceNowClient, SnowError
from app.state import store
from app.tickets.simulate import simulate


def setup_function():
    store.reset()
    store.clock.reset()
    store.load_roster("2026-10")
    store.clock.set(datetime(2026, 10, 12, 10, 0, tzinfo=IST))   # Monday 10:00


def client():
    # In-process transport: no server needed. Background loop is not running in tests.
    return ServiceNowClient(base_url="http://test/mock-snow", transport=httpx.ASGITransport(app=app))


def make(group, n=1, scenario="normal"):
    return simulate(store, scenario, count=n, group=group)["created"]


def test_list_unassigned_returns_only_that_group():
    make("IDAHO", 3)
    make("EMP", 2)
    rows = asyncio.run(client().list_unassigned("IDAHO"))
    assert len(rows) == 3 and all(r["assignment_group"] == "IDAHO" for r in rows)


def test_out_of_hours_ticket_not_listed():
    make("EMP", scenario="out_of_hours")
    assert asyncio.run(client().list_unassigned("EMP")) == []


def test_group_members():
    members = asyncio.run(client().get_group_members("NGA"))
    assert len(members) == 3
    assert {m["id"] for m in members} == {"nga-1", "nga-2", "nga-3"}


def test_assign_success_updates_ticket_and_audit():
    t = make("IDAHO")[0]
    now = store.clock.now()
    pick = eligible_members("IDAHO", now, store.members, store.roster)[0]
    res = asyncio.run(client().assign(t.sys_id, pick.id, "Lightest load", actor="agent"))
    assert res["assigned_to"] == pick.id and res["state"] == "2"
    assert t.status == "ASSIGNED"
    assert store.audit[-1]["actor"] == "agent"


def test_assign_wrong_group_rejected():
    t = make("IDAHO")[0]
    with pytest.raises(SnowError, match="not in group") as e:
        asyncio.run(client().assign(t.sys_id, "nga-1"))
    assert e.value.status == 400 and t.status == "PENDING"


def test_assign_twice_rejected():
    t = make("IDAHO")[0]
    now = store.clock.now()
    pick = eligible_members("IDAHO", now, store.members, store.roster)[0]
    asyncio.run(client().assign(t.sys_id, pick.id))
    with pytest.raises(SnowError, match="already assigned"):
        asyncio.run(client().assign(t.sys_id, pick.id))


def test_unknown_incident_404():
    with pytest.raises(SnowError) as e:
        asyncio.run(client().get_incident("does-not-exist"))
    assert e.value.status == 404