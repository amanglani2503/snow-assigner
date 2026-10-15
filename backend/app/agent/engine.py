from app.agent.assign import code_for, eligible_members, pick_least_loaded
from app.rules.clock import IST
from app.rules.shifts import is_on_shift
from app.tickets.generator import ignored_reason


class AssignmentError(ValueError):
    pass


def _name(store, member_id):
    m = next((m for m in store.members if m.id == member_id), None)
    return m.name if m else str(member_id)


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
    """The single place where a ticket gets assigned (first assignment or reassignment)."""
    store.ensure_roster(now)
    member = validate_assignment(store, ticket, member_id, now, actor)

    if actor == "agent":
        # Agent runs can overlap, so the counts the model read may be stale by now.
        # Record the real counts at the moment of assignment (before this ticket is added).
        candidates = eligible_members(ticket.assignment_group, now, store.members, store.roster)
        load = store.load()
        counts = ", ".join(f"{m.name} {load.get(m.id, 0)}" for m in candidates)
        reason = f"{reason} | System check at assignment, open tickets before this one: {counts}"

    reassigning = ticket.reassign_pending
    first_assignment = ticket.assigned_at is None

    ticket.status = "ASSIGNED"
    ticket.assigned_to = member.id
    ticket.reassign_pending = False
    if first_assignment:
        # The first assignment decides the SLA. A later reassignment never changes it.
        ticket.assigned_at = now
        ticket.breached = now > ticket.deadline
    store.last_assigned[member.id] = store.next_seq()

    if reassigning:
        reason = f"Reassigned from {_name(store, ticket.previous_assignees[-1])}: {reason}"
    ticket.reason = reason
    if ticket.breached:
        ticket.reason += " [SLA breached]"
        if first_assignment:
            store.log(now, ticket, "BREACHED", f"Assigned after deadline {ticket.deadline:%a %d %b %H:%M}", actor)
    store.log(now, ticket, "REASSIGNED" if reassigning else "ASSIGNED",
              f"{member.name} ({member.id}): {ticket.reason}", actor)
    return member


def mark_member_unavailable(store, member_id, now, reason="left during shift"):
    """A member on shift has to leave. They stop being eligible for today and their open
    tickets go back to the queue, where the rules loop or the agent gives them a new owner."""
    store.ensure_roster(now)
    member = next((m for m in store.members if m.id == member_id), None)
    if member is None:
        raise AssignmentError(f"Unknown member {member_id}")
    day = now.astimezone(IST).date().isoformat()
    code = code_for(store.roster, member_id, now)
    if not is_on_shift(code, now):
        raise AssignmentError(f"{member.name} is not on shift right now, so there is nothing to hand over")

    store.absence_originals[(member_id, day)] = code
    store.roster[member_id][day] = "UL"       # UL = unplanned leave (left during the shift)

    moved = []
    for t in sorted(store.tickets.values(), key=lambda t: (t.opened_at, t.number)):
        if t.status == "ASSIGNED" and t.assigned_to == member_id:
            t.status = "PENDING"
            t.assigned_to = None
            t.previous_assignees.append(member_id)
            t.reassign_pending = True
            moved.append(t)
            store.log(now, t, "REASSIGN_QUEUED", f"{member.name} left ({reason}); needs a new owner", "system")

    covered = bool(eligible_members(member.group, now, store.members, store.roster))
    store.log_event(now, "-", "MEMBER_LEFT",
                    f"{member.name} left at {now:%H:%M} ({reason}); {len(moved)} open ticket(s) handed over", "system")
    if moved and not covered:
        for t in moved:
            store.log(now, t, "NO_COVERAGE",
                      f"No other member of {member.group} is on shift: waits until someone's shift starts "
                      f"(escalate to the team lead)", "system")
    return {"member": member.name, "group": member.group,
            "tickets_queued": [t.number for t in moved], "group_covered": covered}


def restore_member(store, member_id, now):
    """Undo mark_member_unavailable for today. Tickets already moved stay with their new owners."""
    member = next((m for m in store.members if m.id == member_id), None)
    if member is None:
        raise AssignmentError(f"Unknown member {member_id}")
    day = now.astimezone(IST).date().isoformat()
    original = store.absence_originals.pop((member_id, day), None)
    if original is None:
        raise AssignmentError(f"{member.name} is not marked as having left today")
    store.roster[member_id][day] = original
    store.log_event(now, "-", "MEMBER_BACK", f"{member.name} is back on shift", "system")


def tick(store, now=None):
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
            # Out-of-hours / weekend / holiday ticket that is still PENDING: ignore it by rules.
            ignore_ticket_now(store, t, now, ignored_reason(store, t.opened_at), actor="rules")
            continue
        candidates = eligible_members(t.assignment_group, now, store.members, store.roster)
        chosen = pick_least_loaded(candidates, load, store.last_assigned)
        if chosen is None:
            continue

        before = load.get(chosen.id, 0)
        names = ", ".join(m.name for m in candidates)
        reason = f"Least-loaded of {len(candidates)} on shift ({names}); {chosen.name} had {before} open"
        assign_to_member(store, t, chosen.id, now, reason, actor="rules")
        load[chosen.id] = before + 1

    return store.audit[first_new:]


def assign_one_by_rules(store, ticket, now=None, actor="rules"):
    """Rules-based decision for a single ticket (the agent's fallback): ignore it if it
    arrived outside the service window, otherwise assign it."""
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