import { useState } from 'react'
import type { AuditEntry, Metrics } from './api'

export default function Insights({ audit, metrics }: { audit: AuditEntry[]; metrics: Metrics | null }) {
  const [showTools, setShowTools] = useState(true)
  const [ticket, setTicket] = useState('')

  const max = Math.max(1, ...(metrics?.load_per_member.map((m) => m.open) ?? [1]))
  const q = ticket.trim().toLowerCase()
  const rows = audit.filter(
    (a) => (showTools || a.event !== 'TOOL_CALL') && (!q || a.ticket.toLowerCase().includes(q)),
  )

  return (
    <div>
      <div className="panel">
        <h2>Load per member</h2>
        {metrics?.load_per_member.map((m) => (
          <div className="loadrow" key={m.id}>
            <span className="loadname"><b>{m.group}</b> {m.name}</span>
            <span className="bar"><span style={{ width: `${(m.open / max) * 100}%` }} /></span>
            <span className="loadnum">{m.open}</span>
          </div>
        ))}
      </div>

      <div className="panel">
        <h2>Assignment log and agent activity</h2>
        <div className="row">
          <input
            placeholder="Filter by ticket, e.g. INC0010003"
            value={ticket} onChange={(e) => setTicket(e.target.value)}
            style={{ width: 240 }}
          />
          <label className="hint">
            <input type="checkbox" checked={showTools} onChange={(e) => setShowTools(e.target.checked)} />
            {' '}Show agent tool calls
          </label>
          <span className="hint">{rows.length} entries, newest first</span>
        </div>
        <div className="scroll">
          <table>
            <thead><tr><th>Time</th><th>Ticket</th><th>By</th><th>Event</th><th>Details</th></tr></thead>
            <tbody>
              {rows.length === 0 && <tr><td colSpan={5} className="empty">Nothing logged yet.</td></tr>}
              {rows.map((a, i) => (
                <tr key={i} className={a.event === 'TOOL_CALL' ? 'toolrow' : ''}>
                  <td>{a.ts.slice(5, 10)} {a.ts.slice(11, 19)}</td>
                  <td>{a.ticket}</td>
                  <td><span className={'actor ' + a.actor}>{a.actor}</span></td>
                  <td><span className={'badge ' + a.event}>{a.event.replace('_', ' ')}</span></td>
                  <td className="wrap">{a.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="hint">
          In AI agent mode, each ticket shows the agent's steps: reading the ticket, checking who is on shift,
          comparing open load, then the assignment. AGENT FALLBACK means Gemini failed and the rules assigned it instead.
        </p>
      </div>
    </div>
  )
}