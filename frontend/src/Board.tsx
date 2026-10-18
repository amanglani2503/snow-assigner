import { useState } from 'react'
import { api, GROUPS, L2_GROUP } from './api'
import type { Metrics, Ticket } from './api'

const when = (iso: string) => iso.slice(5, 10) + ' ' + iso.slice(11, 16)

function countdown(t: Ticket) {
  if (t.status === 'QUEUED' && t.release_at) return 'from ' + when(t.release_at)
  if (t.status !== 'PENDING' || t.seconds_to_deadline === null) return '-'
  const s = t.seconds_to_deadline
  if (s < 0) return 'overdue'
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

function Actions({ t, metrics, onChange, onMsg }: {
  t: Ticket; metrics: Metrics | null; onChange: () => void; onMsg: (m: string) => void
}) {
  const [target, setTarget] = useState('')
  const [busy, setBusy] = useState(false)
  if (t.status !== 'ASSIGNED') return null

  const options = (metrics?.load_per_member ?? []).filter(
    (m) => m.group === t.assignment_group && m.on_shift && m.id !== t.assigned_to,
  )

  async function run(fn: () => Promise<string>) {
    setBusy(true)
    try { onMsg(await fn()) } catch (e) { onMsg('Error: ' + String(e)) }
    setBusy(false)
    onChange()
  }

  return (
    <div className="actions">
      {options.length > 0 ? (
        <>
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">Transfer to...</option>
            {options.map((m) => <option key={m.id} value={m.id}>{m.name} ({m.open})</option>)}
          </select>
          <button className="small" disabled={busy || !target}
            onClick={() => run(async () => {
              const r = await api.transfer(t.sys_id, target)
              setTarget('')
              return `${t.number} transferred to ${r.assigned_to_name}`
            })}>
            Transfer
          </button>
        </>
      ) : <span className="hint">no one else on shift</span>}
      {t.assignment_group !== L2_GROUP && (
        <button className="small l2" disabled={busy}
          onClick={() => {
            if (window.confirm(`Escalate ${t.number} to L2 support?`)) {
              run(async () => {
                const r = await api.escalate(t.sys_id)
                return `${t.number} escalated to L2` + (r.l2_covered ? '' : ' (nobody in L2 is on shift, it will wait)')
              })
            }
          }}>
          Escalate to L2
        </button>
      )}
    </div>
  )
}

export default function Board({ tickets, metrics, onChange }: {
  tickets: Ticket[]; metrics: Metrics | null; onChange: () => void
}) {
  const [group, setGroup] = useState('')
  const [status, setStatus] = useState('')
  const [msg, setMsg] = useState('')

  const rows = tickets.filter((t) => (!group || t.assignment_group === group) && (!status || t.status === status))

  return (
    <div className="panel">
      <div className="row">
        <select value={group} onChange={(e) => setGroup(e.target.value)}>
          <option value="">All groups</option>
          {[...GROUPS, L2_GROUP].map((g) => <option key={g}>{g}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          <option>QUEUED</option>
          <option>PENDING</option>
          <option>ASSIGNED</option>
        </select>
        <span className="hint">{rows.length} tickets</span>
      </div>
      {msg && <p className="msg">{msg}</p>}
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Ticket</th><th>Group</th><th>Description</th><th>Opened</th>
              <th>Deadline</th><th>Status</th><th>Assigned to</th><th>Countdown</th><th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={9} className="empty">No tickets yet. Use the simulator.</td></tr>}
            {rows.map((t) => (
              <tr key={t.sys_id} title={t.reason}>
                <td>{t.number}</td>
                <td>
                  {t.assignment_group}
                  {t.level === 'L2' && <span className="badge L2">L2</span>}
                  {t.escalated_from && <div className="hint">from {t.escalated_from}</div>}
                </td>
                <td>{t.short_description}</td>
                <td>{when(t.opened_at)}</td>
                <td>{t.deadline ? when(t.deadline) : '-'}</td>
                <td>
                  <span className={'badge ' + t.status}>{t.status}</span>
                  {t.reassign_pending && <span className="badge REASSIGNING">REASSIGNING</span>}
                  {t.breached && <span className="badge BREACHED">BREACHED</span>}
                </td>
                <td>
                  {t.assigned_to_name ?? '-'}
                  {t.previous_assignee_names.length > 0 && (
                    <div className="hint">previously {t.previous_assignee_names.join(', ')}</div>
                  )}
                </td>
                <td>{countdown(t)}</td>
                <td><Actions t={t} metrics={metrics} onChange={onChange} onMsg={setMsg} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="hint">Hover a row to see why it was assigned or queued.</p>
    </div>
  )
}