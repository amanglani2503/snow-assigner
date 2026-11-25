from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, time, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app import config
from app.rules.clock import IST
from app.state import store
from app.tickets.work import waited_seconds, work_limit_seconds, work_seconds

router = APIRouter(prefix="/analytics")


# ---------- helpers ----------

def _period(rng, start, end, now):
    today = datetime.combine(now.date(), time(0, 0), tzinfo=IST)
    if rng == "custom":
        try:
            s = datetime.combine(datetime.fromisoformat(start).date(), time(0, 0), tzinfo=IST)
            e = datetime.combine(datetime.fromisoformat(end).date(), time(0, 0), tzinfo=IST) + timedelta(days=1)
        except (TypeError, ValueError):
            raise HTTPException(400, "custom range needs start and end as YYYY-MM-DD")
        if e <= s:
            raise HTTPException(400, "end must not be before start")
        if (e - s).days > 366:
            raise HTTPException(400, "range is limited to 366 days")
        return s, e
    days = {"today": 1, "7d": 7, "30d": 30}.get(rng)
    if days is None:
        raise HTTPException(400, "range must be today, 7d, 30d or custom")
    return today - timedelta(days=days - 1), today + timedelta(days=1)


def _select(s, e, group, priority):
    return [t for t in store.tickets.values()
            if s <= t.opened_at < e
            and (not group or t.assignment_group == group)
            and (not priority or str(t.priority) == priority)]


def _facts(t, now) -> dict:
    handled = t.assigned_at is not None and not t.external    # first assignment seen by this service
    start = t.release_at or t.opened_at                        # SLA clock start
    assign_secs = max(0.0, (t.assigned_at - start).total_seconds()) if handled else None

    if handled:
        assign_breach = bool(t.deadline and t.assigned_at > t.deadline)
        assign_over = (t.assigned_at - t.deadline).total_seconds() if assign_breach else 0
    elif t.status == "PENDING" and t.deadline and now > t.deadline:
        assign_breach, assign_over = True, (now - t.deadline).total_seconds()   # still waiting and overdue
    else:
        assign_breach, assign_over = False, 0

    work_secs = work_seconds(t, now) if handled else None
    work_breach = bool(t.work_breached and not t.external)
    work_over = (work_secs - work_limit_seconds(t)) if (work_breach and work_secs is not None) else 0
    wait_secs = waited_seconds(t, now) if handled else None
    return {"handled": handled, "assign_secs": assign_secs, "assign_breach": assign_breach,
            "assign_over": assign_over, "work_secs": work_secs, "work_breach": work_breach,
            "work_over": work_over, "wait_secs": wait_secs}


def _pct(ok, total):
    return round(100 * ok / total, 1) if total else None


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _assign_sla(rows):
    considered = [d for _, d in rows if d["handled"] or d["assign_breach"]]
    return _pct(sum(not d["assign_breach"] for d in considered), len(considered))


def _work_sla(rows):
    considered = [d for t, d in rows if (d["handled"] and t.status == "CLOSED") or d["work_breach"]]
    return _pct(sum(not d["work_breach"] for d in considered), len(considered))


def _kpis(rows) -> dict:
    handled = [(t, d) for t, d in rows if d["handled"]]
    closed = [(t, d) for t, d in handled if t.status == "CLOSED"]
    return {
        "received": len(rows),
        "open": sum(t.status != "CLOSED" for t, _ in rows),
        "assign_sla_pct": _assign_sla(rows),
        "work_sla_pct": _work_sla(rows),
        "assign_breaches": sum(d["assign_breach"] for _, d in rows),
        "work_breaches": sum(d["work_breach"] for _, d in rows),
        "avg_assign_seconds": _avg(d["assign_secs"] for _, d in handled),
        "avg_work_seconds": _avg(d["work_secs"] for _, d in closed),
        "avg_wait_seconds": _avg(d["wait_secs"] for _, d in handled),
        "auto_pct": _pct(len(handled), sum(1 for t, _ in rows if t.assigned_at is not None)),
    }


# ---------- routes ----------

@router.get("")
async def analytics(rng: str = Query("7d", alias="range"), start: Optional[str] = None,
                    end: Optional[str] = None, group: Optional[str] = None,
                    priority: Optional[str] = None):
    now = store.clock.now()
    s, e = _period(rng, start, end, now)
    names = {m.id: m.name for m in store.members}

    rows = [(t, _facts(t, now)) for t in _select(s, e, group, priority)]
    prev_rows = [(t, _facts(t, now)) for t in _select(s - (e - s), s, group, priority)]

    # time buckets: hourly for a single day, daily otherwise
    hourly = (e - s) <= timedelta(days=1)
    step = timedelta(hours=1) if hourly else timedelta(days=1)
    starts, cur = [], s
    while cur < e:
        starts.append(cur)
        cur += step
    labels = [b.strftime("%H:00") if hourly else b.strftime("%d %b") for b in starts]
    idx = lambda t: min(max(int((t.opened_at - s) / step), 0), len(starts) - 1)

    groups = sorted({t.assignment_group for t, _ in rows})
    volume = [{"label": l, "total": 0, **{g: 0 for g in groups}} for l in labels]
    by_bucket = defaultdict(list)
    for t, d in rows:
        i = idx(t)
        volume[i][t.assignment_group] += 1
        volume[i]["total"] += 1
        by_bucket[i].append((t, d))
    sla_trend = [{"label": labels[i],
                  "assignment": _assign_sla(by_bucket[i]) if by_bucket[i] else None,
                  "work": _work_sla(by_bucket[i]) if by_bucket[i] else None}
                 for i in range(len(starts))]

    # where tickets sit right now
    flow = {"Queued": 0, "Pending": 0, "Assigned": 0, "Waiting": 0, "Closed": 0}
    for t, _ in rows:
        if t.status == "QUEUED": flow["Queued"] += 1
        elif t.status == "PENDING": flow["Pending"] += 1
        elif t.status == "CLOSED": flow["Closed"] += 1
        elif t.state_category == "WAITING": flow["Waiting"] += 1
        else: flow["Assigned"] += 1

    # time-to-assign histogram
    edges = [1, 5, 15, 30, 60]
    names_d = ["< 1 min", "1-5 min", "5-15 min", "15-30 min", "30-60 min", "> 60 min"]
    dist = [0] * 6
    for _, d in rows:
        if d["assign_secs"] is not None:
            dist[bisect_right(edges, d["assign_secs"] / 60)] += 1

    # arrival heatmap: weekday (Mon=0) x hour, IST
    heat = [[0] * 24 for _ in range(7)]
    for t, _ in rows:
        o = t.opened_at.astimezone(IST)
        heat[o.weekday()][o.hour] += 1

    # per group
    per = defaultdict(list)
    for t, d in rows:
        per[t.assignment_group].append((t, d))
    group_table, work_vs_wait = [], []
    for g in groups:
        gr = per[g]
        handled = [(t, d) for t, d in gr if d["handled"]]
        closed = [(t, d) for t, d in handled if t.status == "CLOSED"]
        group_table.append({
            "group": g, "received": len(gr),
            "breaches": sum(d["assign_breach"] or d["work_breach"] for _, d in gr),
            "assign_sla_pct": _assign_sla(gr), "work_sla_pct": _work_sla(gr),
            "avg_work_seconds": _avg(d["work_secs"] for _, d in closed)})
        w, wt = _avg(d["work_secs"] for _, d in handled), _avg(d["wait_secs"] for _, d in handled)
        work_vs_wait.append({"group": g, "work_min": round((w or 0) / 60, 1), "wait_min": round((wt or 0) / 60, 1)})

    # breached tickets (drill-down list)
    breaches = []
    for t, d in rows:
        if not (d["assign_breach"] or d["work_breach"]):
            continue
        breaches.append({
            "sys_id": t.sys_id, "number": t.number, "description": t.short_description,
            "group": t.assignment_group, "priority": str(t.priority),
            "member": names.get(t.assigned_to, "") if t.assigned_to else "",
            "types": (["assignment"] if d["assign_breach"] else []) + (["work"] if d["work_breach"] else []),
            "assign_over_seconds": int(d["assign_over"]), "work_over_seconds": int(d["work_over"]),
            "work_seconds": None if d["work_secs"] is None else int(d["work_secs"]),
            "waited_seconds": None if d["wait_secs"] is None else int(d["wait_secs"]),
            "opened_at": t.opened_at.isoformat(), "status": t.status})
    breaches.sort(key=lambda b: max(b["assign_over_seconds"], b["work_over_seconds"]), reverse=True)

    # at risk: live, ignores the date range
    live = [t for t in store.tickets.values()
            if (not group or t.assignment_group == group)
            and (not priority or str(t.priority) == priority)]
    risk_work, risk_assign = [], []
    for t in live:
        if t.status == "ASSIGNED" and not t.external and not t.work_breached:
            used = work_seconds(t, now)
            limit = work_limit_seconds(t)
            if used is not None and used / limit * 100 >= config.AT_RISK_PCT:
                risk_work.append({"sys_id": t.sys_id, "number": t.number, "group": t.assignment_group,
                                  "member": names.get(t.assigned_to, ""), "used_pct": round(used / limit * 100),
                                  "remaining_seconds": int(limit - used)})
        elif t.status == "PENDING" and t.deadline and not t.reassign_pending:
            left = (t.deadline - now).total_seconds()
            if left <= config.AT_RISK_ASSIGN_MINUTES * 60:
                risk_assign.append({"sys_id": t.sys_id, "number": t.number, "group": t.assignment_group,
                                    "seconds_left": int(left)})
    risk_work.sort(key=lambda r: -r["used_pct"])
    risk_assign.sort(key=lambda r: r["seconds_left"])

    return {
        "now": now.isoformat(), "period": {"start": s.isoformat(), "end": e.isoformat(), "hourly": hourly},
        "target": config.SLA_TARGET_PCT, "all_groups": sorted({m.group for m in store.members}),
        "kpis": _kpis(rows), "prev": _kpis(prev_rows),
        "groups": groups, "volume": volume, "sla_trend": sla_trend,
        "flow": [{"name": k, "value": v} for k, v in flow.items()],
        "work_vs_wait": work_vs_wait,
        "assign_dist": [{"label": l, "count": c} for l, c in zip(names_d, dist)],
        "heatmap": heat, "group_table": group_table,
        "breaches": breaches[:500], "at_risk": {"work": risk_work[:50], "assign": risk_assign[:50]},
    }


@router.get("/ticket/{sys_id}/timeline")
async def timeline(sys_id: str):
    t = store.tickets.get(sys_id)
    if t is None:
        raise HTTPException(404, "Ticket not found")
    return {"number": t.number,
            "events": [a for a in store.audit if a["ticket"] == t.number and a["event"] != "TOOL_CALL"]}