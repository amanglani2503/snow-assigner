import { useState } from 'react'
import { GROUPS } from './api'
import type { Ticket } from './api'

const when = (iso: string) => iso.slice(5, 10) + ' ' + iso.slice(11, 16)

function countdown(t: Ticket) {
  if (t.status !== 'PENDING' || t.seconds_to_deadline === null) return '-'
  const s = t.seconds_to_deadline
  if (s < 0) return 'overdue'
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export default function Board({ tickets }: { tickets: Ticket[] }) {
  const [group, setGroup] = useState('')
  const [status, setStatus] = useState('')

  const rows = tickets.filter((t) => (!group || t.assignment_group === group) && (!status || t.status === status))

  return (
    <div className="panel">
      <div className="row">
        <select value={group} onChange={(e) => setGroup(e.target.value)}>
          <option value="">All groups</option>
          {GROUPS.map((g) => <option key={g}>{g}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          <option>PENDING</option>
          <option>ASSIGNED</option>
          <option>IGNORED</option>
        </select>
        <span className="hint">{rows.length} tickets</span>
      </div>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Ticket</th><th>Group</th><th>Description</th><th>Opened</th>
              <th>Deadline</th><th>Status</th><th>Assigned to</th><th>Countdown</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={8} className="empty">No tickets yet. Use the simulator.</td></tr>}
            {rows.map((t) => (
              <tr key={t.sys_id} title={t.reason}>
                <td>{t.number}</td>
                <td>{t.assignment_group}</td>
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
                    <div className="hint">reassigned from {t.previous_assignee_names.join(', ')}</div>
                  )}
                </td>
                <td>{countdown(t)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="hint">Hover a row to see why it was assigned (or ignored).</p>
    </div>
  )
}