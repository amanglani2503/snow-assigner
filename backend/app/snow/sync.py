from app.snow.client import ServiceNowClient, SnowError
from app.tickets.models import Ticket
from datetime import datetime, timedelta

async def pull_unassigned_tickets_from_snow(store):
    """Query ServiceNow for unassigned active tickets across all groups and sync to store."""
    client = ServiceNowClient()
    groups = ["IDAHO", "EMP", "DBE", "DB-LEGI1", "DB-LEGI2", "NGA", "Network"]

    now = store.clock.now()

    for group_name in groups:
        try:
            # Query real ServiceNow instance for unassigned active tickets
            raw_tickets = await client.list_unassigned_tickets(group_name)

            for r in raw_tickets:
                sys_id = r.get("sys_id")
                if not sys_id or sys_id in store.tickets:
                    continue  # Already tracked locally

                opened_at_str = r.get("opened_at")
                opened_at = datetime.fromisoformat(opened_at_str) if opened_at_str else now

                in_hours = store.is_serviceable(opened_at)
                release_at = None if in_hours else store.next_service_start(opened_at)
                clock_start = opened_at if in_hours else release_at

                # Build local Ticket object representation
                ticket = Ticket(
                    sys_id=sys_id,
                    number=r.get("number"),
                    short_description=r.get("short_description", "No description"),
                    priority=r.get("priority", "3"),
                    assignment_group=group_name,
                    opened_at=opened_at,
                    in_service_hours=in_hours,
                    deadline=clock_start + timedelta(minutes=30),
                    status="PENDING" if in_hours else "QUEUED",
                    release_at=release_at,
                    level=r.get("u_level", "L1"),
                )
                # Inside pull_unassigned_tickets_from_snow(store) in sync.py:
                store.add_ticket(ticket)

                # Log the incident number explicitly to the audit trail / logs
                store.log(now, ticket, "INCIDENT_ARRIVED",
                          f"New ticket arrived from ServiceNow: {ticket.number} for group {group_name}")

        except SnowError as e:
            # Log error or handle connection drops gracefully
            print(f"Error pulling tickets for group {group_name}: {e}")


def sync_snow_ticket_to_store(store, r):
    """Convert a raw ServiceNow incident record into a fully valid local Ticket object."""
    sys_id = r.get("sys_id")
    if not sys_id or sys_id in store.tickets:
        return False

    now = store.clock.now()
    opened_at_str = r.get("opened_at")
    opened_at = datetime.fromisoformat(opened_at_str) if opened_at_str else now

    in_hours = store.is_serviceable(opened_at)
    release_at = None if in_hours else store.next_service_start(opened_at)
    clock_start = opened_at if in_hours else release_at

    # Check if already assigned in ServiceNow
    assigned_to = r.get("assigned_to")
    status = "ASSIGNED" if assigned_to else ("PENDING" if in_hours else "QUEUED")

    ticket = Ticket(
        sys_id=sys_id,
        number=r.get("number", store.next_number()),
        short_description=r.get("short_description") or r.get("description", "No description"),
        priority=str(r.get("priority", "3")),
        assignment_group=r.get("assignment_group"),
        opened_at=opened_at,
        in_service_hours=in_hours,
        deadline=clock_start + timedelta(minutes=30) if clock_start else now + timedelta(minutes=30),
        status=status,
        assigned_to=assigned_to if assigned_to else None,
        assigned_at=now if assigned_to else None,
        release_at=release_at,
        level=r.get("u_level", "L1"),
        reason="Pulled from ServiceNow sync"
    )

    store.add_ticket(ticket)
    store.log(now, ticket, "SYNCED_FROM_SNOW", f"Pulled ticket {ticket.number} into store")
    return True