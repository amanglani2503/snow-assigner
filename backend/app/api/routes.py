import re
import random
from dataclasses import asdict
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from app.roster.generator import days_in_month, org_holiday_dates
from app.agent.engine import tick
from app.roster.generator import days_in_month, org_holiday_dates
from app.roster.models import GROUPS
from app.rules.sla import is_serviceable
from app.state import store
from app.tickets.simulate import SCENARIOS, simulate

router = APIRouter()
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class SimulateRequest(BaseModel):
    scenario: str = "normal"
    count: Optional[int] = Field(None, ge=1, le=100)
    group: Optional[str] = None
    seed: Optional[int] = None


class ClockRequest(BaseModel):
    time: datetime   # e.g. "2026-10-12T10:00:00" (no timezone = IST)

class ModeRequest(BaseModel):
    mode: str   # "rules" or "agent"

# ---------- helpers ----------

def _names() -> dict:
    return {m.id: m.name for m in store.members}


def ticket_view(t, names=None) -> dict:
    names = names or _names()
    d = asdict(t)
    d["assigned_to_name"] = names.get(t.assigned_to)
    now = store.clock.now()
    d["seconds_to_deadline"] = (
        int((t.deadline - now).total_seconds())
        if t.deadline and t.status == "PENDING" else None
    )
    return d


def clock_view() -> dict:
    now = store.clock.now()
    return {"now": now.isoformat(), "weekday": now.strftime("%A"),
            "in_service_hours": store.is_serviceable(now),
            "holiday": store.is_org_holiday(now)}


def check_month(month: str):
    if not MONTH_RE.match(month):
        raise HTTPException(400, "month must look like 2026-10")


def roster_view(month: str) -> dict:
    days = [d.isoformat() for d in days_in_month(month)]
    holidays = org_holiday_dates(store.roster, store.members)
    return {
        "month": month,
        "days": days,
        "org_holidays": [d for d in days if d in holidays],
        "members": [{"id": m.id, "name": m.name, "group": m.group, "location": m.location}
                    for m in store.members],
        "roster": {
            m.id: {d: store.roster.get(m.id, {}).get(d, "OFF") for d in days}
            for m in store.members
        },
    }


# ---------- routes ----------

@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/groups")
async def groups():
    return [
        {"group": g,
         "members": [{"id": m.id, "name": m.name} for m in store.members if m.group == g]}
        for g in GROUPS
    ]


@router.get("/clock")
async def get_clock():
    return clock_view()


@router.put("/clock")
async def set_clock(body: ClockRequest):
    store.clock.set(body.time)
    return clock_view()


@router.post("/clock/reset")
async def reset_clock():
    store.clock.reset()
    return clock_view()


@router.post("/tickets/simulate")
async def simulate_tickets(body: SimulateRequest):
    if body.scenario not in SCENARIOS:
        raise HTTPException(400, f"scenario must be one of {SCENARIOS}")
    if body.group and body.group not in GROUPS:
        raise HTTPException(400, f"group must be one of {GROUPS}")
    try:
        res = simulate(store, body.scenario, body.count, body.group, body.seed)
    except ValueError as e:
        raise HTTPException(400, str(e))
    names = _names()
    return {
        "scenario": res["scenario"],
        "created": [ticket_view(t, names) for t in res["created"]],
        "duplicates_rejected": res["duplicates_rejected"],
        "clock": res["clock"].isoformat(),
    }


@router.get("/tickets")
async def list_tickets(
    status: Optional[str] = Query(None, description="PENDING | ASSIGNED | IGNORED"),
    group: Optional[str] = None,
    limit: int = Query(200, ge=1, le=1000),
):
    names = _names()
    items = [
        t for t in store.tickets.values()
        if (not status or t.status == status.upper())
        and (not group or t.assignment_group == group)
    ]
    items.sort(key=lambda t: (t.opened_at, t.number), reverse=True)
    return [ticket_view(t, names) for t in items[:limit]]


@router.post("/tick")
async def run_tick():
    """Run the assignment pass now (the background loop also does this every 3 seconds)."""
    return {"audit_added": tick(store)}


@router.get("/roster/{month}")
async def get_roster(month: str):
    check_month(month)
    first = store.members[0].id
    if f"{month}-01" not in store.roster.get(first, {}):
        store.load_roster(month)
    return roster_view(month)


@router.post("/roster/{month}/generate")
async def generate_roster_route(
    month: str,
    leave_prob: float = Query(0.0, ge=0, le=0.3, description="chance of planned leave (PL) per member per weekday"),
    ch_prob: float = Query(0.0, ge=0, le=0.3, description="chance of a location company holiday (CH) per weekday"),
    org_holidays: int = Query(0, ge=0, le=5, description="company-wide holidays in the month"),
    seed: Optional[int] = None,
):
    check_month(month)
    if seed is None:
        seed = random.randrange(1_000_000)
    store.load_roster(month, leave_prob=leave_prob, seed=seed,
                      ch_prob=ch_prob, org_holidays=org_holidays)
    return roster_view(month)


@router.get("/metrics")
async def metrics():
    tickets = list(store.tickets.values())
    assigned = [t for t in tickets if t.status == "ASSIGNED"]
    ok = [t for t in assigned if not t.breached]
    waits = [(t.assigned_at - t.opened_at).total_seconds() for t in assigned]
    load = store.load()
    return {
        "total": len(tickets),
        "assigned": len(assigned),
        "pending": sum(t.status == "PENDING" for t in tickets),
        "ignored": sum(t.status == "IGNORED" for t in tickets),
        "breached": sum(t.breached for t in tickets),
        "within_sla_pct": round(100 * len(ok) / len(assigned), 1) if assigned else None,
        "avg_seconds_to_assign": round(sum(waits) / len(waits), 1) if waits else None,
        "load_per_member": [
            {"id": m.id, "name": m.name, "group": m.group, "open": load.get(m.id, 0)}
            for m in store.members
        ],
    }


@router.get("/audit")
async def audit(limit: int = Query(100, ge=1, le=1000)):
    return list(reversed(store.audit))[:limit]


@router.post("/reset")
async def reset():
    store.reset()
    store.clock.reset()
    return {"status": "reset", **clock_view()}

@router.get("/mode")
async def get_mode():
    return {"mode": store.mode}


@router.put("/mode")
async def set_mode(body: ModeRequest):
    if body.mode not in ("rules", "agent"):
        raise HTTPException(400, "mode must be 'rules' or 'agent'")
    store.mode = body.mode
    return {"mode": store.mode}