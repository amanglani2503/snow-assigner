from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Ticket:
    sys_id: str
    number: str
    short_description: str
    priority: str
    assignment_group: str                 # an L1 group, or the L2 group after escalation
    opened_at: datetime
    in_service_hours: bool                # did it arrive inside Mon-Fri 09:00-22:00 (and not on a holiday)?
    deadline: Optional[datetime] = None   # SLA clock start + 30 min (start = arrival, or the next 09:00 if queued)
    status: str = "PENDING"               # QUEUED | PENDING | ASSIGNED
    assigned_to: Optional[str] = None
    assigned_at: Optional[datetime] = None   # time of the FIRST assignment (decides the SLA)
    breached: bool = False
    reason: str = ""
    release_at: Optional[datetime] = None    # for QUEUED tickets: when the service window next opens
    previous_assignees: list = field(default_factory=list)   # member ids, oldest first
    reassign_pending: bool = False        # True while a ticket waits for a new owner
    handover_note: str = ""               # e.g. "Reassigned from Aarav Sharma", prefixed to the next reason
    level: str = "L1"                     # L1 | L2
    escalated_from: Optional[str] = None  # the L1 group it came from, if escalated