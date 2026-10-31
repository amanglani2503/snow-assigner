from app.snow.client import ServiceNowClient, SnowError
from app.tickets.models import Ticket
from app.rules.sla import is_serviceable
from datetime import datetime, timedelta
import uuid


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

                # Add to local store so dispatcher/agent can process it
                store.add_ticket(ticket)
                store.log(now, ticket, "SYNCED_FROM_SNOW", f"Pulled unassigned ticket for {group_name}")

        except SnowError as e:
            # Log error or handle connection drops gracefully
            print(f"Error pulling tickets for group {group_name}: {e}")