import { useState } from 'react'
import { api } from './api'
import type { AuditEntry, Metrics } from './api'

export default function Insights({ audit, metrics, onChange }: {
  audit: AuditEntry[]; metrics: Metrics | null; onChange: () => void
}) {
  const [showTools, setShowTools] = useState(true)
  const [ticket, setTicket] = useState('')
  const [msg, setMsg] = useState('')

  const max = Math.max(1, ...(metrics?.load_per_member.map((m) => m.open) ?? [1]))
  const q = ticket.trim().toLowerCase()
  const rows = audit.filter(
    (a) => (showTools || a.event !== 'TOOL_CALL') && (!q || a.ticket.toLowerCase().includes(q)),
  )

  async function leave(id: string, name: string) {
    try {
      const r = await api.memberLeaves(id)
      setMsg(`${name} left. ${r.tickets_queued.length} ticket(s) handed over.`
        + (r.group_covered ? '' : ' Nobody else in the group is on shift: they wait for the next shift.'))
    } catch (e) {
      setMsg('Error: ' + String(e))
    }
    onChange()
  }

  async function back(id: string, name: string) {
    try {
      await api.memberBack(id)
      setMsg(`${name} is back on shift.`)
    } catch (e) {
      setMsg('Error: ' + String(e))
    }
    onChange()
  }

  return (
    <div>
      <div className="panel">
        <h2>Load per member</h2>
        {metrics?.load_per_member.map((m) => (
          <div className="loadrow" key={m.id}>
            <span className={'shiftdot ' + (m.on_shift ? 'on' : 'off')}
              title={m.on_shift ? 'On shift now' : 'Not on shift (' + m.code + ')'} />
            <span className="loadname"><b>{m.group}</b> {m.name}</span>
            <span className="bar"><span style={{ width: `${(m.open / max) * 100}%` }} /></span>
            <span className="loadnum">{m.open}</span>
            <span className="loadaction">
              {m.code === 'UL'
                ? <button className="small" onClick={() => back(m.id, m.name)}>Back on shift</button>
                : m.on_shift && <button className="small warn" onClick={() => leave(m.id, m.name)}>Leaves now</button>}
            </span>
          </div>
        ))}
        {msg && <p className="msg">{msg}</p>}
        <p className="hint">
          "Leaves now" removes the member from today's shift and hands their open tickets to someone else on shift.
        </p>
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
          In AI agent mode, each ticket shows the agent's steps. AGENT FALLBACK means Gemini failed and the rules decided instead.
        </p>
      </div>
    </div>
  )
}