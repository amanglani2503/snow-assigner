import uuid
from datetime import timedelta

from app.rules.sla import SLA_MINUTES, is_serviceable

from .models import Ticket

# General-purpose descriptions: no category, any group member can take any ticket.
DESCRIPTIONS = [
    "Unable to access application after update",
    "Request for new user account setup",
    "Report fails to generate",
    "Job failed overnight, needs investigation",
    "System running slowly for multiple users",
    "Request to restore data from backup",
    "Error message on login page",
    "Permission change request",
    "Scheduled task did not run",
    "Data mismatch between two systems",
]

IGNORED_REASON = "Ignored: arrived outside service hours (Mon-Fri 09:00-22:00)"
HOLIDAY_REASON = "Ignored: company holiday (all locations), not serviceable"


def ignored_reason(store, opened_at) -> str:
    if is_serviceable(opened_at) and store.is_org_holiday(opened_at):
        return HOLIDAY_REASON
    return IGNORED_REASON


def build_ticket(store, group, opened_at, rng) -> Ticket:
    in_hours = store.is_serviceable(opened_at)
    # In agent mode the agent makes the call: every ticket starts PENDING, even out-of-hours ones.
    # In rules mode, out-of-hours tickets are ignored right away.
    ignored = not in_hours and store.mode != "agent"
    return Ticket(
        sys_id=uuid.UUID(int=rng.getrandbits(128)).hex,
        number=store.next_number(),
        short_description=rng.choice(DESCRIPTIONS),
        priority=rng.choices(["1", "2", "3", "4"], weights=[1, 3, 4, 2])[0],
        assignment_group=group,
        opened_at=opened_at,
        in_service_hours=in_hours,
        deadline=opened_at + timedelta(minutes=SLA_MINUTES) if in_hours else None,
        status="IGNORED" if ignored else "PENDING",
        reason=ignored_reason(store, opened_at) if ignored else "",
    )