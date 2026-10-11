import asyncio
from datetime import datetime

import httpx

from app import config
from app.agent.dispatcher import assign_with_agent
from app.agent.tools import set_client
from app.main import app
from app.rules.clock import IST
from app.snow.client import ServiceNowClient
from app.state import store
from app.tickets.simulate import simulate

store.load_roster("2026-10")
store.clock.set(datetime(2026, 10, 12, 10, 0, tzinfo=IST))   # Monday 10:00
set_client(ServiceNowClient(base_url="http://test/mock-snow", transport=httpx.ASGITransport(app=app)))

ticket = simulate(store, "normal", count=1, group="IDAHO")["created"][0]
print(f"Model: {config.GEMINI_MODEL}")
print(f"Ticket {ticket.number} for group {ticket.assignment_group}\n")

asyncio.run(assign_with_agent(store, ticket))

for a in store.audit:
    print(f"[{a['actor']:14}] {a['event']:14} {a['detail']}")
print(f"\nResult: {ticket.status}, assigned to {ticket.assigned_to}")
print(f"Reason: {ticket.reason}")