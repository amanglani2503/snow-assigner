import logging
from datetime import datetime, timedelta, timezone
from app.tickets.work import apply_state, classify
from app import config
from app.roster.models import Member
from app.rules.clock import IST
from app.rules.sla import SLA_MINUTES
from app.snow.client import SnowError
from app.tickets.generator import queued_reason
from app.tickets.models import Ticket

log = logging.getLogger("snow-assigner")

_client = None
def get_client():
    global _client
    if _client is None:
        from app.snow.client import ServiceNowClient
        _client = ServiceNowClient()
    return _client

def parse_snow_time(s: str) -> datetime:
    """ServiceNow returns 'YYYY-MM-DD HH:MM:SS' in UTC."""
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone(IST)


async def sync_members(store):
    client, members, seen = get_client(), [], set()
    for group in config.SNOW_GROUPS:
        for u in await client.get_group_members(group):
            if u["id"] and u["id"] not in seen:
                seen.add(u["id"])
                members.append(Member(id=u["id"], name=u["name"] or u["id"], group=group))
    if members:
        store.set_members(members)
        store.ensure_roster(store.clock.now())
        log.info("loaded %d members from ServiceNow", len(members))


# def ticket_from_snow(store, r, group) -> Ticket:
#     opened = parse_snow_time(r["opened_at"])
#     in_hours = store.is_serviceable(opened)
#     release = None if in_hours else store.next_service_start(opened)
#     start = opened if in_hours else release
#     t = Ticket(sys_id=r["sys_id"], number=r["number"], short_description=r["short_description"],
#                priority=str(r["priority"]), assignment_group=group, opened_at=opened,
#                in_service_hours=in_hours, deadline=start + timedelta(minutes=SLA_MINUTES),
#                status="PENDING" if in_hours else "QUEUED", release_at=release)
#     if r.get("assigned_to"):
#         t.status, t.assigned_to = "ASSIGNED", r["assigned_to"]
#         t.assigned_at = store.clock.now()
#         t.reason = "Already assigned in ServiceNow"
#     elif not in_hours:
#         t.reason = queued_reason(store, opened, release)
#     return t
#
#
# async def sync_tickets(store):
#     if not store.members:
#         return                      # members first, or the service-window check is meaningless
#     client = get_client()
#     for group in config.SNOW_GROUPS:
#         try:
#             rows = await client.list_active_for_group(group)
#         except SnowError as e:
#             log.warning("ticket sync failed for %s: %s", group, e)
#             continue
#         now = store.clock.now()
#         pending_out = {o["sys_id"] for o in store.outbox}   # computed after the await
#         seen = set()
#         for r in rows:
#             sid = r["sys_id"]
#             seen.add(sid)
#             t = store.tickets.get(sid)
#             if t is None:
#                 t = ticket_from_snow(store, r, group)
#                 store.add_ticket(t)
#                 store.log(now, t, "INGESTED", f"Pulled from ServiceNow ({group}), status {t.status}", "system")
#                 if t.status == "QUEUED":
#                     store.log(now, t, "QUEUED", t.reason)
#                 continue
#             if sid in pending_out or t.reassign_pending or sid in store.in_flight:
#                 continue
#             snow_owner = r.get("assigned_to") or None
#             if snow_owner and snow_owner != t.assigned_to:
#                 store.log(now, t, "EXTERNAL_CHANGE",
#                           f"Owner changed in ServiceNow to {snow_owner}", "system")
#                 t.assigned_to, t.status = snow_owner, "ASSIGNED"
#         for t in store.tickets.values():     # gone from the active list = resolved/closed
#             if (t.assignment_group == group and t.sys_id not in seen
#                     and t.status in ("ASSIGNED", "PENDING", "QUEUED")
#                     and t.sys_id not in pending_out and t.sys_id not in store.in_flight):
#                 t.status = "CLOSED"
#                 store.log(now, t, "CLOSED", "No longer active in ServiceNow", "system")

def raw(f):
    return f.get("value") if isinstance(f, dict) else f

def disp(f):
    return (f.get("display_value") if isinstance(f, dict) else f) or ""

def snow_time(f):
    v = raw(f)
    return parse_snow_time(v) if v else None


def ticket_from_snow(store, r, group) -> Ticket:
    opened = snow_time(r["opened_at"])
    in_hours = store.is_serviceable(opened)
    release = None if in_hours else store.next_service_start(opened)
    start = opened if in_hours else release
    owner = raw(r.get("assigned_to")) or None
    label = disp(r.get(config.SNOW_STATE_FIELD))
    t = Ticket(sys_id=raw(r["sys_id"]), number=raw(r["number"]),
               short_description=raw(r["short_description"]) or "",
               priority=str(raw(r["priority"])), assignment_group=group, opened_at=opened,
               in_service_hours=in_hours, deadline=start + timedelta(minutes=SLA_MINUTES),
               status="PENDING" if in_hours else "QUEUED", release_at=release,
               snow_state=label, state_category=classify(label, assigned=bool(owner)))
    if owner:
        t.status, t.assigned_to, t.external = "ASSIGNED", owner, True
        t.assigned_at = store.clock.now()
        t.reason = "Already assigned in ServiceNow"
    elif not in_hours:
        t.reason = queued_reason(store, opened, release)
    return t


async def sync_tickets(store):
    if not store.members:
        return
    client = get_client()
    for group in config.SNOW_GROUPS:
        try:
            rows = await client.list_active_for_group(group)
        except SnowError as e:
            log.warning("ticket sync failed for %s: %s", group, e)
            continue
        now = store.clock.now()
        pending_out = {o["sys_id"] for o in store.outbox}
        seen = set()
        for r in rows:
            sid = raw(r["sys_id"])
            seen.add(sid)
            label = disp(r.get(config.SNOW_STATE_FIELD))
            t = store.tickets.get(sid)
            if t is None:
                if classify(label, assigned=bool(raw(r.get("assigned_to")))) == "DONE":
                    continue                      # already finished before we saw it
                t = ticket_from_snow(store, r, group)
                store.add_ticket(t)
                store.log(now, t, "INGESTED", f"Pulled from ServiceNow ({group}), state '{label}'", "system")
                if t.status == "QUEUED":
                    store.log(now, t, "QUEUED", t.reason)
                continue
            apply_state(store, t, label, now)     # track waiting / done transitions
            if sid in pending_out or t.reassign_pending or sid in store.in_flight:
                continue
            owner = raw(r.get("assigned_to")) or None
            if owner and owner != t.assigned_to:
                store.log(now, t, "EXTERNAL_CHANGE", f"Owner changed in ServiceNow to {owner}", "system")
                t.assigned_to, t.status, t.external = owner, "ASSIGNED", True
                t.assigned_at = t.assigned_at or now

        # Tickets that left the active list are finished: read the final state and close time.
        for t in list(store.tickets.values()):
            if (t.assignment_group != group or t.sys_id in seen
                    or t.status not in ("ASSIGNED", "PENDING", "QUEUED")
                    or t.sys_id in pending_out or t.sys_id in store.in_flight):
                continue
            try:
                r = await client.get_incident_state(t.sys_id)
            except SnowError:
                continue
            closed = snow_time(r.get("closed_at")) or snow_time(r.get("resolved_at"))
            apply_state(store, t, disp(r.get(config.SNOW_STATE_FIELD)), now, closed)
            if t.status != "CLOSED":              # inactive in ServiceNow, so it is finished
                apply_state(store, t, "closed", now, closed)
            store.log(now, t, "CLOSED", f"Closed in ServiceNow (state '{t.snow_state}')", "system")


async def flush_outbox(store):
    """Write assignments back to ServiceNow. Retries, then reverts the ticket."""
    client = get_client()
    for item in list(store.outbox):
        try:
            await client.assign(item["sys_id"], item["member_id"], item["note"], actor="auto-assigner")
            store.outbox.remove(item)
        except SnowError as e:
            item["attempts"] += 1
            t = store.tickets.get(item["sys_id"])
            if t is not None:
                store.log(store.clock.now(), t, "SNOW_WRITE_FAILED",
                          f"attempt {item['attempts']}: {e}", "system")
            if item["attempts"] >= 5:
                store.outbox.remove(item)
                if t is not None and t.status == "ASSIGNED":
                    t.status, t.assigned_to = "PENDING", None   # try again from scratch