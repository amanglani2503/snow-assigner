from datetime import datetime

from app.agent.assign import eligible_members, pick_least_loaded
from app.roster.generator import days_in_month, generate_roster
from app.roster.models import GROUPS, MEMBERS, Member
from app.rules.clock import IST


def dt(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=IST)


# ---------- seed data ----------

def test_six_groups_with_two_or_three_members():
    assert len(GROUPS) == 6
    for g in GROUPS:
        n = len([m for m in MEMBERS if m.group == g])
        assert 2 <= n <= 3, g

def test_member_ids_unique():
    ids = [m.id for m in MEMBERS]
    assert len(ids) == len(set(ids))


# ---------- roster generator ----------

def test_weekends_are_off():
    r = generate_roster("2026-10", MEMBERS)
    for days in r.values():
        assert days["2026-10-10"] == "OFF"   # Sat
        assert days["2026-10-11"] == "OFF"   # Sun

def test_no_leave_when_prob_zero():
    r = generate_roster("2026-10", MEMBERS, leave_prob=0)
    assert all(c != "LEAVE" for days in r.values() for c in days.values())

def test_every_day_of_month_present():
    r = generate_roster("2026-10", MEMBERS)
    assert len(r["idaho-1"]) == len(days_in_month("2026-10")) == 31

def test_every_group_has_both_shifts_on_weekdays():
    r = generate_roster("2026-10", MEMBERS, leave_prob=0)
    for g in GROUPS:
        for day in ("2026-10-12", "2026-10-13", "2026-10-19"):
            codes = {r[m.id][day] for m in MEMBERS if m.group == g}
            assert {"M", "E"} <= codes, (g, day)

def test_same_seed_same_roster():
    assert generate_roster("2026-10", MEMBERS) == generate_roster("2026-10", MEMBERS)


# ---------- eligibility (hand-built roster) ----------

TEAM = [Member("a", "A", "G"), Member("b", "B", "G"), Member("c", "C", "G"), Member("x", "X", "OTHER")]
ROSTER = {
    "a": {"2026-10-12": "M"},
    "b": {"2026-10-12": "E"},
    "c": {"2026-10-12": "LEAVE"},
    "x": {"2026-10-12": "M"},
}

def ids(group, now):
    return sorted(m.id for m in eligible_members(group, now, TEAM, ROSTER))

def test_1229_only_morning():
    assert ids("G", dt(2026, 10, 12, 12, 29)) == ["a"]

def test_1230_both_shifts():
    assert ids("G", dt(2026, 10, 12, 12, 30)) == ["a", "b"]

def test_1830_only_evening():
    assert ids("G", dt(2026, 10, 12, 18, 30)) == ["b"]

def test_2200_nobody():
    assert ids("G", dt(2026, 10, 12, 22, 0)) == []

def test_before_900_nobody():
    assert ids("G", dt(2026, 10, 12, 8, 59)) == []

def test_leave_excluded_and_other_group_excluded():
    assert "c" not in ids("G", dt(2026, 10, 12, 13))
    assert "x" not in ids("G", dt(2026, 10, 12, 13))

def test_member_missing_from_roster_is_off():
    assert ids("G", dt(2026, 10, 13, 13)) == []


# ---------- assignment ----------

def test_least_loaded_wins():
    got = pick_least_loaded(TEAM[:2], {"a": 3, "b": 1}, {})
    assert got.id == "b"

def test_tie_goes_to_longest_idle():
    got = pick_least_loaded(TEAM[:2], {"a": 1, "b": 1}, {"a": 100, "b": 50})
    assert got.id == "b"

def test_no_candidates_returns_none():
    assert pick_least_loaded([], {}, {}) is None