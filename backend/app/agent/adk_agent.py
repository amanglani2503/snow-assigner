import uuid

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app import config
from app.agent.tools import ALL_TOOLS
from app.state import store

APP_NAME = "snow_assigner"
USER_ID = "assigner"

INSTRUCTION = """You assign incoming ServiceNow tickets to team members.
Work through these steps for the ticket you are given:
1. Call get_ticket to read the ticket and its assignment_group (an L1 group, or the L2 support group for escalated tickets).
2. Call get_group_members for that group.
3. Call get_availability for every member. Only members with on_shift_now true can be assigned.
4. Call get_open_load for each member who is on shift.
5. Call assign_ticket for the on-shift member with the fewest open tickets. If there is a tie, pick any of the tied members.
   In the reason, name the people you compared and the open ticket counts you saw when you checked, in one or two sentences.
   Other tickets may be assigned at the same moment, so these counts are a snapshot and the system records the exact counts itself.

assign_ticket only accepts a member with the fewest open tickets at that moment, because other tickets are being assigned at the same time.
If assign_ticket returns an error, read the message and fix the problem.
If it says a member is not the least loaded, immediately call assign_ticket again with one of the members it suggests.
For any other error (for example the member is not on shift), pick another on-shift member and try again.

Some tickets are being reassigned because the previous owner left, or escalated to L2. Treat them like any other ticket and assign them to an on-shift member of the ticket's current group.
Never assign to someone who is not on shift or not in the ticket's group.
Tickets are general, so any on-shift member of the group can take any ticket.
When done, reply with one short sentence saying who got the ticket and why."""

_runner = None
_sessions = None


def _get_runner():
    global _runner, _sessions
    if _runner is None:
        agent = Agent(
            name="ticket_assigner",
            model=config.GEMINI_MODEL,
            description="Assigns incoming tickets to on-shift members of the right group.",
            instruction=INSTRUCTION,
            tools=ALL_TOOLS,
        )
        _sessions = InMemorySessionService()
        _runner = Runner(agent=agent, app_name=APP_NAME, session_service=_sessions)
    return _runner, _sessions


async def run_agent(ticket) -> None:
    """Run the ADK agent for one ticket. A fresh session per ticket keeps runs independent."""
    runner, sessions = _get_runner()
    session_id = f"{ticket.number}-{uuid.uuid4().hex[:6]}"
    await sessions.create_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)

    prompt = (f"A new ticket arrived. sys_id={ticket.sys_id} number={ticket.number} "
              f"assignment_group={ticket.assignment_group}. Assign it now.")
    message = types.Content(role="user", parts=[types.Part(text=prompt)])

    final = ""
    async for event in runner.run_async(user_id=USER_ID, session_id=session_id, new_message=message):
        if event.is_final_response() and event.content and event.content.parts:
            final = "".join(p.text or "" for p in event.content.parts).strip()
    if final:
        store.log(store.clock.now(), ticket, "AGENT_SUMMARY", final, actor="agent")