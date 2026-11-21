from contextvars import ContextVar

from app.agent.assign import code_for
from app.rules.shifts import SHIFT_HOURS, is_on_shift
from app.state import store
from app.agent.engine import AssignmentError, assign_to_member


# The agent loop sets this to the ticket number being worked on, so tool calls
# in the audit log can be tied to a ticket.
current_ticket: ContextVar[str] = ContextVar("current_ticket", default="-")

_client = None


def _done(tool: str, args: dict, result: dict) -> dict:
    extra = f" ({result['message']})" if result["status"] == "error" else ""
    store.log_tool(store.clock.now(), current_ticket.get(), tool, f"{args} -> {result['status']}{extra}")
    return result


def _error(message: str) -> dict:
    return {"status": "error", "message": message}


async def get_current_time() -> dict:
    """Get the current date and time (IST) and whether the service window is open.
    Tickets are only assigned Monday to Friday, 09:00 to 22:00, except on company-wide holidays.

    Returns:
        dict with now (ISO time), weekday, in_service_window (true or false) and company_holiday (true or false).
    """
    now = store.clock.now()
    return _done("get_current_time", {}, {
        "status": "success", "now": now.isoformat(),
        "weekday": now.strftime("%A"), "in_service_window": store.is_serviceable(now),
        "company_holiday": store.is_org_holiday(now),
    })


async def get_ticket(sys_id: str) -> dict:
    """Look up an incident. Returns number, short_description, priority,
    assignment_group, opened_at, assigned_to, sla_due and level."""
    t = store.tickets.get(sys_id)
    if t is None:
        return _done("get_ticket", {"sys_id": sys_id}, _error(f"Incident {sys_id} not found"))
    return _done("get_ticket", {"sys_id": sys_id}, {"status": "success", "ticket": {
        "sys_id": t.sys_id, "number": t.number, "short_description": t.short_description,
        "priority": t.priority, "assignment_group": t.assignment_group,
        "opened_at": t.opened_at.isoformat(), "assigned_to": t.assigned_to or "",
        "sla_due": t.deadline.isoformat() if t.deadline else "", "level": t.level}})


async def get_group_members(group: str) -> dict:
    """List the members of an assignment group (id and name)."""
    members = [{"id": m.id, "name": m.name} for m in store.members if m.group == group]
    if not members:
        return _done("get_group_members", {"group": group}, _error(f"No members found for group {group}"))
    return _done("get_group_members", {"group": group},
                 {"status": "success", "group": group, "members": members})


async def get_availability(member_id: str) -> dict:
    """Check whether a member is on shift right now, from the monthly roster.
    Shift M runs 09:00-18:30 and shift E runs 12:30-22:00. OFF, PL (planned leave), UL (left during the shift) and CH
    (company holiday at the member's location) mean unavailable.

    Args:
        member_id: The member id, for example idaho-1.

    Returns:
        dict with name, group, today_code (M, E, OFF, PL, UL or CH), shift_hours and on_shift_now.
    """
    now = store.clock.now()
    store.ensure_roster(now)
    member = next((m for m in store.members if m.id == member_id), None)
    if member is None:
        return _done("get_availability", {"member_id": member_id}, _error(f"Unknown member {member_id}"))
    code = code_for(store.roster, member_id, now)
    hours = SHIFT_HOURS.get(code)
    return _done("get_availability", {"member_id": member_id}, {
        "status": "success", "member_id": member_id, "name": member.name, "group": member.group,
        "today_code": code,
        "shift_hours": f"{hours[0]:%H:%M}-{hours[1]:%H:%M}" if hours else None,
        "on_shift_now": is_on_shift(code, now),
    })


async def get_open_load(member_id: str) -> dict:
    """Count the open tickets currently assigned to a member."""
    return _done("get_open_load", {"member_id": member_id}, {
        "status": "success", "member_id": member_id,
        "open_tickets": store.load().get(member_id, 0)})


async def assign_ticket(sys_id: str, member_id: str, reason: str) -> dict:
    """Assign a ticket to a member. Rejected if the ticket is queued or already assigned,
    or the member is not in the group, not on shift, or not the least loaded.

    Args:
        sys_id: The incident sys_id.
        member_id: The member id to assign to.
        reason: One or two sentences explaining the choice.
    """
    args = {"sys_id": sys_id, "member_id": member_id}
    t = store.tickets.get(sys_id)
    if t is None:
        return _done("assign_ticket", args, _error(f"Incident {sys_id} not found"))
    try:
        m = assign_to_member(store, t, member_id, store.clock.now(), reason, actor="agent")
    except AssignmentError as e:
        return _done("assign_ticket", args, _error(str(e)))
    return _done("assign_ticket", args, {"status": "success", "number": t.number, "assigned_to": m.id})


ALL_TOOLS = [get_current_time, get_ticket, get_group_members,
             get_availability, get_open_load, assign_ticket]