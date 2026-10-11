import asyncio
from datetime import datetime

import httpx
from fastapi.testclient import TestClient

from app.agent.assign import eligible_members
from app.agent.dispatcher import assign_with_agent, dispatch
from app.agent.tools import assign_ticket, set_client
from app.main import app
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


def make(group="IDAHO"):
    return simulate(store, "normal", count=1, group=group)["created"][0]


def on_shift(group):
    return eligible_members(group, store.clock.now(), store.members, store.roster)


async def good_agent(ticket):
    pick = on_shift(ticket.assignment_group)[0]
    await assign_ticket(ticket.sys_id, pick.id, "fake agent")


def events(name):
    return [a for a in store.audit if a["event"] == name]


def test_agent_success_assigns_without_fallback():
    t = make()
    asyncio.run(assign_with_agent(store, t, runner_fn=good_agent))
    assert t.status == "ASSIGNED"
    assert events("AGENT_FALLBACK") == []
    assert events("ASSIGNED")[-1]["actor"] == "agent"
    assert t.sys_id not in store.in_flight


def test_agent_exception_falls_back_to_rules():
    t = make()

    async def boom(ticket):
        raise RuntimeError("model down")

    asyncio.run(assign_with_agent(store, t, runner_fn=boom))
    assert t.status == "ASSIGNED"
    fb = events("AGENT_FALLBACK")
    assert len(fb) == 1 and "model down" in fb[0]["detail"]
    assert events("ASSIGNED")[-1]["actor"] == "rules-fallback"
    assert t.sys_id not in store.in_flight


def test_agent_timeout_falls_back_to_rules():
    t = make()

    async def slow(ticket):
        await asyncio.sleep(5)

    asyncio.run(assign_with_agent(store, t, runner_fn=slow, timeout=0.05))
    assert t.status == "ASSIGNED"
    assert "timed out" in events("AGENT_FALLBACK")[0]["detail"]


def test_agent_finishing_without_assigning_falls_back():
    t = make()

    async def lazy(ticket):
        return None

    asyncio.run(assign_with_agent(store, t, runner_fn=lazy))
    assert t.status == "ASSIGNED"
    assert "did not assign" in events("AGENT_FALLBACK")[0]["detail"]


def test_dispatch_in_rules_mode_uses_tick():
    t = make()
    tasks = asyncio.run(dispatch(store))
    assert tasks == []
    assert t.status == "ASSIGNED"
    assert events("ASSIGNED")[-1]["actor"] == "rules"


def test_dispatch_in_agent_mode_uses_agent():
    store.mode = "agent"
    t1, t2 = make("IDAHO"), make("EMP")

    async def go():
        tasks = await dispatch(store, runner_fn=good_agent)
        await asyncio.gather(*tasks)

    asyncio.run(go())
    assert t1.status == "ASSIGNED" and t2.status == "ASSIGNED"
    assert {a["actor"] for a in events("ASSIGNED")} == {"agent"}
    assert store.in_flight == set()


def test_dispatch_respects_concurrency_limit():
    store.mode = "agent"
    for _ in range(5):
        make("NGA")

    async def slowish(ticket):
        await asyncio.sleep(0.05)
        await good_agent(ticket)

    async def go():
        counts = []
        for _ in range(3):
            tasks = await dispatch(store, runner_fn=slowish, max_concurrent=2)
            counts.append(len(tasks))
            await asyncio.gather(*tasks)
        return counts

    assert asyncio.run(go()) == [2, 2, 1]


def test_mode_api_get_put_invalid():
    c = TestClient(app)
    assert c.get("/mode").json() == {"mode": "rules"}
    assert c.put("/mode", json={"mode": "agent"}).json() == {"mode": "agent"}
    assert c.put("/mode", json={"mode": "bogus"}).status_code == 400


def test_mode_survives_reset():
    c = TestClient(app)
    c.put("/mode", json={"mode": "agent"})
    c.post("/reset")
    assert c.get("/mode").json()["mode"] == "agent"