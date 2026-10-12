import { useEffect, useState } from 'react'
import { api } from './api'
import type { ClockInfo, RosterData } from './api'

const LABEL: Record<string, string> = { M: 'M', E: 'E', OFF: '·', PL: 'PL', CH: 'CH', UL: 'UL', LEAVE: 'L' }
const WD = ['S', 'M', 'T', 'W', 'T', 'F', 'S']
const clamp = (v: string, lo: number, hi: number) => Math.min(hi, Math.max(lo, Number(v) || 0))

export default function Roster({ clock }: { clock: ClockInfo | null }) {
  const [month, setMonth] = useState(clock ? clock.now.slice(0, 7) : '2026-10')
  const [data, setData] = useState<RosterData | null>(null)
  const [error, setError] = useState('')
  const [pl, setPl] = useState('5')
  const [ch, setCh] = useState('5')
  const [hol, setHol] = useState('1')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.roster(month).then((d) => { setData(d); setError('') }).catch((e) => setError(String(e)))
  }, [month])

  async function randomize() {
    setBusy(true)
    try {
      setData(await api.generateRoster(month, clamp(pl, 0, 30) / 100, clamp(ch, 0, 30) / 100, clamp(hol, 0, 5)))
      setError('')
    } catch (e) {
      setError(String(e))
    }
    setBusy(false)
  }

  const today = clock ? clock.now.slice(0, 10) : ''

  return (
    <div className="panel">
      <div className="row">
        <input type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} />
        <span className="hint">
          <span className="cell M">M</span> 09:00-18:30 &nbsp;
          <span className="cell E">E</span> 12:30-22:00 &nbsp;
          <span className="cell OFF">·</span> off &nbsp;
          <span className="cell PL">PL</span> planned leave &nbsp;
          <span className="cell CH">CH</span> company holiday
          <span className="cell UL">UL</span> left during shift
        </span>
      </div>

      <div className="row randrow">
        <label className="hint">PL % <input type="number" min={0} max={30} value={pl} onChange={(e) => setPl(e.target.value)} style={{ width: 60 }} /></label>
        <label className="hint">CH % per location <input type="number" min={0} max={30} value={ch} onChange={(e) => setCh(e.target.value)} style={{ width: 60 }} /></label>
        <label className="hint">Company-wide holidays <input type="number" min={0} max={5} value={hol} onChange={(e) => setHol(e.target.value)} style={{ width: 60 }} /></label>
        <button disabled={busy} onClick={randomize} style={{ flex: 'none' }}>Randomize PL / CH</button>
      </div>

      {error && <div className="error">{error}</div>}
      {data && (
        <p className="hint">
          Company-wide holidays (all locations, not serviceable):{' '}
          <b>{data.org_holidays.length ? data.org_holidays.map((d) => d.slice(5)).join(', ') : 'none'}</b>.
          PL and location CH are only placed where every group keeps a morning and an evening person.
        </p>
      )}
      {data && (
        <div className="scroll">
          <table className="roster">
            <thead>
              <tr>
                <th className="sticky">Member</th>
                {data.days.map((d) => {
                  const dow = new Date(d + 'T00:00:00Z').getUTCDay()
                  const cls = (dow === 0 || dow === 6 ? 'wkend ' : '')
                    + (data.org_holidays.includes(d) ? 'holiday ' : '')
                    + (d === today ? 'today' : '')
                  return (
                    <th key={d} className={cls}>
                      <div>{WD[dow]}</div><div>{Number(d.slice(8))}</div>
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {data.members.map((m, i) => (
                <tr key={m.id} className={i > 0 && data.members[i - 1].group !== m.group ? 'groupstart' : ''}>
                  <td className="sticky"><b>{m.group}</b> {m.name} <span className="loc">{m.location}</span></td>
                  {data.days.map((d) => {
                    const code = data.roster[m.id]?.[d] ?? 'OFF'
                    return <td key={d} className={'cell ' + code + (d === today ? ' today' : '')}>{LABEL[code] ?? code}</td>
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}