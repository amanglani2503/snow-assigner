from collections import Counter
from datetime import datetime, timedelta

from app.roster.generator import generate_roster
from app.roster.models import MEMBERS
from app.rules.clock import IST, Clock
from app.rules.sla import SERVICE_START, is_serviceable as in_service_window

class Store:
    """In-memory store. Replace with Firestore later behind the same methods."""

    def __init__(self, clock=None, members=None):
        self.clock = clock or Clock()
        self.members = list(members or MEMBERS)
        self.mode = "rules"
        self._init_state()

    def _init_state(self):
        self.tickets = {}
        self.roster = {}
        self.audit = []
        self.last_assigned = {}
        self.in_flight = set()
        self.absence_originals = {}   # (member_id, day) -> roster code before they left
        self._seq = 0
        self._numbers = 0

    # ---- roster ----
    def load_roster(self, month, leave_prob=0.0, seed=42, ch_prob=0.0, org_holidays=0):
        roster = generate_roster(month, self.members, leave_prob, seed, ch_prob, org_holidays)
        for mid, days in roster.items():
            self.roster.setdefault(mid, {}).update(days)

    def ensure_roster(self, when):
        month = when.astimezone(IST).strftime("%Y-%m")
        first_member = self.members[0].id
        if f"{month}-01" not in self.roster.get(first_member, {}):
            self.load_roster(month)

    # ---- service calendar ----
    def is_org_holiday(self, when) -> bool:
        """True when every member is on company holiday (CH) that day."""
        self.ensure_roster(when)
        day = when.astimezone(IST).date().isoformat()
        return all(self.roster.get(m.id, {}).get(day) == "CH" for m in self.members)

    def is_serviceable(self, when) -> bool:
        """Mon-Fri 09:00-22:00, and not a company-wide holiday."""
        return in_service_window(when) and not self.is_org_holiday(when)

    def next_service_start(self, when):
        """First moment at or after `when` when the service window is open
        (skips evenings, weekends and company-wide holidays)."""
        t = when.astimezone(IST)
        if self.is_serviceable(t):
            return t
        d = t.date()
        for _ in range(120):
            start = datetime.combine(d, SERVICE_START, tzinfo=IST)
            if start >= t and self.is_serviceable(start):
                return start
            d += timedelta(days=1)
        raise ValueError("No service day found in the next 120 days")

    # ---- tickets ----
    def add_ticket(self, ticket) -> bool:
        """Idempotent on sys_id. Returns False if it already exists."""
        if ticket.sys_id in self.tickets:
            return False
        self.tickets[ticket.sys_id] = ticket
        return True

    def next_number(self) -> str:
        self._numbers += 1
        return f"INC{10000 + self._numbers:07d}"

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def load(self) -> Counter:
        return Counter(t.assigned_to for t in self.tickets.values() if t.status == "ASSIGNED")

    # ---- audit ----
    def log(self, ts, ticket, event, detail="", actor="rules"):
        self.audit.append({
            "ts": ts.isoformat(), "ticket": ticket.number,
            "event": event, "detail": detail, "actor": actor,
        })

    def log_event(self, ts, ticket_number, event, detail, actor="system"):
        self.audit.append({
            "ts": ts.isoformat(), "ticket": ticket_number,
            "event": event, "detail": detail, "actor": actor,
        })

    def log_tool(self, ts, ticket_number, tool, detail, actor="agent"):
        self.audit.append({
            "ts": ts.isoformat(), "ticket": ticket_number,
            "event": "TOOL_CALL", "detail": f"{tool}: {detail}", "actor": actor,
        })

    def reset(self):
        self._init_state()