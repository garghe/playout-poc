import { api } from "../api";
import { clock } from "../format";
import type { State } from "../types";

export function Alarms({ state, onError }: { state: State | null; onError: (e: unknown) => void }) {
  const alarms = state?.alarms ?? [];
  const checks = (state?.checks ?? []).filter((c) => c.level !== "info");
  const tz = state?.tz ?? "Europe/London";
  return (
    <section className="panel alarms">
      <div className="panel-title">
        <span>Alarms {alarms.length > 0 && <span className="count">{alarms.length}</span>}</span>
        {alarms.some((a) => a.key.startsWith("item:")) && (
          <button className="btn small ghost" onClick={() => api("/alarms/clear").catch(onError)}>Ack item alarms</button>
        )}
      </div>
      <ul className="alarm-list">
        {alarms.length === 0 && <li className="ok">✓ No active alarms</li>}
        {alarms.map((a) => (
          <li key={a.key} className={`al-${a.level}`}>
            <span className="mono">{clock(a.since, tz)}</span> {a.msg}
          </li>
        ))}
      </ul>
      <div className="panel-title sub"><span>Pre-air checks</span><span className="muted small">{checks.length} findings</span></div>
      <ul className="alarm-list checks">
        {checks.length === 0 && <li className="ok">✓ Playlist checks clean</li>}
        {checks.map((c, i) => (
          <li key={i} className={`al-${c.level}`}>
            <b>{c.title || c.uid}</b>: {c.message}
          </li>
        ))}
      </ul>
    </section>
  );
}
