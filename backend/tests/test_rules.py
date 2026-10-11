from datetime import datetime

from app.rules.clock import IST, Clock
from app.rules.shifts import is_on_shift
from app.rules.sla import is_serviceable, next_service_start, sla_deadline


def dt(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=IST)


# Oct 2026: 9 = Fri, 10 = Sat, 11 = Sun, 12 = Mon

def test_weekday_in_hours_is_serviceable():
    assert is_serviceable(dt(2026, 10, 12, 10))

def test_boundaries():
    assert is_serviceable(dt(2026, 10, 12, 9, 0))
    assert not is_serviceable(dt(2026, 10, 12, 8, 59))
    assert is_serviceable(dt(2026, 10, 12, 21, 59))
    assert not is_serviceable(dt(2026, 10, 12, 22, 0))

def test_late_night_not_serviceable():
    assert not is_serviceable(dt(2026, 10, 12, 23, 15))

def test_weekend_not_serviceable():
    assert not is_serviceable(dt(2026, 10, 10, 11))
    assert not is_serviceable(dt(2026, 10, 11, 11))

def test_deadline_simple():
    assert sla_deadline(dt(2026, 10, 12, 10)) == dt(2026, 10, 12, 10, 30)

def test_deadline_edge_2150_pauses_overnight():
    assert sla_deadline(dt(2026, 10, 12, 21, 50)) == dt(2026, 10, 13, 9, 20)

def test_deadline_friday_night_rolls_to_monday():
    assert sla_deadline(dt(2026, 10, 9, 21, 50)) == dt(2026, 10, 12, 9, 20)

def test_deadline_out_of_hours_starts_next_working_day():
    assert sla_deadline(dt(2026, 10, 12, 23, 15)) == dt(2026, 10, 13, 9, 30)
    assert sla_deadline(dt(2026, 10, 12, 7, 40)) == dt(2026, 10, 12, 9, 30)

def test_deadline_weekend_starts_monday():
    assert sla_deadline(dt(2026, 10, 10, 14)) == dt(2026, 10, 12, 9, 30)

def test_next_service_start_saturday():
    assert next_service_start(dt(2026, 10, 10, 14)) == dt(2026, 10, 12, 9)

def test_morning_shift_boundaries():
    assert not is_on_shift("M", dt(2026, 10, 12, 8, 59))
    assert is_on_shift("M", dt(2026, 10, 12, 9, 0))
    assert is_on_shift("M", dt(2026, 10, 12, 18, 29))
    assert not is_on_shift("M", dt(2026, 10, 12, 18, 30))

def test_evening_shift_boundaries():
    assert not is_on_shift("E", dt(2026, 10, 12, 12, 29))
    assert is_on_shift("E", dt(2026, 10, 12, 12, 30))
    assert is_on_shift("E", dt(2026, 10, 12, 21, 59))
    assert not is_on_shift("E", dt(2026, 10, 12, 22, 0))

def test_off_and_leave_never_on_shift():
    assert not is_on_shift("OFF", dt(2026, 10, 12, 10))
    assert not is_on_shift("LEAVE", dt(2026, 10, 12, 10))

def test_clock_time_travel():
    c = Clock()
    c.set(dt(2026, 10, 12, 10))
    assert abs((c.now() - dt(2026, 10, 12, 10)).total_seconds()) < 2
    c.set(datetime(2026, 10, 10, 23, 15))  # naive is treated as IST
    assert c.now().hour in (23, 0)