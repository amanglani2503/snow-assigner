export const GROUPS = ['IDAHO', 'EMP', 'DBE', 'DB-LEGI1', 'DB-LEGI2', 'NGA']
export const SCENARIOS = [
  { id: 'normal', label: 'Normal', hint: '3 tickets at the current simulated time' },
  { id: 'burst', label: 'Burst', hint: 'Jumps to 14:00, 20 tickets (NGA by default)' },
  { id: 'out_of_hours', label: 'Out of hours', hint: 'Jumps to 23:15, ticket is ignored' },
  { id: 'weekend', label: 'Weekend', hint: 'Jumps to Saturday 11:00, ticket is ignored' },
  { id: 'holiday', label: 'Company holiday', hint: 'Jumps to the next company-wide holiday (generate one in the Roster tab first), ticket is ignored' },
  { id: 'edge_2150', label: 'Edge 21:50', hint: 'Jumps to Friday 21:50, deadline 22:20' },
  { id: 'duplicate', label: 'Duplicate', hint: 'Same ticket sent twice, one is rejected' },
]

export interface Ticket {
  sys_id: string
  number: string
  short_description: string
  priority: string
  assignment_group: string
  opened_at: string
  in_service_hours: boolean
  deadline: string | null
  status: 'PENDING' | 'ASSIGNED' | 'IGNORED'
  assigned_to: string | null
  assigned_at: string | null
  breached: boolean
  reason: string
  assigned_to_name: string | null
  seconds_to_deadline: number | null
  previous_assignee_names: string[]
  reassign_pending: boolean
}

export interface ClockInfo { now: string; weekday: string; in_service_hours: boolean; holiday: boolean }

export interface Metrics {
  total: number
  assigned: number
  pending: number
  ignored: number
  breached: number
  within_sla_pct: number | null
  avg_seconds_to_assign: number | null
  load_per_member: { id: string; name: string; group: string; open: number; code: string; on_shift: boolean }[]
}

export interface LeaveResult { member: string; group: string; tickets_queued: string[]; group_covered: boolean }

export interface AuditEntry { ts: string; ticket: string; event: string; detail: string; actor: string}

export interface RosterData {
  month: string
  days: string[]
  org_holidays: string[]
  members: { id: string; name: string; group: string; location: string }[]
  roster: Record<string, Record<string, string>>
}

export interface SimResult {
  scenario: string
  created: Ticket[]
  duplicates_rejected: number
  clock: string
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch('/api' + path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json() as Promise<T>
}

const post = <T>(path: string, body?: unknown) =>
  req<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  clock: () => req<ClockInfo>('/clock'),
  setClock: (time: string) => req<ClockInfo>('/clock', { method: 'PUT', body: JSON.stringify({ time }) }),
  tickets: () => req<Ticket[]>('/tickets?limit=300'),
  metrics: () => req<Metrics>('/metrics'),
  audit: () => req<AuditEntry[]>('/audit?limit=500'),
  mode: () => req<{ mode: 'rules' | 'agent' }>('/mode'),
  setMode: (mode: string) => req<{ mode: 'rules' | 'agent' }>('/mode', { method: 'PUT', body: JSON.stringify({ mode }) }),
  roster: (month: string) => req<RosterData>(`/roster/${month}`),
  simulate: (body: { scenario: string; count?: number; group?: string }) =>
  post<SimResult>('/tickets/simulate', body),
  reset: () => post<{ status: string }>('/reset'),
  generateRoster: (month: string, pl: number, ch: number, holidays: number) =>
  post<RosterData>(`/roster/${month}/generate?leave_prob=${pl}&ch_prob=${ch}&org_holidays=${holidays}`),
  memberLeaves: (id: string) => post<LeaveResult>(`/members/${id}/leave-now`),
  memberBack: (id: string) => post<{ status: string }>(`/members/${id}/back`),
}