from app.agent.assign import code_for, eligible_members, pick_least_loaded
from app.roster.models import L2_GROUP
from app.rules.clock import IST
from app.rules.shifts import is_on_shift


class AssignmentError(ValueError):
    pass


def _name(store, member_id):
    m = next((m for m in store.members if m.id == member_id), None)
    return m.name if m else str(member_id)


def _check_member(store, ticket, member_id, now):
    """The member must exist, belong to the ticket's group and be on shift right now."""
    member = next((m for m in store.members if m.id == member_id), None)
    if member is None:
        raise AssignmentError(f"Unknown member {member_id}")
    if member.group != ticket.assignment_group:
        raise AssignmentError(f"{member.name} is not in group {ticket.assignment_group}")
    if not is_on_shift(code_for(store.roster, member.id, now), now):
        raise AssignmentError(f"{member.name} is not on shift at {now:%a %H:%M}")
    return member


def validate_assignment(store, ticket, member_id, now, actor="rules"):
    if ticket.status == "QUEUED":
        raise AssignmentError(
            f"Ticket {ticket.number} is queued until {ticket.release_at:%a %d %b %H:%M} and cannot be assigned yet")
    if ticket.status == "ASSIGNED":
        raise AssignmentError(f"Ticket {ticket.number} is already assigned")
    member = _check_member(store, ticket, member_id, now)

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


def release_due_tickets(store, now):
    """Queued tickets whose service window has opened become PENDING, and their SLA clock starts."""
    released = []
    queued = [t for t in store.tickets.values() if t.status == "QUEUED"]
    for t in sorted(queued, key=lambda t: (t.release_at or t.opened_at, t.number)):
        if t.release_at is not None and now >= t.release_at:
            t.status = "PENDING"
            released.append(t)
            store.log(now, t, "RELEASED",
                      f"Service window open: queued ticket released, SLA due {t.deadline:%a %H:%M}", "system")
    return released


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

    if ticket.handover_note:
        reason = f"{ticket.handover_note}: {reason}"
        ticket.handover_note = ""
    ticket.reason = reason
    if ticket.breached:
        ticket.reason += " [SLA breached]"
        if first_assignment:
            store.log(now, ticket, "BREACHED", f"Assigned after deadline {ticket.deadline:%a %d %b %H:%M}", actor)
    store.log(now, ticket, "REASSIGNED" if reassigning else "ASSIGNED",
              f"{member.name} ({member.id}): {ticket.reason}", actor)
    return member


def _queue_for_new_owner(store, ticket, now, note):
    """Take a ticket away from its owner and put it back in the queue for a new one."""
    ticket.previous_assignees.append(ticket.assigned_to)
    ticket.status = "PENDING"
    ticket.assigned_to = None
    ticket.reassign_pending = True
    ticket.handover_note = note


def _warn_if_uncovered(store, tickets, group, now):
    covered = bool(eligible_members(group, now, store.members, store.roster))
    if tickets and not covered:
        for t in tickets:
            store.log(now, t, "NO_COVERAGE",
                      f"No member of {group} is on shift: waits until someone's shift starts "
                      f"(escalate to the team lead)", "system")
    return covered


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
            _queue_for_new_owner(store, t, now, f"Reassigned from {member.name}")
            moved.append(t)
            store.log(now, t, "REASSIGN_QUEUED", f"{member.name} left ({reason}); needs a new owner", "system")

    store.log_event(now, "-", "MEMBER_LEFT",
                    f"{member.name} left at {now:%H:%M} ({reason}); {len(moved)} open ticket(s) handed over", "system")
    covered = _warn_if_uncovered(store, moved, member.group, now)
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


def transfer_ticket(store, ticket, member_id, now, reason="", actor="user"):
    """Hand an assigned ticket to another on-shift member of the same group, right away.
    A person decides this, so the least-loaded rule is not applied. The SLA is unchanged."""
    store.ensure_roster(now)
    if ticket.status != "ASSIGNED":
        raise AssignmentError("Only assigned tickets can be transferred")
    if member_id == ticket.assigned_to:
        raise AssignmentError("Ticket is already assigned to that member")
    member = _check_member(store, ticket, member_id, now)

    old = ticket.assigned_to
    ticket.previous_assignees.append(old)
    ticket.assigned_to = member.id
    store.last_assigned[member.id] = store.next_seq()
    note = f"Transferred from {_name(store, old)} to {member.name}" + (f": {reason}" if reason else "")
    ticket.reason = note
    store.log(now, ticket, "TRANSFERRED", f"{note} (by {actor})", actor)
    return member


def escalate_to_l2(store, ticket, now, reason="", actor="user"):
    """Send an assigned ticket to second-level support. It goes back to the queue under the
    L2 group and is given to an on-shift L2 member. The SLA is unchanged."""
    store.ensure_roster(now)
    if ticket.status != "ASSIGNED":
        raise AssignmentError("Only assigned tickets can be escalated to L2")
    if ticket.assignment_group == L2_GROUP:
        raise AssignmentError("Ticket is already with L2 support")

    old_group = ticket.assignment_group
    old_owner = _name(store, ticket.assigned_to)
    _queue_for_new_owner(store, ticket, now, f"Escalated to L2 from {old_owner} ({old_group})")
    ticket.escalated_from = ticket.escalated_from or old_group
    ticket.assignment_group = L2_GROUP
    ticket.level = "L2"
    store.log(now, ticket, "ESCALATED_L2",
              f"Escalated from {old_group} ({old_owner}) to L2 by {actor}" + (f": {reason}" if reason else ""), actor)
    covered = _warn_if_uncovered(store, [ticket], L2_GROUP, now)
    return {"ticket": ticket.number, "l2_covered": covered}


def tick(store, now=None):
    now = now or store.clock.now()
    store.ensure_roster(now)
    first_new = len(store.audit)
    release_due_tickets(store, now)
    load = store.load()

    pending = sorted(
        (t for t in store.tickets.values()
         if t.status == "PENDING" and t.sys_id not in store.in_flight),
        key=lambda t: t.opened_at,
    )

    for t in pending:
        candidates = eligible_members(t.assignment_group, now, store.members, store.roster)
        chosen = pick_least_loaded(candidates, load, store.last_assigned)
        if chosen is None:
            continue    # nobody on shift: the ticket waits

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