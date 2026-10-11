from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Ticket:
    sys_id: str
    number: str
    short_description: str
    priority: str
    assignment_group: str
    opened_at: datetime
    in_service_hours: bool
    deadline: Optional[datetime] = None   # opened_at + 30 min (None if ignored)
    status: str = "PENDING"               # PENDING | ASSIGNED | IGNORED
    assigned_to: Optional[str] = None
    assigned_at: Optional[datetime] = None
    breached: bool = False
    reason: str = ""