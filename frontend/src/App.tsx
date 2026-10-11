import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import type { AuditEntry, ClockInfo, Metrics, Ticket } from './api'
import Simulator from './Simulator'
import Board from './Board'
import Roster from './Roster'
import Insights from './Insights'
import './index.css'

type Tab = 'board' | 'roster' | 'log'
type Mode = 'rules' | 'agent'

export default function App() {
  const [clock, setClock] = useState<ClockInfo | null>(null)
  const [tickets, setTickets] = useState<Ticket[]>([])
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [audit, setAudit] = useState<AuditEntry[]>([])
  const [mode, setModeState] = useState<Mode>('rules')
  const [tab, setTab] = useState<Tab>('board')
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    try {
      const [c, t, m, a, md] = await Promise.all([
        api.clock(), api.tickets(), api.metrics(), api.audit(), api.mode(),
      ])
      setClock(c); setTickets(t); setMetrics(m); setAudit(a); setModeState(md.mode); setError('')
    } catch (e) {
      setError('Cannot reach the backend. Is uvicorn running on port 8000? ' + String(e))
    }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 2000)
    return () => clearInterval(id)
  }, [refresh])

  async function chooseMode(m: Mode) {
    try {
      const r = await api.setMode(m)
      setModeState(r.mode)
    } catch (e) {
      setError('Could not change mode: ' + String(e))
    }
  }

  const fallbacks = audit.filter((a) => a.event === 'AGENT_FALLBACK').length
  const agentAssigned = audit.filter((a) => a.event === 'ASSIGNED' && a.actor === 'agent').length

  const cards: [string, string | number][] = metrics
    ? [
        ['Total', metrics.total],
        ['Assigned', metrics.assigned],
        ['Ignored', metrics.ignored],
        ['Breached', metrics.breached],
        ['Within 30 min', metrics.within_sla_pct === null ? '-' : metrics.within_sla_pct + '%'],
        ['Avg assign time', metrics.avg_seconds_to_assign === null ? '-' : metrics.avg_seconds_to_assign + 's'],
      ]
    : []

  return (
    <div className="app">
      <header>
        <h1>ServiceNow Auto-Assigner <span className="poc">POC</span></h1>
        <div className="headright">
          <div className="modebox">
            <div className="label">Assignment mode</div>
            <div className="seg">
              <button className={mode === 'rules' ? 'active' : ''} onClick={() => chooseMode('rules')}>Rules</button>
              <button className={mode === 'agent' ? 'active' : ''} onClick={() => chooseMode('agent')}>AI agent</button>
            </div>
            <div className="small">
              {mode === 'agent'
                ? `Gemini agent via ADK (${agentAssigned} assigned, ${fallbacks} fallbacks)`
                : 'Deterministic least-loaded rules'}
            </div>
          </div>
          {clock && (
            <div className={'clockbox ' + (clock.in_service_hours ? 'in' : 'out')}>
              <div className="big">{clock.now.slice(11, 19)}</div>
              <div>{clock.weekday} {clock.now.slice(0, 10)}</div>
              <div className="small">{clock.in_service_hours ? 'In service window' : clock.holiday ? 'Company holiday' : 'Outside service window'}</div>
            </div>
          )}
        </div>
      </header>

      {error && <div className="error">{error}</div>}

      <div className="cards">
        {cards.map(([label, value]) => (
          <div className="card" key={label}>
            <div className="value">{value}</div>
            <div className="label">{label}</div>
          </div>
        ))}
      </div>

      <div className="layout">
        <aside>
          <Simulator clock={clock} onChange={refresh} />
        </aside>
        <main>
          <nav className="tabs">
            <button className={tab === 'board' ? 'active' : ''} onClick={() => setTab('board')}>Live board</button>
            <button className={tab === 'roster' ? 'active' : ''} onClick={() => setTab('roster')}>Roster</button>
            <button className={tab === 'log' ? 'active' : ''} onClick={() => setTab('log')}>Log and agent activity</button>
          </nav>
          {tab === 'board' && <Board tickets={tickets} />}
          {tab === 'roster' && <Roster clock={clock} />}
          {tab === 'log' && <Insights audit={audit} metrics={metrics} />}
        </main>
      </div>
    </div>
  )
}