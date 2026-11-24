import re
from app import config


def norm(label: str) -> str:
    return re.sub(r"[^a-z]+", "_", (label or "").lower()).strip("_")


def classify(label: str, assigned: bool) -> str:
    n = norm(label)
    if n in config.DONE_STATES:
        return "DONE"
    if n in config.WAITING_STATES or (n.startswith("awaiting_") and n != "awaiting_assignment"):
        return "WAITING"
    if n in ("", "awaiting_assignment", "new"):
        return "WORKING" if assigned else "UNASSIGNED"   # assigned but state not moved yet: clock runs
    return "WORKING"                                      # In Progress Investigate/Repair/Recover, etc.


def fmt(sec: float) -> str:
    h, m = divmod(int(sec) // 60, 60)
    return f"{h}h {m}m"


def work_limit_seconds(t) -> int:
    return int(config.WORK_SLA_BY_PRIORITY.get(str(t.priority), config.WORK_SLA_MINUTES)) * 60


def waited_seconds(t, now) -> float:
    end = t.closed_at or now
    w = t.waiting_seconds
    if t.wait_since and t.assigned_at:
        w += max(0.0, (end - max(t.wait_since, t.assigned_at)).total_seconds())
    return w


def work_seconds(t, now):
    if t.assigned_at is None or t.external:
        return None
    end = t.closed_at or now
    return max(0.0, (end - t.assigned_at).total_seconds() - waited_seconds(t, now))


def check_work_sla(store, t, now):
    if t.work_breached or t.external:
        return
    used = work_seconds(t, now)
    limit = work_limit_seconds(t)
    if used is None or used <= limit:
        return
    t.work_breached = t.breached = True
    msg = f"Active work {fmt(used)} exceeded the {fmt(limit)} limit (waiting excluded: {fmt(waited_seconds(t, now))})"
    t.breach_reasons.append(msg)
    t.reason = (t.reason + " [Work SLA breached]").strip()
    store.log(now, t, "BREACHED", msg, "system")


def evaluate_work_sla(store, now):
    """Open tickets can cross the limit while nobody touches them, so this runs every cycle."""
    for t in store.tickets.values():
        if t.status == "ASSIGNED":
            check_work_sla(store, t, now)


def apply_state(store, t, label, now, closed_time=None):
    cat = classify(label, assigned=t.assigned_to is not None)
    t.snow_state = label
    if cat == t.state_category:
        return
    prev, t.state_category = t.state_category, cat
    store.log(now, t, "STATE_CHANGE", f"{prev} -> {cat} ({label})", "system")

    if prev == "WAITING" and t.wait_since and t.assigned_at:
        t.waiting_seconds += max(0.0, (now - max(t.wait_since, t.assigned_at)).total_seconds())
        t.wait_since = None
    if cat == "WAITING":
        t.wait_since = now
    if cat == "DONE":
        t.closed_at = closed_time or now
        t.wait_since = None
        t.status = "CLOSED"          # leaves the load count
    check_work_sla(store, t, now)