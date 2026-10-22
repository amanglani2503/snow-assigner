from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.agent.assign import eligible_members
from app.agent.engine import tick
from app.agent.tools import get_availability, get_current_time
from app.main import app
from app.roster.generator import days_in_month, generate_roster, org_holiday_dates
from app.roster.models import GROUPS, MEMBERS
from app.rules.clock import IST
from app.state import store as app_store
from app.tickets.repository import Store
from app.tickets.simulate import simulate

SEEDS = range(1, 31)


def dt(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=IST)


def heavy(seed):
    return generate_roster("2026-10", MEMBERS, leave_prob=0.3, ch_prob=0.3, org_holidays=2, seed=seed)


def store_with(seed, org_holidays=0):
    s = Store()
    s.load_roster("2026-10", leave_prob=0.3, ch_prob=0.3, org_holidays=org_holidays, seed=seed)
    return s


def at(s, key, hour=14):
    d = datetime.fromisoformat(key)
    s.clock.set(dt(d.year, d.month, d.day, hour))
    return s.clock.now()


# ---------- roster generation ----------

def test_every_group_keeps_m_and_e_on_every_serviceable_day():
    for seed in SEEDS:
        r = heavy(seed)
        holidays = org_holiday_dates(r, MEMBERS)
        for d in days_in_month("2026-10"):
            key = d.isoformat()
            if d.weekday() >= 5 or key in holidays:
                continue
            for g in GROUPS:
                codes = {r[m.id][key] for m in MEMBERS if m.group == g}
                assert {"M", "E"} <= codes, (seed, key, g)


def test_company_wide_holidays_are_the_requested_number_of_weekdays():
    for seed in SEEDS:
        h = org_holiday_dates(heavy(seed), MEMBERS)
        assert len(h) == 2
        assert all(datetime.fromisoformat(x).weekday() < 5 for x in h)


def test_pl_and_ch_actually_appear_and_are_reproducible():
    codes = {c for seed in SEEDS for days in heavy(seed).values() for c in days.values()}
    assert {"M", "E", "OFF", "PL", "CH"} <= codes
    assert heavy(7) == heavy(7) and heavy(7) != heavy(8)


def test_nothing_random_unless_asked():
    r = generate_roster("2026-10", MEMBERS, leave_prob=0)
    assert {c for days in r.values() for c in days.values()} <= {"M", "E", "OFF"}


# ---------- service calendar ----------

def test_company_holiday_ticket_is_queued_until_the_next_service_day():
    s = store_with(3, org_holidays=1)
    holiday = sorted(org_holiday_dates(s.roster, s.members))[0]
    now = at(s, holiday, 11)
    assert s.is_org_holiday(now) and not s.is_serviceable(now)
    t = simulate(s, "normal", count=1, group="IDAHO")["created"][0]
    assert t.status == "QUEUED" and not t.in_service_hours
    assert "company holiday" in t.reason
    assert t.release_at > now and s.is_serviceable(t.release_at) and t.release_at.hour == 9
    tick(s, now)
    assert t.status == "QUEUED" and t.assigned_to is None
    tick(s, t.release_at)
    assert t.status == "ASSIGNED"


def test_partial_ch_day_stays_serviceable_and_excludes_those_members():
    for seed in SEEDS:
        s = store_with(seed)
        for key in sorted(s.roster["idaho-1"]):
            ch = {m.id for m in s.members if s.roster[m.id][key] == "CH"}
            if ch:
                now = at(s, key)
                assert s.is_serviceable(now)
                on = {m.id for g in GROUPS for m in eligible_members(g, now, s.members, s.roster)}
                assert not (ch & on)
                t = simulate(s, "normal", count=1, group="NGA")["created"][0]
                tick(s, now)
                assert t.status == "ASSIGNED"
                return
    pytest.fail("no partial CH day found")


def test_pl_member_is_not_eligible():
    for seed in SEEDS:
        s = store_with(seed)
        for key in sorted(s.roster["idaho-1"]):
            pl = {m.id for m in s.members if s.roster[m.id][key] == "PL"}
            if pl:
                now = at(s, key)
                on = {m.id for g in GROUPS for m in eligible_members(g, now, s.members, s.roster)}
                assert not (pl & on)
                return
    pytest.fail("no PL day found")


def test_every_group_assignable_all_service_hours_on_every_serviceable_day():
    s = store_with(5, org_holidays=2)
    for d in days_in_month("2026-10"):
        if d.weekday() >= 5:
            continue
        for minutes in range(9 * 60, 22 * 60, 30):
            now = dt(d.year, d.month, d.day, minutes // 60, minutes % 60)
            if not s.is_serviceable(now):
                continue
            for g in GROUPS:
                assert eligible_members(g, now, s.members, s.roster), (g, now)


# ---------- scenarios ----------

def test_holiday_scenario_jumps_to_company_holiday_and_ignores():
    s = store_with(5, org_holidays=1)
    s.clock.set(dt(2026, 10, 1, 10))
    t = simulate(s, "holiday", group="EMP")["created"][0]
    assert s.is_org_holiday(s.clock.now()) and t.status == "QUEUED"


def test_holiday_scenario_without_holidays_raises_and_keeps_clock():
    s = Store()
    s.clock.set(dt(2026, 10, 12, 10))
    with pytest.raises(ValueError, match="company holiday"):
        simulate(s, "holiday")
    assert s.clock.now().day == 12


def test_burst_and_edge_skip_company_holidays():
    s = Store()
    s.load_roster("2026-10")
    for m in s.members:
        s.roster[m.id]["2026-10-12"] = "CH"
        s.roster[m.id]["2026-10-16"] = "CH"
    s.clock.set(dt(2026, 10, 12, 10))
    simulate(s, "burst", count=3, group="NGA")
    assert s.clock.now().date().isoformat() == "2026-10-13"
    t = simulate(s, "edge_2150", group="IDAHO")["created"][0]
    assert t.opened_at.date().isoformat() == "2026-10-23" and t.status == "PENDING"


# ---------- API and agent tools ----------

@pytest.fixture
def client():
    app_store.reset()
    app_store.clock.reset()
    return TestClient(app)


def test_generate_roster_api_with_pl_ch_and_holidays(client):
    r = client.post("/roster/2026-10/generate?leave_prob=0.2&ch_prob=0.2&org_holidays=2&seed=11")
    assert r.status_code == 200
    body = r.json()
    assert len(body["org_holidays"]) == 2
    assert all("location" in m for m in body["members"])
    assert "CH" in {c for days in body["roster"].values() for c in days.values()}
    day = body["org_holidays"][0]
    client.put("/clock", json={"time": f"{day}T11:00:00"})
    c = client.get("/clock").json()
    assert c["holiday"] is True and c["in_service_hours"] is False


def test_holiday_scenario_api_400_without_holidays(client):
    client.put("/clock", json={"time": "2026-10-12T10:00:00"})
    assert client.post("/tickets/simulate", json={"scenario": "holiday"}).status_code == 400


def test_agent_tools_see_pl_ch_and_holidays():
    import asyncio
    app_store.reset()
    app_store.load_roster("2026-10", leave_prob=0.3, ch_prob=0.3, org_holidays=1, seed=3)
    day = sorted(org_holiday_dates(app_store.roster, app_store.members))[0]
    at(app_store, day, 11)
    t = asyncio.run(get_current_time())
    assert t["company_holiday"] is True and t["in_service_window"] is False
    a = asyncio.run(get_availability("idaho-1"))
    assert a["today_code"] == "CH" and a["on_shift_now"] is False
    app_store.reset()
    app_store.clock.reset()



def test_agent_reason_records_true_open_counts_even_if_model_text_is_stale():
    from app.agent.engine import assign_to_member
    s = Store()
    s.load_roster("2026-10")
    s.clock.set(dt(2026, 10, 12, 10))
    now = s.clock.now()
    on = eligible_members("IDAHO", now, s.members, s.roster)
    for m in on[1:]:
        s.roster[m.id]["2026-10-12"] = "PL"          # only one person left on shift
    only = on[0]
    tickets = simulate(s, "normal", count=3, group="IDAHO")["created"]
    for t in tickets:
        assign_to_member(s, t, only.id, now, "stale text: 0 open", actor="agent")
    for i, t in enumerate(tickets):
        assert f"before this one: {only.name} {i}" in t.reason