from app.agent.assign import code_for, eligible_members, pick_least_loaded
from app.rules.shifts import is_on_shift
from app.tickets.generator import ignored_reason

class AssignmentError(ValueError):
    """Raised when an assignment breaks a rule. Used by tick(), the agent tool and the mock API."""


def validate_assignment(store, ticket, member_id, now, actor="rules"):
    if ticket.status == "IGNORED" or not ticket.in_service_hours:
        raise AssignmentError("Ticket arrived outside service hours (Mon-Fri 09:00-22:00) and is ignored")
    if ticket.status == "ASSIGNED":
        raise AssignmentError(f"Ticket {ticket.number} is already assigned")
    member = next((m for m in store.members if m.id == member_id), None)
    if member is None:
        raise AssignmentError(f"Unknown member {member_id}")
    if member.group != ticket.assignment_group:
        raise AssignmentError(f"{member.name} is not in group {ticket.assignment_group}")
    if not is_on_shift(code_for(store.roster, member.id, now), now):
        raise AssignmentError(f"{member.name} is not on shift at {now:%a %H:%M}")

    if actor == "agent":
        # Balance rule: checked at the moment of assignment, so concurrent agent runs can't
        # all pick the same person from stale counts.
        candidates = eligible_members(ticket.assignment_group, now, store.members, store.roster)
        load = store.load()
        lowest = min(load.get(m.id, 0) for m in candidates)
        if load.get(member.id, 0) > lowest:
            counts = ", ".join(f"{m.name}: {load.get(m.id, 0)} open" for m in candidates)
            options = ", ".join(f"{m.name} ({m.id})" for m in candidates if load.get(m.id, 0) == lowest)
            raise AssignmentError(
                f"{member.name} is not the least loaded ({counts}). Assign to one of: {options}")
    return member

def ignore_ticket_now(store, ticket, now, reason, actor="rules"):
    """The single place where a ticket gets ignored. Only tickets that arrived outside
    the service window (weekend, outside 09:00-22:00, company-wide holiday) can be ignored."""
    if ticket.status == "ASSIGNED":
        raise AssignmentError(f"Ticket {ticket.number} is already assigned")
    if ticket.status == "IGNORED":
        raise AssignmentError(f"Ticket {ticket.number} is already ignored")
    if ticket.in_service_hours:
        raise AssignmentError(
            "Ticket arrived within the service window (Mon-Fri 09:00-22:00, not a company holiday) "
            "and must be assigned, not ignored")
    ticket.status = "IGNORED"
    ticket.deadline = None
    ticket.reason = reason
    store.log(now, ticket, "IGNORED", reason, actor)

def assign_to_member(store, ticket, member_id, now, reason, actor="rules"):
    """The single place where a ticket gets assigned. Validates, updates, logs."""
    store.ensure_roster(now)
    member = validate_assignment(store, ticket, member_id, now, actor)

    if actor == "agent":
        # Agent runs can overlap, so the counts the model read may be stale by now.
        # Record the real counts at the moment of assignment (before this ticket is added).
        candidates = eligible_members(ticket.assignment_group, now, store.members, store.roster)
        load = store.load()
        counts = ", ".join(f"{m.name} {load.get(m.id, 0)}" for m in candidates)
        reason = f"{reason} | System check at assignment, open tickets before this one: {counts}"

    ticket.status = "ASSIGNED"
    ticket.assigned_to = member.id
    ticket.assigned_at = now
    ticket.breached = now > ticket.deadline
    store.last_assigned[member.id] = store.next_seq()

    ticket.reason = reason
    if ticket.breached:
        ticket.reason += " [SLA breached]"
        store.log(now, ticket, "BREACHED", f"Assigned after deadline {ticket.deadline:%a %d %b %H:%M}", actor)
    store.log(now, ticket, "ASSIGNED", f"{member.name} ({member.id}): {ticket.reason}", actor)
    return member


def tick(store, now=None):
    """Rules-based pass over PENDING tickets. Skips tickets the agent is working on.
    Returns the audit entries added."""
    now = now or store.clock.now()
    store.ensure_roster(now)
    first_new = len(store.audit)
    load = store.load()

    pending = sorted(
        (t for t in store.tickets.values()
         if t.status == "PENDING" and t.sys_id not in store.in_flight),
        key=lambda t: t.opened_at,
    )

    for t in pending:
        if not t.in_service_hours:
            # Out-of-hours / weekend / holiday ticket still PENDING: ignore it by rules.
            ignore_ticket_now(store, t, now, ignored_reason(store, t.opened_at), actor="rules")
            continue
        
        candidates = eligible_members(t.assignment_group, now, store.members, store.roster)
        chosen = pick_least_loaded(candidates, load, store.last_assigned)
        if chosen is None:
            continue   # not expected: roster guarantees coverage 09:00-22:00

        before = load.get(chosen.id, 0)
        names = ", ".join(m.name for m in candidates)
        reason = f"Least-loaded of {len(candidates)} on shift ({names}); {chosen.name} had {before} open"
        assign_to_member(store, t, chosen.id, now, reason, actor="rules")
        load[chosen.id] = before + 1

    return store.audit[first_new:]



def assign_one_by_rules(store, ticket, now=None, actor="rules"):
    """Rules-based assignment of a single ticket. Used as the agent's fallback."""
    now = now or store.clock.now()
    store.ensure_roster(now)
    if not ticket.in_service_hours:
        ignore_ticket_now(store, ticket, now, ignored_reason(store, ticket.opened_at), actor=actor)
        return True
    
    candidates = eligible_members(ticket.assignment_group, now, store.members, store.roster)
    load = store.load()
    chosen = pick_least_loaded(candidates, load, store.last_assigned)
    if chosen is None:
        return False
    names = ", ".join(m.name for m in candidates)
    reason = (f"Fallback, least-loaded of {len(candidates)} on shift ({names}); "
              f"{chosen.name} had {load.get(chosen.id, 0)} open")
    assign_to_member(store, ticket, chosen.id, now, reason, actor=actor)
    return True