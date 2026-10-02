import { useState } from "react";
import { api } from "../api";
import { cd, clock } from "../format";
import type { State } from "../types";

function urgency(rem: number) {
  if (rem <= 10) return "cd-red";
  if (rem <= 60) return "cd-amber";
  return "";
}

export function Countdowns({ state, now, onError }: { state: State | null; now: number; onError: (e: unknown) => void }) {
  // paused countdowns use the server's frozen value instead of ticking locally
  const cds = (state?.countdowns ?? []).map((c) => ({ ...c, rem: c.paused ? c.remaining : c.at - now }));
  const brk = cds.find((c) => c.key === "break");
  const rest = cds.filter((c) => c !== brk);
  const tz = state?.tz ?? "Europe/London";
  const [show, setShow] = useState(false);
  // while a break is on air, the big card counts down to the end of the break
  const cur = state?.events.find((e) => e.status === "on_air");
  const breakEnd = cur?.kind === "spot"
    ? Math.max(...state!.events.filter((e) => e.break_id === cur.break_id && e.status !== "skipped").map((e) => e.p_end ?? 0))
    : null;

  return (
    <section className="panel cd-panel">
      <div className="panel-title">
        <span>Countdowns</span>
        <button className="btn small" onClick={() => setShow((s) => !s)}>{show ? "Close" : "+ Manual"}</button>
      </div>
      {show && <AddCountdown live={state?.live_inputs ?? []} onDone={() => setShow(false)} onError={onError} />}
      {breakEnd ? (
        <div className={`cd-big in-break ${urgency(breakEnd - now)}`}>
          <div className="cd-label">IN BREAK · {cur?.break_title} · BACK IN</div>
          <div className="cd-time mono">{cd(breakEnd - now)}</div>
          <div className="muted small">programme resumes {clock(breakEnd, tz)}{brk ? ` · then ${brk.label.replace("Next break: ", "")} in ${cd(brk.rem)}` : ""}</div>
        </div>
      ) : (
        <div className={`cd-big ${brk && !brk.paused ? urgency(brk.rem) : ""} ${brk?.paused ? "paused" : ""}`}>
          <div className="cd-label">NEXT BREAK{brk?.paused ? " · PAUSED" : ""}</div>
          <div className="cd-time mono">{brk ? cd(brk.rem) : "—"}</div>
          <div className="muted small">{brk ? (brk.paused ? `${brk.label.replace("Next break: ", "")} · after ${state?.mode === "live" ? "live override" : "hold"}` : `${brk.label.replace("Next break: ", "")} · ${clock(brk.at, tz)}`) : "no break scheduled"}</div>
        </div>
      )}
      <ul className="cd-list">
        {rest.length === 0 && <li className="muted">No upcoming events</li>}
        {rest.map((c) => (
          <li key={c.key} className={`${urgency(c.rem)} ${c.fired ? "cd-fired" : ""}`}>
            <span className={`chip ${c.source === "manual" ? "chip-manual" : ""}`}>{c.category}</span>
            <span className="cd-name" title={c.label}>{c.label}</span>
            {c.action && c.action !== "none" && <span className="chip chip-action">{c.action}</span>}
            <span className="mono cd-at">{c.paused ? "paused" : clock(c.at, tz)}</span>
            <b className={`mono ${c.paused ? "paused" : ""}`} title={c.paused ? "frozen until you return to the playlist / release hold" : undefined}>
              {c.fired ? "NOW" : `${c.paused ? "⏸ " : ""}${cd(c.rem)}`}
            </b>
            {c.source === "manual" && (
              <button className="x" title="remove" onClick={() => api(`/countdowns/${c.key}`, undefined, "DELETE").catch(onError)}>×</button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function AddCountdown({ live, onDone, onError }: { live: string[]; onDone: () => void; onError: (e: unknown) => void }) {
  const [label, setLabel] = useState("Breaking news");
  const [category, setCategory] = useState("breaking news");
  const [mode, setMode] = useState<"in" | "at">("in");
  const [value, setValue] = useState("2:00");
  const [action, setAction] = useState("none");
  const [input, setInput] = useState(live[0] ?? "");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const body: Record<string, unknown> = { label, category, action, live_input: action === "go_live" ? input : null };
    if (mode === "in") {
      const parts = value.split(":").map(Number);
      const secs = parts.reduce((a, b) => a * 60 + b, 0);
      if (!isFinite(secs) || secs <= 0) return onError(new Error("duration like 2:00 or 90"));
      body.seconds = secs;
    } else body.at = value;
    try {
      await api("/countdowns", body);
      onDone();
    } catch (err) {
      onError(err);
    }
  };

  return (
    <form className="cd-form" onSubmit={submit}>
      <div className="presets">
        {[["Breaking news", "breaking news", "go_live"], ["Football KO", "football", "none"], ["Film start", "film", "none"]].map(([l, c, a]) => (
          <button type="button" className="btn small ghost" key={l} onClick={() => { setLabel(l); setCategory(c); setAction(a); }}>{l}</button>
        ))}
      </div>
      <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Label" required />
      <input value={category} onChange={(e) => setCategory(e.target.value)} placeholder="Category" />
      <div className="row">
        <select value={mode} onChange={(e) => setMode(e.target.value as "in" | "at")}>
          <option value="in">in (mm:ss)</option>
          <option value="at">at (HH:MM[:SS])</option>
        </select>
        <input value={value} onChange={(e) => setValue(e.target.value)} placeholder={mode === "in" ? "2:00" : "20:00"} className="mono" />
      </div>
      <div className="row">
        <select value={action} onChange={(e) => setAction(e.target.value)} title="what happens at zero">
          <option value="none">At zero: just alert</option>
          <option value="go_live">At zero: go live</option>
          <option value="take_next">At zero: take next</option>
          <option value="scte">At zero: SCTE out 60s</option>
        </select>
        {action === "go_live" && (
          <select value={input} onChange={(e) => setInput(e.target.value)}>
            {live.map((l) => <option key={l}>{l}</option>)}
          </select>
        )}
      </div>
      <button className="btn primary" type="submit">Start countdown</button>
    </form>
  );
}
