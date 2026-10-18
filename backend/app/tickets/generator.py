import uuid
from datetime import timedelta

from app.rules.clock import IST
from app.rules.sla import SERVICE_START, SLA_MINUTES, is_serviceable

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


def queued_reason(store, opened_at, release_at) -> str:
    if opened_at.astimezone(IST).weekday() >= 5:
        why = "on a weekend"
    elif is_serviceable(opened_at) and store.is_org_holiday(opened_at):
        why = "on a company holiday"
    else:
        why = "outside 09:00-22:00"
    return f"Queued: arrived {why}. Assigned when the shift starts ({release_at:%a %d %b %H:%M})"


def build_ticket(store, group, opened_at, rng) -> Ticket:
    in_hours = store.is_serviceable(opened_at)
    # Outside the service window the ticket is queued. Its SLA clock starts when the window opens.
    release_at = None if in_hours else store.next_service_start(opened_at)
    clock_start = opened_at if in_hours else release_at
    return Ticket(
        sys_id=uuid.UUID(int=rng.getrandbits(128)).hex,
        number=store.next_number(),
        short_description=rng.choice(DESCRIPTIONS),
        priority=rng.choices(["1", "2", "3", "4"], weights=[1, 3, 4, 2])[0],
        assignment_group=group,
        opened_at=opened_at,
        in_service_hours=in_hours,
        deadline=clock_start + timedelta(minutes=SLA_MINUTES),
        status="PENDING" if in_hours else "QUEUED",
        release_at=release_at,
        reason="" if in_hours else queued_reason(store, opened_at, release_at),
    )