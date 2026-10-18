from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from app.agent.engine import AssignmentError, assign_to_member
from app.state import store

router = APIRouter(tags=["mock-servicenow"])


def _error(status: int, message: str, detail: str = ""):
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "detail": detail}, "status": "failure"},
    )


def _incident(t) -> dict:
    return {
        "sys_id": t.sys_id, "number": t.number, "short_description": t.short_description,
        "priority": t.priority, "assignment_group": t.assignment_group,
        "opened_at": t.opened_at.isoformat(), "assigned_to": t.assigned_to or "",
        "state": {"ASSIGNED": "2", "QUEUED": "3"}.get(t.status, "1"),   # 1 New, 2 In Progress, 3 On Hold
        "u_sla_due": t.deadline.isoformat() if t.deadline else "",
        "u_queued": t.status == "QUEUED",
        "u_level": t.level,
    }


def _matches(t, query: str) -> bool:
    """Supports: assignment_group=X, number=X, assigned_toISEMPTY, assigned_toISNOTEMPTY,
    joined with ^. Other query parts are ignored."""
    for part in filter(None, query.split("^")):
        if part == "assigned_toISEMPTY" and t.assigned_to:
            return False
        if part == "assigned_toISNOTEMPTY" and not t.assigned_to:
            return False
        if part.startswith("assignment_group=") and t.assignment_group != part.split("=", 1)[1]:
            return False
        if part.startswith("number=") and t.number != part.split("=", 1)[1]:
            return False
        if part.startswith("assigned_to=") and t.assigned_to != part.split("=", 1)[1]:
            return False
    return True


@router.get("/api/now/table/incident")
async def list_incidents(sysparm_query: str = "", sysparm_limit: int = 100):
    items = [t for t in store.tickets.values() if _matches(t, sysparm_query)]
    items.sort(key=lambda t: (t.opened_at, t.number))
    return {"result": [_incident(t) for t in items[:sysparm_limit]]}


@router.get("/api/now/table/incident/{sys_id}")
async def get_incident(sys_id: str):
    t = store.tickets.get(sys_id)
    if t is None:
        return _error(404, "No Record found", f"Record doesn't exist or ACL restricts access: {sys_id}")
    return {"result": _incident(t)}


@router.patch("/api/now/table/incident/{sys_id}")
async def patch_incident(sys_id: str, body: dict, x_actor: str = Header("agent")):
    """Assign a ticket: {"assigned_to": "<member id>", "work_notes": "<why>"}.
    X-Actor is a POC-only header so the audit log can show who made the call."""
    t = store.tickets.get(sys_id)
    if t is None:
        return _error(404, "No Record found", f"Record doesn't exist: {sys_id}")
    if not body.get("assigned_to"):
        return _error(400, "assigned_to is required")
    reason = body.get("work_notes") or "Assigned via ServiceNow API"
    try:
        assign_to_member(store, t, body["assigned_to"], store.clock.now(), reason, actor=x_actor)
    except AssignmentError as e:
        return _error(400, "Operation Failed", str(e))
    return {"result": _incident(t)}


@router.get("/api/now/table/sys_user_grmember")
async def group_members(sysparm_query: str = ""):
    """Members of a group, like ServiceNow's group membership table. Query: group=<name>."""
    group = None
    for part in sysparm_query.split("^"):
        if part.startswith("group="):
            group = part.split("=", 1)[1]
    return {"result": [
        {"group": {"value": m.group, "display_value": m.group},
         "user": {"value": m.id, "display_value": m.name}}
        for m in store.members if not group or m.group == group
    ]}