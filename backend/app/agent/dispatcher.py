import asyncio
import logging

from app import config
from app.agent.assign import eligible_members
from app.agent.engine import assign_one_by_rules, tick
from app.agent.tools import current_ticket

log = logging.getLogger("snow-assigner")
_background: set = set()


async def _default_runner(ticket):
    from app.agent.adk_agent import run_agent   # imported lazily so tests don't need Gemini
    await run_agent(ticket)


async def assign_with_agent(store, ticket, runner_fn=None, timeout=None):
    """Let the agent decide the ticket (assign or ignore). If it fails, decide by rules instead."""
    runner_fn = runner_fn or _default_runner
    timeout = timeout or config.AGENT_TIMEOUT_SECONDS
    store.in_flight.add(ticket.sys_id)
    token = current_ticket.set(ticket.number)
    try:
        problem = ""
        try:
            await asyncio.wait_for(runner_fn(ticket), timeout=timeout)
            if ticket.status == "PENDING":
                problem = "agent did not assign or ignore the ticket"
        except asyncio.TimeoutError:
            problem = f"agent timed out after {timeout}s"
        except Exception as e:
            problem = f"agent error: {str(e)[:200]}"

        if problem and ticket.status == "PENDING" and ticket.sys_id in store.tickets:
            store.log(store.clock.now(), ticket, "AGENT_FALLBACK",
                      problem + "; deciding by rules", actor="agent")
            try:
                assign_one_by_rules(store, ticket, actor="rules-fallback")
            except Exception:
                log.exception("fallback failed for %s", ticket.number)
    finally:
        current_ticket.reset(token)
        store.in_flight.discard(ticket.sys_id)


async def dispatch(store, runner_fn=None, max_concurrent=None):
    """Called every few seconds. Rules mode: run tick(). Agent mode: start agent runs
    for pending tickets, up to the concurrency limit. Returns the started tasks."""
    if store.mode != "agent":
        tick(store)
        return []

    limit = max_concurrent or config.AGENT_MAX_CONCURRENT
    now = store.clock.now()
    store.ensure_roster(now)
    pending = sorted(
        (t for t in store.tickets.values()
         if t.status == "PENDING" and t.sys_id not in store.in_flight),
        key=lambda t: t.opened_at,
    )
    tasks = []
    for t in pending:
        if len(store.in_flight) >= limit:
            break
        store.in_flight.add(t.sys_id)    # claimed now, so the next cycle won't start a second run
        task = asyncio.create_task(assign_with_agent(store, t, runner_fn))
        _background.add(task)
        task.add_done_callback(_background.discard)
        tasks.append(task)
    return tasks