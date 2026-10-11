from collections import Counter
from datetime import datetime

from app.agent.engine import tick
from app.rules.clock import IST
from app.tickets.repository import Store
from app.tickets.simulate import simulate

MON_10AM = datetime(2026, 10, 12, 10, 0, tzinfo=IST)


def show(store, title):
    print(f"\n=== {title} | clock {store.clock.now():%a %d %b %H:%M} ===")
    for t in sorted(store.tickets.values(), key=lambda t: t.number):
        due = f"{t.deadline:%a %H:%M}" if t.deadline else "-"
        print(f"{t.number} {t.assignment_group:9} {t.status:8} {t.assigned_to or '-':8} "
              f"breach={t.breached!s:5} due {due:9} | {t.reason}")


def run(title, scenario, **kw):
    store = Store()
    store.clock.set(MON_10AM)
    simulate(store, scenario, **kw)
    tick(store)
    show(store, title)
    return store


run("Normal flow", "normal", count=4, seed=1)
run("Out of hours (ignored)", "out_of_hours", seed=2)
run("Weekend (ignored)", "weekend", seed=3)
run("Edge 21:50", "edge_2150", seed=4)

s = run("Burst of 20 (NGA)", "burst", seed=6)
print("\nBurst distribution:", dict(Counter(t.assigned_to for t in s.tickets.values())))