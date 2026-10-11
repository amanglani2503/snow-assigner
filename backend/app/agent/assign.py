from datetime import datetime

from app.rules.clock import IST
from app.rules.shifts import is_on_shift


def code_for(roster: dict, member_id: str, now: datetime) -> str:
    day = now.astimezone(IST).date().isoformat()
    return roster.get(member_id, {}).get(day, "OFF")


def eligible_members(group: str, now: datetime, members, roster: dict) -> list:
    """Members of the group who are on shift at `now` (not OFF/LEAVE)."""
    return [
        m for m in members
        if m.group == group and is_on_shift(code_for(roster, m.id, now), now)
    ]


def pick_least_loaded(candidates: list, load: dict, last_assigned: dict):
    """Fewest open tickets wins. Ties go to whoever was assigned longest ago
    (round-robin), then by id for stable results."""
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda m: (load.get(m.id, 0), last_assigned.get(m.id, 0), m.id),
    )