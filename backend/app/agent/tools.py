from contextvars import ContextVar
from datetime import datetime

from app.agent.assign import code_for
from app.rules.clock import IST
from app.rules.shifts import SHIFT_HOURS, is_on_shift
from app.rules.sla import is_serviceable as in_service_window
from app.snow.client import ServiceNowClient, SnowError
from app.state import store

# The agent loop sets this to the ticket number being worked on, so tool calls
# in the audit log can be tied to a ticket.
current_ticket: ContextVar[str] = ContextVar("current_ticket", default="-")

_client = None


def get_client() -> ServiceNowClient:
    global _client
    if _client is None:
        _client = ServiceNowClient()
    return _client


def set_client(client) -> None:
    global _client
    _client = client


def _done(tool: str, args: dict, result: dict) -> dict:
    extra = f" ({result['message']})" if result["status"] == "error" else ""
    store.log_tool(store.clock.now(), current_ticket.get(), tool, f"{args} -> {result['status']}{extra}")
    return result


def _error(message: str) -> dict:
    return {"status": "error", "message": message}


def _why_not_serviceable(opened: datetime) -> str:
    if opened.astimezone(IST).weekday() >= 5:
        return "arrived on a weekend"
    if in_service_window(opened) and store.is_org_holiday(opened):
        return "arrived on a company-wide holiday"
    return "arrived outside 09:00-22:00"


async def get_current_time() -> dict:
    """Get the current date and time (IST) and whether the service window is open.
    Tickets are only serviced Monday to Friday, 09:00 to 22:00, except on company-wide holidays.

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
    """Look up an incident in ServiceNow and check whether it arrived inside the service window.
    The service window is Monday to Friday, 09:00 to 22:00, except company-wide holidays.
    Tickets that arrived outside it must be ignored, not assigned.

    Args:
        sys_id: The ServiceNow sys_id of the incident.

    Returns:
        dict with the ticket's number, description, priority, assignment_group, opened_at,
        assigned_to (empty if unassigned), sla_due, arrival_weekday, arrived_in_service_window
        (true or false) and, when false, why_not_serviceable.
    """
    try:
        r = await get_client().get_incident(sys_id)
    except SnowError as e:
        return _done("get_ticket", {"sys_id": sys_id}, _error(str(e)))
    keep = ("sys_id", "number", "short_description", "priority",
            "assignment_group", "opened_at", "assigned_to")
    ticket = {k: r[k] for k in keep}
    ticket["sla_due"] = r["u_sla_due"]
    opened = datetime.fromisoformat(r["opened_at"])
    ticket["arrival_weekday"] = opened.strftime("%A")
    ticket["arrived_in_service_window"] = store.is_serviceable(opened)
    if not ticket["arrived_in_service_window"]:
        ticket["why_not_serviceable"] = _why_not_serviceable(opened)
    return _done("get_ticket", {"sys_id": sys_id}, {"status": "success", "ticket": ticket})


async def get_group_members(group: str) -> dict:
    """List the members of a ServiceNow assignment group.

    Args:
        group: The assignment group name, for example IDAHO, EMP, DBE, DB-LEGI1, DB-LEGI2 or NGA.

    Returns:
        dict with members, a list of objects with id and name.
    """
    try:
        members = await get_client().get_group_members(group)
    except SnowError as e:
        return _done("get_group_members", {"group": group}, _error(str(e)))
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
    """Count the open tickets currently assigned to a member in ServiceNow.

    Args:
        member_id: The member id, for example idaho-1.

    Returns:
        dict with open_tickets, the number of tickets assigned to this member.
    """
    try:
        rows = await get_client().list_assigned_to(member_id)
    except SnowError as e:
        return _done("get_open_load", {"member_id": member_id}, _error(str(e)))
    return _done("get_open_load", {"member_id": member_id},
                 {"status": "success", "member_id": member_id, "open_tickets": len(rows)})


async def assign_ticket(sys_id: str, member_id: str, reason: str) -> dict:
    """Assign a ticket to a member by updating assigned_to in ServiceNow.
    This is rejected if the ticket arrived outside the service window or is already assigned,
    or if the member is not in the ticket's group or not on shift right now.

    Args:
        sys_id: The ServiceNow sys_id of the incident.
        member_id: The member id to assign to, for example idaho-1.
        reason: One or two sentences explaining why this member was chosen.

    Returns:
        dict with status success and the assigned member, or status error with the rejection message.
    """
    args = {"sys_id": sys_id, "member_id": member_id}
    try:
        r = await get_client().assign(sys_id, member_id, work_notes=reason, actor="agent")
    except SnowError as e:
        return _done("assign_ticket", args, _error(str(e)))
    return _done("assign_ticket", args, {
        "status": "success", "number": r["number"], "assigned_to": r["assigned_to"]})


async def ignore_ticket(sys_id: str, reason: str) -> dict:
    """Ignore a ticket that arrived outside the service window (weekend, outside 09:00-22:00,
    or a company-wide holiday). Ignored tickets are not assigned and not tracked against the SLA.
    This is rejected if the ticket arrived inside the service window or is already assigned.

    Args:
        sys_id: The ServiceNow sys_id of the incident.
        reason: One sentence naming the arrival day and time and why it is not serviceable.

    Returns:
        dict with status success and the ticket number, or status error with the rejection message.
    """
    args = {"sys_id": sys_id}
    try:
        r = await get_client().ignore(sys_id, work_notes=reason, actor="agent")
    except SnowError as e:
        return _done("ignore_ticket", args, _error(str(e)))
    return _done("ignore_ticket", args, {"status": "success", "number": r["number"], "ignored": True})


ALL_TOOLS = [get_current_time, get_ticket, get_group_members,
             get_availability, get_open_load, assign_ticket, ignore_ticket]