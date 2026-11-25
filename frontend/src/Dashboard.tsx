import { useEffect, useMemo, useState } from 'react'
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api } from './api'
import type { Analytics, AuditEntry, BreachRow } from './api'

const COLORS = ['#3b5bdb', '#2f9e44', '#e8590c', '#7048e8', '#1098ad', '#c2255c', '#f08c00', '#495057']
const WD = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

const dur = (s: number | null | undefined) =>
  s == null ? '-' : s < 60 ? `${Math.round(s)}s`
  : s < 3600 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`
  : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
const pct = (v: number | null) => (v == null ? '-' : v + '%')

function Delta({ cur, prev, lowerBetter }: { cur: number | null; prev: number | null; lowerBetter?: boolean }) {
  if (cur == null || prev == null || cur === prev) return null
  const good = lowerBetter ? cur < prev : cur > prev
  const rel = prev !== 0 ? ` ${Math.round((Math.abs(cur - prev) / Math.abs(prev)) * 100)}%` : ''
  return <span className={'delta ' + (good ? 'good' : 'bad')}>{cur > prev ? '▲' : '▼'}{rel}</span>
}

function Kpi({ label, value, sub, cur, prev, lowerBetter, onClick }: {
  label: string; value: string | number; sub?: string
  cur?: number | null; prev?: number | null; lowerBetter?: boolean; onClick?: () => void
}) {
  return (
    <div className={'kpi' + (onClick ? ' clickable' : '')} onClick={onClick}>
      <div className="label">{label}</div>
      <div className="value">{value} {cur !== undefined && <Delta cur={cur} prev={prev ?? null} lowerBetter={lowerBetter} />}</div>
      {sub && <div className="hint">{sub}</div>}
    </div>
  )
}

function Chart({ title, children, hint }: { title: string; children: React.ReactNode; hint?: string }) {
  return (
    <div className="panel chartbox">
      <h2>{title}</h2>
      <div style={{ width: '100%', height: 260 }}>{children}</div>
      {hint && <p className="hint">{hint}</p>}
    </div>
  )
}

function exportCsv(rows: BreachRow[]) {
  const head = ['Ticket', 'Group', 'Priority', 'Assigned to', 'Breach types', 'Assignment over (s)',
    'Work over (s)', 'Active work (s)', 'Waited (s)', 'Opened', 'Status']
  const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`
  const lines = [head, ...rows.map((r) => [r.number, r.group, r.priority, r.member, r.types.join('+'),
    r.assign_over_seconds, r.work_over_seconds, r.work_seconds, r.waited_seconds, r.opened_at, r.status])]
    .map((r) => r.map(esc).join(','))
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' }))
  a.download = 'breached-tickets.csv'
  a.click()
  URL.revokeObjectURL(a.href)
}

interface PanelFilter { type: string; group: string; member: string }

function BreachPanel({ rows, init, onClose }: { rows: BreachRow[]; init: PanelFilter; onClose: () => void }) {
  const [f, setF] = useState(init)
  const [open, setOpen] = useState<BreachRow | null>(null)
  const [events, setEvents] = useState<AuditEntry[]>([])
  const [err, setErr] = useState('')

  const groups = [...new Set(rows.map((r) => r.group))].sort()
  const members = [...new Set(rows.map((r) => r.member).filter(Boolean))].sort()
  const shown = rows.filter((r) =>
    (f.type === 'all' || r.types.includes(f.type as 'assignment' | 'work'))
    && (!f.group || r.group === f.group) && (!f.member || r.member === f.member))

  async function showTimeline(r: BreachRow) {
    setOpen(r); setEvents([]); setErr('')
    try { setEvents((await api.timeline(r.sys_id)).events) } catch (e) { setErr(String(e)) }
  }

  return (
    <>
      <div className="overlay" onClick={onClose} />
      <div className="drawer">
        <div className="row">
          <h2 style={{ margin: 0, flex: 1 }}>Breached tickets ({shown.length})</h2>
          <button className="small" onClick={() => exportCsv(shown)} disabled={!shown.length}>Export CSV</button>
          <button className="small warn" onClick={onClose}>Close</button>
        </div>
        <div className="row">
          <select value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })}>
            <option value="all">All breach types</option>
            <option value="assignment">Assignment</option>
            <option value="work">Work</option>
          </select>
          <select value={f.group} onChange={(e) => setF({ ...f, group: e.target.value })}>
            <option value="">All groups</option>{groups.map((g) => <option key={g}>{g}</option>)}
          </select>
          <select value={f.member} onChange={(e) => setF({ ...f, member: e.target.value })}>
            <option value="">All members</option>{members.map((m) => <option key={m}>{m}</option>)}
          </select>
        </div>
        <div className="scroll" style={{ maxHeight: '45vh' }}>
          <table>
            <thead><tr><th>Ticket</th><th>Group</th><th>P</th><th>Member</th><th>Breach</th><th>Over by</th><th>Work / waited</th></tr></thead>
            <tbody>
              {shown.length === 0 && <tr><td colSpan={7} className="empty">No breached tickets for these filters.</td></tr>}
              {shown.map((r) => (
                <tr key={r.sys_id} className={'clickrow' + (open?.sys_id === r.sys_id ? ' sel' : '')}
                    onClick={() => showTimeline(r)} title={r.description}>
                  <td>{r.number}</td><td>{r.group}</td><td>{r.priority}</td><td>{r.member || '-'}</td>
                  <td>{r.types.map((t) => <span key={t} className="badge BREACHED">{t}</span>)}</td>
                  <td>{r.types.includes('assignment') && <div>assign +{dur(r.assign_over_seconds)}</div>}
                      {r.types.includes('work') && <div>work +{dur(r.work_over_seconds)}</div>}</td>
                  <td>{dur(r.work_seconds)} / {dur(r.waited_seconds)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {open && (
          <div className="timeline">
            <h3>Timeline: {open.number}</h3>
            {err && <div className="error">{err}</div>}
            {events.map((a, i) => (
              <div className="tl" key={i}>
                <span className="tltime">{a.ts.slice(5, 10)} {a.ts.slice(11, 19)}</span>
                <span className={'badge ' + a.event}>{a.event.replace('_', ' ')}</span>
                <span className="tldetail">{a.detail}</span>
              </div>
            ))}
          </div>
        )}
        {!open && <p className="hint">Click a ticket to see its timeline.</p>}
      </div>
    </>
  )
}

export default function Dashboard() {
  const [range, setRange] = useState('7d')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [group, setGroup] = useState('')
  const [priority, setPriority] = useState('')
  const [data, setData] = useState<Analytics | null>(null)
  const [err, setErr] = useState('')
  const [panel, setPanel] = useState<PanelFilter | null>(null)
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 }>({ key: 'breaches', dir: -1 })

  const query = useMemo(() => {
    const q = new URLSearchParams({ range })
    if (range === 'custom') { q.set('start', start); q.set('end', end) }
    if (group) q.set('group', group)
    if (priority) q.set('priority', priority)
    return q.toString()
  }, [range, start, end, group, priority])

  useEffect(() => {
    if (range === 'custom' && (!start || !end)) return
    let alive = true
    let timer: number
    const load = async () => {
      try { const d = await api.analytics(query); if (alive) { setData(d); setErr('') } }
      catch (e) { if (alive) setErr(String(e)) }
      if (alive) timer = window.setTimeout(load, 10000)
    }
    load()
    return () => { alive = false; window.clearTimeout(timer) }
  }, [query, range, start, end])

  const k = data?.kpis
  const p = data?.prev
  const heatMax = Math.max(1, ...(data?.heatmap.flat() ?? [1]))
  const sortedGroups = [...(data?.group_table ?? [])].sort((a, b) => {
    const x = (a as never)[sort.key] ?? -1, y = (b as never)[sort.key] ?? -1
    return (x > y ? 1 : x < y ? -1 : 0) * sort.dir
  })
  const th = (key: string, label: string) => (
    <th className="sortable" onClick={() => setSort({ key, dir: sort.key === key ? (-sort.dir as 1 | -1) : -1 })}>
      {label}{sort.key === key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
    </th>
  )
  const open = (type = 'all', g = '') => setPanel({ type, group: g, member: '' })

  return (
    <div>
      <div className="panel">
        <div className="row">
          {['today', '7d', '30d', 'custom'].map((r) => (
            <button key={r} className={'chip' + (range === r ? ' active' : '')} onClick={() => setRange(r)}>
              {r === 'today' ? 'Today' : r === '7d' ? 'Last 7 days' : r === '30d' ? 'Last 30 days' : 'Custom'}
            </button>
          ))}
          {range === 'custom' && (
            <>
              <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
              <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
            </>
          )}
          <select value={group} onChange={(e) => setGroup(e.target.value)}>
            <option value="">All groups</option>
            {(data?.all_groups ?? []).map((g) => <option key={g}>{g}</option>)}
          </select>
          <select value={priority} onChange={(e) => setPriority(e.target.value)}>
            <option value="">All priorities</option>
            {['1', '2', '3', '4'].map((x) => <option key={x} value={x}>P{x}</option>)}
          </select>
          <span className="hint">Refreshes every 10 s. Arrows compare with the previous period.</span>
        </div>
      </div>

      {err && <div className="error">{err}</div>}
      {!data && !err && <p className="hint">Loading...</p>}

      {data && k && p && (
        <>
          <div className="kpis">
            <Kpi label="Tickets received" value={k.received} sub={`${k.open} still open`} cur={k.received} prev={p.received} />
            <Kpi label="Assignment SLA" value={pct(k.assign_sla_pct)} sub="assigned within 30 min" cur={k.assign_sla_pct} prev={p.assign_sla_pct} />
            <Kpi label="Work SLA" value={pct(k.work_sla_pct)} sub="active work within limit" cur={k.work_sla_pct} prev={p.work_sla_pct} />
            <Kpi label="Auto-assigned" value={pct(k.auto_pct)} sub="assigned by the system" cur={k.auto_pct} prev={p.auto_pct} />
            <Kpi label="Assignment breaches" value={k.assign_breaches} sub="click to view" cur={k.assign_breaches} prev={p.assign_breaches} lowerBetter onClick={() => open('assignment')} />
            <Kpi label="Work breaches" value={k.work_breaches} sub="click to view" cur={k.work_breaches} prev={p.work_breaches} lowerBetter onClick={() => open('work')} />
            <Kpi label="Avg time to assign" value={dur(k.avg_assign_seconds)} cur={k.avg_assign_seconds} prev={p.avg_assign_seconds} lowerBetter />
            <Kpi label="Avg active work" value={dur(k.avg_work_seconds)} sub="closed tickets, waiting excluded" cur={k.avg_work_seconds} prev={p.avg_work_seconds} lowerBetter />
            <Kpi label="Avg waiting time" value={dur(k.avg_wait_seconds)} sub="waiting on customer / user" cur={k.avg_wait_seconds} prev={p.avg_wait_seconds} lowerBetter />
          </div>

          <div className="panel">
            <h2>At risk now <span className="hint">(live, ignores the date range)</span></h2>
            <div className="riskgrid">
              <div>
                <h3>Work limit over {'>'}{80}% used</h3>
                {data.at_risk.work.length === 0 && <p className="hint">None.</p>}
                {data.at_risk.work.map((r) => (
                  <div className="riskrow" key={r.sys_id}>
                    <b>{r.number}</b> {r.group} · {r.member}
                    <span className="bar"><span style={{ width: `${Math.min(100, r.used_pct)}%`, background: r.used_pct >= 95 ? '#e03131' : '#f08c00' }} /></span>
                    <span className="hint">{r.used_pct}% · {dur(r.remaining_seconds)} left</span>
                  </div>
                ))}
              </div>
              <div>
                <h3>Waiting for assignment, deadline close</h3>
                {data.at_risk.assign.length === 0 && <p className="hint">None.</p>}
                {data.at_risk.assign.map((r) => (
                  <div className="riskrow" key={r.sys_id}>
                    <b>{r.number}</b> {r.group}
                    <span className={'delta ' + (r.seconds_left < 0 ? 'bad' : 'good')}>
                      {r.seconds_left < 0 ? `overdue ${dur(-r.seconds_left)}` : `${dur(r.seconds_left)} left`}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="charts">
            <Chart title={`Ticket volume (${data.period.hourly ? 'per hour' : 'per day'})`}>
              <ResponsiveContainer>
                <BarChart data={data.volume}>
                  <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="label" fontSize={11} />
                  <YAxis allowDecimals={false} fontSize={11} /><Tooltip /><Legend />
                  {data.groups.map((g, i) => <Bar key={g} dataKey={g} stackId="v" fill={COLORS[i % COLORS.length]} />)}
                </BarChart>
              </ResponsiveContainer>
            </Chart>

            <Chart title="SLA compliance trend" hint={`Dashed line is the ${data.target}% target.`}>
              <ResponsiveContainer>
                <LineChart data={data.sla_trend}>
                  <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="label" fontSize={11} />
                  <YAxis domain={[0, 100]} unit="%" fontSize={11} /><Tooltip /><Legend />
                  <ReferenceLine y={data.target} stroke="#868e96" strokeDasharray="5 5" />
                  <Line type="monotone" dataKey="assignment" name="Assignment SLA" stroke="#3b5bdb" connectNulls />
                  <Line type="monotone" dataKey="work" name="Work SLA" stroke="#2f9e44" connectNulls />
                </LineChart>
              </ResponsiveContainer>
            </Chart>

            <Chart title="Ticket flow (current status)">
              <ResponsiveContainer>
                <BarChart data={data.flow} layout="vertical" margin={{ left: 20 }}>
                  <CartesianGrid strokeDasharray="3 3" /><XAxis type="number" allowDecimals={false} fontSize={11} />
                  <YAxis type="category" dataKey="name" fontSize={12} width={70} /><Tooltip />
                  <Bar dataKey="value" fill="#7048e8" />
                </BarChart>
              </ResponsiveContainer>
            </Chart>

            <Chart title="Active work vs waiting (avg minutes per group)" hint="Click a bar to see that group's breaches.">
              <ResponsiveContainer>
                <BarChart data={data.work_vs_wait}>
                  <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="group" fontSize={11} />
                  <YAxis unit="m" fontSize={11} /><Tooltip /><Legend />
                  <Bar dataKey="work_min" name="Active work" stackId="w" fill="#3b5bdb"
                       onClick={(d: any) => open('all', d.group)} cursor="pointer" />
                  <Bar dataKey="wait_min" name="Waiting" stackId="w" fill="#f08c00"
                       onClick={(d: any) => open('all', d.group)} cursor="pointer" />
                </BarChart>
              </ResponsiveContainer>
            </Chart>

            <Chart title="Time to assign (distribution)">
              <ResponsiveContainer>
                <BarChart data={data.assign_dist}>
                  <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="label" fontSize={11} />
                  <YAxis allowDecimals={false} fontSize={11} /><Tooltip />
                  <Bar dataKey="count" name="Tickets" fill="#1098ad" />
                </BarChart>
              </ResponsiveContainer>
            </Chart>

            <div className="panel chartbox">
              <h2>Arrival heatmap (weekday × hour, IST)</h2>
              <div className="heat">
                <span />
                {Array.from({ length: 24 }, (_, h) => <span key={h} className="heathead">{h}</span>)}
                {data.heatmap.map((row, d) => (
                  <div key={d} style={{ display: 'contents' }}>
                    <span className="heatlabel">{WD[d]}</span>
                    {row.map((n, h) => (
                      <span key={h} className="heatcell" title={`${WD[d]} ${h}:00, ${n} ticket(s)`}
                            style={{ background: n ? `rgba(59,91,219,${0.15 + 0.85 * (n / heatMax)})` : '#f1f3f5' }} />
                    ))}
                  </div>
                ))}
              </div>
              <p className="hint">Darker means more tickets. Use it to plan shift coverage.</p>
            </div>
          </div>

          <div className="panel">
            <h2>Group comparison <span className="hint">(click a row to open its breaches)</span></h2>
            <div className="scroll">
              <table>
                <thead>
                  <tr>{th('group', 'Group')}{th('received', 'Received')}{th('breaches', 'Breaches')}
                    {th('assign_sla_pct', 'Assignment SLA')}{th('work_sla_pct', 'Work SLA')}{th('avg_work_seconds', 'Avg work time')}</tr>
                </thead>
                <tbody>
                  {sortedGroups.length === 0 && <tr><td colSpan={6} className="empty">No tickets in this period.</td></tr>}
                  {sortedGroups.map((g) => (
                    <tr key={g.group} className="clickrow" onClick={() => open('all', g.group)}>
                      <td><b>{g.group}</b></td><td>{g.received}</td>
                      <td>{g.breaches > 0 ? <span className="badge BREACHED">{g.breaches}</span> : 0}</td>
                      <td>{pct(g.assign_sla_pct)}</td><td>{pct(g.work_sla_pct)}</td><td>{dur(g.avg_work_seconds)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {panel && <BreachPanel rows={data.breaches} init={panel} onClose={() => setPanel(null)} />}
        </>
      )}
    </div>
  )
}