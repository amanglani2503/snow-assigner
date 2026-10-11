import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.state import store


@pytest.fixture
def client():
    store.reset()
    store.clock.reset()
    # No "with": the background loop is not started, tests call /tick explicitly.
    return TestClient(app)


def set_monday_10am(client):
    r = client.put("/clock", json={"time": "2026-10-12T10:00:00"})
    assert r.status_code == 200
    return r.json()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_clock_set_and_get(client):
    body = set_monday_10am(client)
    assert body["now"].startswith("2026-10-12T10:0")
    assert body["weekday"] == "Monday"
    assert body["in_service_hours"] is True
    assert client.get("/clock").json()["weekday"] == "Monday"


def test_simulate_normal_then_tick_assigns(client):
    set_monday_10am(client)
    r = client.post("/tickets/simulate", json={"scenario": "normal", "count": 3, "group": "IDAHO"})
    assert r.status_code == 200
    assert len(r.json()["created"]) == 3
    client.post("/tick")
    tickets = client.get("/tickets").json()
    assert len(tickets) == 3
    assert all(t["status"] == "ASSIGNED" and t["assigned_to_name"] for t in tickets)


def test_out_of_hours_is_ignored(client):
    set_monday_10am(client)
    client.post("/tickets/simulate", json={"scenario": "out_of_hours", "group": "EMP"})
    client.post("/tick")
    t = client.get("/tickets").json()[0]
    assert t["status"] == "IGNORED" and t["assigned_to"] is None
    assert client.get("/metrics").json()["ignored"] == 1


def test_burst_metrics(client):
    set_monday_10am(client)
    client.post("/tickets/simulate", json={"scenario": "burst", "count": 20, "group": "NGA"})
    client.post("/tick")
    m = client.get("/metrics").json()
    assert m["assigned"] == 20 and m["within_sla_pct"] == 100.0
    loads = [x["open"] for x in m["load_per_member"] if x["group"] == "NGA"]
    assert sum(loads) == 20 and max(loads) - min(loads) <= 1


def test_duplicate_rejected(client):
    set_monday_10am(client)
    r = client.post("/tickets/simulate", json={"scenario": "duplicate"})
    assert r.json()["duplicates_rejected"] == 1
    assert len(client.get("/tickets").json()) == 1


def test_bad_scenario_and_group(client):
    assert client.post("/tickets/simulate", json={"scenario": "nope"}).status_code == 400
    assert client.post("/tickets/simulate", json={"group": "XYZ"}).status_code == 400


def test_roster_month(client):
    r = client.get("/roster/2026-10")
    assert r.status_code == 200
    body = r.json()
    assert len(body["days"]) == 31 and len(body["members"]) == 15
    assert body["roster"]["idaho-1"]["2026-10-10"] == "OFF"      # Saturday
    assert client.get("/roster/2026-13").status_code == 400


def test_audit_has_assigned_event(client):
    set_monday_10am(client)
    client.post("/tickets/simulate", json={"scenario": "normal", "count": 1, "group": "DBE"})
    client.post("/tick")
    assert any(a["event"] == "ASSIGNED" for a in client.get("/audit").json())


def test_reset_clears_everything(client):
    set_monday_10am(client)
    client.post("/tickets/simulate", json={"scenario": "normal", "count": 2})
    assert client.post("/reset").json()["status"] == "reset"
    assert client.get("/tickets").json() == []
    assert client.get("/audit").json() == []



def test_api_prefix_serves_the_same_routes(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.put("/api/clock", json={"time": "2026-10-12T10:00:00"}).status_code == 200
    assert client.get("/api/clock").json()["weekday"] == "Monday"
    assert client.get("/api/tickets").json() == []