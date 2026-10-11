import calendar
import random
from datetime import date

# roster shape: { member_id: { "2026-10-12": code } }
# codes: M (09:00-18:30), E (12:30-22:00), OFF (weekend), PL (planned leave),
#        CH (company holiday at the member's location)


def days_in_month(month: str) -> list[date]:
    y, m = map(int, month.split("-"))
    return [date(y, m, d) for d in range(1, calendar.monthrange(y, m)[1] + 1)]


def org_holiday_dates(roster: dict, members) -> set:
    """Dates on which every member is on company holiday. Those days are not serviceable."""
    if not members:
        return set()
    first = roster.get(members[0].id, {})
    return {
        day for day, code in first.items()
        if code == "CH" and all(roster.get(m.id, {}).get(day) == "CH" for m in members)
    }


def _covered(day_codes: dict, by_group: dict) -> bool:
    """Every group still has at least one M and one E on duty."""
    for group_members in by_group.values():
        codes = {day_codes[m.id] for m in group_members}
        if "M" not in codes or "E" not in codes:
            return False
    return True


def generate_roster(month: str, members, leave_prob: float = 0.04, seed: int = 42,
                    ch_prob: float = 0.0, org_holidays: int = 0) -> dict:
    """Weekends OFF, M/E alternate by ISO week (members in a group start on opposite shifts).

    leave_prob    chance per member per weekday of a planned leave (PL)
    ch_prob       chance per location per weekday of a company holiday (CH) for that location
    org_holidays  number of weekdays that are a company holiday for everyone (not serviceable)

    PL and location CH are only applied when every group keeps a morning and an evening
    person that day, so random leave and holidays never break service coverage.
    """
    rng = random.Random(seed)
    by_group: dict[str, list] = {}
    for m in members:
        by_group.setdefault(m.group, []).append(m)

    days = days_in_month(month)
    weekdays = [d for d in days if d.weekday() < 5]
    roster = {m.id: {} for m in members}

    for group_members in by_group.values():
        for idx, m in enumerate(group_members):
            for d in days:
                if d.weekday() >= 5:
                    code = "OFF"
                else:
                    code = "M" if (d.isocalendar()[1] + idx) % 2 == 0 else "E"
                roster[m.id][d.isoformat()] = code

    org_days = {d.isoformat() for d in rng.sample(weekdays, min(org_holidays, len(weekdays)))}
    for key in org_days:
        for m in members:
            roster[m.id][key] = "CH"

    locations = sorted({m.location for m in members})
    for d in weekdays:
        key = d.isoformat()
        if key in org_days:
            continue
        day = {m.id: roster[m.id][key] for m in members}

        for loc in locations:
            if rng.random() < ch_prob:
                trial = dict(day)
                for m in members:
                    if m.location == loc:
                        trial[m.id] = "CH"
                if _covered(trial, by_group):
                    day = trial

        for m in members:
            if day[m.id] in ("M", "E") and rng.random() < leave_prob:
                trial = dict(day)
                trial[m.id] = "PL"
                if _covered(trial, by_group):
                    day = trial

        for mid, code in day.items():
            roster[mid][key] = code
    return roster