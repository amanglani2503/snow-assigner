import asyncio
import inspect
from datetime import datetime

import httpx

from app.agent.assign import eligible_members
from app.agent.tools import (
    ALL_TOOLS, assign_ticket, current_ticket, get_availability, get_current_time,
    get_group_members, get_open_load, get_ticket, set_client,
)
from app.main import app
from app.rules.clock import IST
from app.snow.client import ServiceNowClient
from app.state import store
from app.tickets.simulate import simulate


def setup_function():
    store.reset()
    store.clock.reset()
    store.load_roster("2026-10")
    store.clock.set(datetime(2026, 10, 12, 10, 0, tzinfo=IST))   # Monday 10:00
    set_client(ServiceNowClient(base_url="http://test/mock-snow",
                                transport=httpx.ASGITransport(app=app)))


def run(coro):
    return asyncio.run(coro)


def make(group="IDAHO", scenario="normal"):
    return simulate(store, scenario, count=1, group=group)["created"][0]


def on_and_off(group):
    now = store.clock.now()
    on = {m.id for m in eligible_members(group, now, store.members, store.roster)}
    ids = [m.id for m in store.members if m.group == group]
    return [i for i in ids if i in on], [i for i in ids if i not in on]


def test_current_time_follows_simulated_clock():
    r = run(get_current_time())
    assert r["status"] == "success" and r["weekday"] == "Monday" and r["in_service_window"] is True


def test_group_members():
    r = run(get_group_members("NGA"))
    assert r["status"] == "success" and len(r["members"]) == 3
    assert run(get_group_members("XYZ"))["status"] == "error"


def test_availability_on_and_off_shift():
    on, off = on_and_off("IDAHO")
    assert on and off
    a, b = run(get_availability(on[0])), run(get_availability(off[0]))
    assert a["on_shift_now"] is True and a["today_code"] in ("M", "E")
    assert b["on_shift_now"] is False


def test_availability_unknown_member():
    assert run(get_availability("nobody"))["status"] == "error"


def test_get_ticket_and_unknown_ticket():
    t = make()
    r = run(get_ticket(t.sys_id))
    assert r["status"] == "success" and r["ticket"]["number"] == t.number
    assert r["ticket"]["assigned_to"] == ""
    assert run(get_ticket("nope"))["status"] == "error"


def test_open_load_counts_assigned_tickets():
    on, _ = on_and_off("IDAHO")
    assert len(on) >= 2
    t1, t2, t3 = make(), make(), make()
    assert run(get_open_load(on[0]))["open_tickets"] == 0
    run(assign_ticket(t1.sys_id, on[0], "test"))
    run(assign_ticket(t2.sys_id, on[1], "test"))
    run(assign_ticket(t3.sys_id, on[0], "test"))
    assert run(get_open_load(on[0]))["open_tickets"] == 2
    assert run(get_open_load(on[1]))["open_tickets"] == 1


def test_assign_success():
    on, _ = on_and_off("IDAHO")
    t = make()
    r = run(assign_ticket(t.sys_id, on[0], "Lightest load"))
    assert r["status"] == "success" and r["assigned_to"] == on[0]
    assert t.status == "ASSIGNED"
    assert any(a["event"] == "ASSIGNED" and a["actor"] == "agent" for a in store.audit)


def test_assign_wrong_group_returns_error():
    t = make()
    r = run(assign_ticket(t.sys_id, "nga-1", "test"))
    assert r["status"] == "error" and "not in group" in r["message"]
    assert t.status == "PENDING"


def test_assign_off_shift_member_returns_error():
    _, off = on_and_off("IDAHO")
    t = make()
    r = run(assign_ticket(t.sys_id, off[0], "test"))
    assert r["status"] == "error" and "not on shift" in r["message"]
    assert t.status == "PENDING"


def test_assign_twice_returns_error():
    on, _ = on_and_off("IDAHO")
    t = make()
    run(assign_ticket(t.sys_id, on[0], "first"))
    r = run(assign_ticket(t.sys_id, on[0], "second"))
    assert r["status"] == "error" and "already assigned" in r["message"]


def test_tool_calls_are_logged_with_ticket_context():
    t = make()

    async def go():
        current_ticket.set(t.number)
        await get_group_members("IDAHO")
        await get_current_time()

    run(go())
    calls = [a for a in store.audit if a["event"] == "TOOL_CALL"]
    assert len(calls) == 2
    assert all(a["ticket"] == t.number and a["actor"] == "agent" for a in calls)
    assert calls[0]["detail"].startswith("get_group_members")


def test_tools_are_adk_friendly():
    assert len(ALL_TOOLS) == 6
    for f in ALL_TOOLS:
        sig = inspect.signature(f)
        assert inspect.iscoroutinefunction(f), f.__name__
        assert f.__doc__ and f.__doc__.strip(), f.__name__
        assert all(p.annotation is not inspect.Parameter.empty for p in sig.parameters.values()), f.__name__
        assert sig.return_annotation is dict, f.__name__