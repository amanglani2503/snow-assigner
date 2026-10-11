import asyncio
import inspect
from datetime import datetime

import httpx

from app.agent.assign import eligible_members
from app.agent.dispatcher import assign_with_agent, dispatch
from app.agent.engine import tick
from app.agent.tools import (
    ALL_TOOLS, assign_ticket, get_ticket, ignore_ticket, set_client,
)
from app.main import app
from app.roster.generator import org_holiday_dates
from app.rules.clock import IST
from app.snow.client import ServiceNowClient
from app.state import store
from app.tickets.simulate import simulate


def setup_function():
    store.reset()
    store.clock.reset()
    store.mode = "rules"
    store.load_roster("2026-10")
    store.clock.set(datetime(2026, 10, 12, 10, 0, tzinfo=IST))   # Monday 10:00
    set_client(ServiceNowClient(base_url="http://test/mock-snow",
                                transport=httpx.ASGITransport(app=app)))


def teardown_function():
    store.mode = "rules"


def run(coro):
    return asyncio.run(coro)


def make(scenario="normal", group="IDAHO"):
    return simulate(store, scenario, count=1, group=group)["created"][0]


def events(name):
    return [a for a in store.audit if a["event"] == name]


async def fake_agent(ticket):
    """What the real agent is told to do: read the ticket, then ignore or assign."""
    r = await get_ticket(ticket.sys_id)
    info = r["ticket"]
    if not info["arrived_in_service_window"]:
        await ignore_ticket(ticket.sys_id, f"{info['arrival_weekday']}: {info['why_not_serviceable']}")
        return
    pick = eligible_members(ticket.assignment_group, store.clock.now(), store.members, store.roster)[0]
    await assign_ticket(ticket.sys_id, pick.id, "fake agent")


# ---------- ticket creation depends on the mode ----------

def test_rules_mode_ignores_out_of_hours_immediately():
    t = make("weekend", "EMP")
    assert t.status == "IGNORED"


def test_agent_mode_leaves_out_of_hours_tickets_pending_for_the_agent():
    store.mode = "agent"
    weekend = make("weekend", "EMP")
    night = make("out_of_hours", "EMP")
    assert weekend.status == "PENDING" and not weekend.in_service_hours and weekend.deadline is None
    assert night.status == "PENDING" and not night.in_service_hours
    assert events("IGNORED") == []


# ---------- tools ----------

def test_get_ticket_reports_service_window():
    store.mode = "agent"
    ok = make("normal", "IDAHO")
    sat = make("weekend", "IDAHO")
    a = run(get_ticket(ok.sys_id))["ticket"]
    b = run(get_ticket(sat.sys_id))["ticket"]
    assert a["arrived_in_service_window"] is True and "why_not_serviceable" not in a
    assert b["arrived_in_service_window"] is False
    assert b["arrival_weekday"] == "Saturday" and "weekend" in b["why_not_serviceable"]


def test_get_ticket_explains_night_and_company_holiday():
    store.mode = "agent"
    night = make("out_of_hours", "IDAHO")
    assert "outside 09:00-22:00" in run(get_ticket(night.sys_id))["ticket"]["why_not_serviceable"]

    store.load_roster("2026-10", org_holidays=1, seed=5)
    day = sorted(org_holiday_dates(store.roster, store.members))[0]
    d = datetime.fromisoformat(day)
    store.clock.set(datetime(d.year, d.month, d.day, 11, 0, tzinfo=IST))
    hol = make("normal", "IDAHO")
    assert "company-wide holiday" in run(get_ticket(hol.sys_id))["ticket"]["why_not_serviceable"]


def test_ignore_ticket_success_for_out_of_hours():
    store.mode = "agent"
    t = make("weekend", "EMP")
    r = run(ignore_ticket(t.sys_id, "Saturday 11:00, weekend"))
    assert r["status"] == "success" and r["ignored"] is True
    assert t.status == "IGNORED" and t.assigned_to is None
    assert events("IGNORED")[-1]["actor"] == "agent"


def test_ignore_ticket_rejected_for_in_hours_ticket():
    store.mode = "agent"
    t = make("normal", "IDAHO")
    r = run(ignore_ticket(t.sys_id, "trying to skip work"))
    assert r["status"] == "error" and "must be assigned" in r["message"]
    assert t.status == "PENDING"


def test_assign_ticket_rejected_for_out_of_hours_ticket():
    store.mode = "agent"
    t = make("weekend", "IDAHO")
    r = run(assign_ticket(t.sys_id, "idaho-1", "test"))
    assert r["status"] == "error" and "outside service hours" in r["message"]
    assert t.status == "PENDING"


def test_ignore_ticket_rejected_when_already_assigned_or_ignored():
    store.mode = "agent"
    ok = make("normal", "IDAHO")
    tick(store, store.clock.now())
    assert ok.status == "ASSIGNED"
    assert run(ignore_ticket(ok.sys_id, "x"))["status"] == "error"
    sat = make("weekend", "EMP")
    run(ignore_ticket(sat.sys_id, "first"))
    assert "already ignored" in run(ignore_ticket(sat.sys_id, "second"))["message"]


def test_tools_are_adk_friendly_with_seven_tools():
    assert len(ALL_TOOLS) == 7
    for f in ALL_TOOLS:
        sig = inspect.signature(f)
        assert inspect.iscoroutinefunction(f), f.__name__
        assert f.__doc__ and f.__doc__.strip(), f.__name__
        assert all(p.annotation is not inspect.Parameter.empty for p in sig.parameters.values()), f.__name__
        assert sig.return_annotation is dict, f.__name__


# ---------- dispatch, fallback and rules interplay ----------

def test_agent_mode_ignores_out_of_hours_and_assigns_in_hours():
    store.mode = "agent"

    async def decide(t):
        await assign_with_agent(store, t, runner_fn=fake_agent)

    ok = make("normal", "IDAHO")
    run(decide(ok))
    night = make("out_of_hours", "EMP")     # moves the clock to Monday 23:15
    run(decide(night))
    sat = make("weekend", "DBE")            # moves the clock to Saturday 11:00
    run(decide(sat))

    assert ok.status == "ASSIGNED"
    assert night.status == "IGNORED" and sat.status == "IGNORED"
    assert [a["actor"] for a in events("IGNORED")] == ["agent", "agent"]
    assert events("AGENT_FALLBACK") == []


def test_in_hours_ticket_still_assigned_by_agent():
    store.mode = "agent"
    t = make("normal", "NGA")
    run(assign_with_agent(store, t, runner_fn=fake_agent))
    assert t.status == "ASSIGNED" and events("AGENT_FALLBACK") == []
    assert events("ASSIGNED")[-1]["actor"] == "agent"


def test_agent_failure_on_out_of_hours_ticket_falls_back_to_rules_ignore():
    store.mode = "agent"
    t = make("weekend", "EMP")

    async def boom(ticket):
        raise RuntimeError("model down")

    run(assign_with_agent(store, t, runner_fn=boom))
    assert t.status == "IGNORED"
    assert "model down" in events("AGENT_FALLBACK")[0]["detail"]
    assert events("IGNORED")[-1]["actor"] == "rules-fallback"


def test_agent_finishing_without_a_decision_falls_back():
    store.mode = "agent"
    t = make("weekend", "EMP")

    async def lazy(ticket):
        return None

    run(assign_with_agent(store, t, runner_fn=lazy))
    assert t.status == "IGNORED"
    assert "did not assign" in events("AGENT_FALLBACK")[0]["detail"]


def test_switching_to_rules_mode_ignores_leftover_pending_out_of_hours_tickets():
    store.mode = "agent"
    t = make("weekend", "EMP")
    assert t.status == "PENDING"
    store.mode = "rules"
    run(dispatch(store))
    assert t.status == "IGNORED" and events("IGNORED")[-1]["actor"] == "rules"


def test_dispatch_in_agent_mode_hands_out_of_hours_ticket_to_agent():
    store.mode = "agent"
    t = make("weekend", "EMP")

    async def go():
        tasks = await dispatch(store, runner_fn=fake_agent)
        await asyncio.gather(*tasks)

    run(go())
    assert t.status == "IGNORED" and events("IGNORED")[-1]["actor"] == "agent"
    assert store.in_flight == set()