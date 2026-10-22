import { useState } from "react";
import { api, GROUPS, SCENARIOS } from "./api";
import type { ClockInfo } from "./api";

const pad = (n: number) => String(n).padStart(2, "0");

// Next date after today (taken from the simulated clock) that falls on `weekday` (Mon=1 ... Sun=0).
function nextWeekday(nowIso: string, weekday: number, h: number, m: number) {
    const [y, mo, d] = nowIso.slice(0, 10).split("-").map(Number);
    const dt = new Date(Date.UTC(y, mo - 1, d));
    do {
        dt.setUTCDate(dt.getUTCDate() + 1);
    } while (dt.getUTCDay() !== weekday);
    return `${dt.toISOString().slice(0, 10)}T${pad(h)}:${pad(m)}:00`;
}

// Next working day (Mon-Fri) after the simulated date, at h:m. Use it to release queued tickets.
function nextWorkday(nowIso: string, h: number, m: number) {
    const [y, mo, d] = nowIso.slice(0, 10).split("-").map(Number);
    const dt = new Date(Date.UTC(y, mo - 1, d));
    do {
        dt.setUTCDate(dt.getUTCDate() + 1);
    } while (dt.getUTCDay() === 0 || dt.getUTCDay() === 6);
    return `${dt.toISOString().slice(0, 10)}T${pad(h)}:${pad(m)}:00`;
}

export default function Simulator({
    clock,
    onChange,
}: {
    clock: ClockInfo | null;
    onChange: () => void;
}) {
    const [picked, setPicked] = useState("2026-10-12T10:00");
    const [group, setGroup] = useState("");
    const [count, setCount] = useState("");
    const [msg, setMsg] = useState("");
    const [busy, setBusy] = useState(false);

    async function run(fn: () => Promise<string>) {
        setBusy(true);
        try {
            setMsg(await fn());
        } catch (e) {
            setMsg("Error: " + String(e));
        }
        setBusy(false);
        onChange();
    }

    const setTime = (t: string) =>
        run(async () => {
            const c = await api.setClock(t);
            return `Clock set to ${c.weekday} ${c.now.slice(0, 16).replace("T", " ")}`;
        });

    return (
        <div className="panel">
            <h2>Simulator</h2>

            <h3>Time travel</h3>
            <input
                type="datetime-local"
                value={picked}
                onChange={(e) => setPicked(e.target.value)}
            />
            <div className="row">
                <button disabled={busy} onClick={() => setTime(picked + ":00")}>
                    Set time
                </button>
                <button
                    disabled={busy || !clock}
                    onClick={() =>
                        clock && setTime(nextWeekday(clock.now, 1, 10, 0))
                    }
                >
                    Next Mon 10:00
                </button>
            </div>
            <div className="row">
                <button
                    disabled={busy || !clock}
                    onClick={() =>
                        clock && setTime(nextWeekday(clock.now, 1, 9, 0))
                    }
                >
                    Next Mon 09:00
                </button>
                <button
                    disabled={busy || !clock}
                    onClick={() =>
                        clock && setTime(nextWeekday(clock.now, 6, 11, 0))
                    }
                >
                    Next Sat 11:00
                </button>
            </div>
            <div className="row">
                    <button
                        disabled={busy || !clock}
                        onClick={() =>
                            clock && setTime(nextWorkday(clock.now, 9, 0))
                        }
                    >
                        Next workday 09:00
                    </button>
                </div>

            <h3>Generate tickets</h3>
            <div className="row">
                <select
                    value={group}
                    onChange={(e) => setGroup(e.target.value)}
                >
                    <option value="">Random group</option>
                    {GROUPS.map((g) => (
                        <option key={g}>{g}</option>
                    ))}
                </select>
                <input
                    type="number"
                    min={1}
                    max={100}
                    placeholder="count"
                    value={count}
                    onChange={(e) => setCount(e.target.value)}
                    style={{ width: 70 }}
                />
            </div>
            <div className="scenarios">
                {SCENARIOS.map((s) => (
                    <button
                        key={s.id}
                        disabled={busy}
                        title={s.hint}
                        onClick={() =>
                            run(async () => {
                                const r = await api.simulate({
                                    scenario: s.id,
                                    group: group || undefined,
                                    count: count ? Number(count) : undefined,
                                });
                                return `${s.label}: ${r.created.length} created, ${r.duplicates_rejected} duplicate rejected. Clock: ${r.clock.slice(0, 16).replace("T", " ")}`;
                            })
                        }
                    >
                        {s.label}
                    </button>
                ))}
            </div>
            <p className="hint">
                Hover a button for what it does. Count applies to Normal and
                Burst.
            </p>

            <button
                className="danger"
                disabled={busy}
                onClick={() =>
                    run(async () => {
                        await api.reset();
                        return "All data cleared, clock back to real time";
                    })
                }
            >
                Reset everything
            </button>

            {msg && <p className="msg">{msg}</p>}
        </div>
    );
}
