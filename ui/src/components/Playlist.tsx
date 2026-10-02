import { Fragment } from "react";
import { api } from "../api";
import { clock, tc } from "../format";
import type { PlayEvent, State } from "../types";

const STATUS_LABEL: Record<string, string> = {
  on_air: "ON AIR", next: "NEXT", done: "DONE", error: "ERROR", skipped: "SKIP",
  truncated: "CUT", short: "SHORT", interrupted: "INTR", stopped: "STOP",
};

export function Playlist({ state, now, onError }: { state: State | null; now: number; onError: (e: unknown) => void }) {
  const events = state?.events ?? [];
  const tz = state?.tz ?? "Europe/London";
  const issues = new Map<string, string[]>();
  for (const c of state?.checks ?? []) {
    if (c.uid && c.level !== "info") issues.set(c.uid, [...(issues.get(c.uid) ?? []), c.message]);
  }

  if (!events.length) {
    return (
      <section className="panel playlist">
        <div className="panel-title"><span>Playlist</span></div>
        <div className="empty">No playlist loaded. Use <b>Playlist → Load sample…</b> or <b>Import file…</b> above.</div>
      </section>
    );
  }

  return (
    <section className="panel playlist">
      <div className="panel-title">
        <span>Playlist · {state?.channel} · {state?.date}</span>
        <span className="muted">{events.length} events · times are projected ({tz})</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th style={{ width: 72 }}>Status</th>
              <th style={{ width: 84 }}>Start</th>
              <th style={{ width: 96 }}>Duration</th>
              <th>Title</th>
              <th style={{ width: 120 }}>Type</th>
              <th>Source</th>
              <th style={{ width: 120 }}></th>
            </tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <Fragment key={e.uid}>
                {e.break_first && <BreakHeader e={e} events={events} tz={tz} />}
                {e.gap_before > 1 && (
                  <tr className="gap-row"><td /><td colSpan={6}>⋯ filler slate {tc(e.gap_before, false)} before hard start</td></tr>
                )}
                <Row e={e} now={now} tz={tz} issues={issues.get(e.uid)} onError={onError} canStartHere={state?.mode === "stopped"} />
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function BreakHeader({ e, events, tz }: { e: PlayEvent; events: PlayEvent[]; tz: string }) {
  const spots = events.filter((x) => x.break_id === e.break_id);
  return (
    <tr className="break-row">
      <td><span className="chip chip-break">BREAK</span></td>
      <td className="mono">{clock(e.p_start, tz)}</td>
      <td className="mono">{tc(e.break_duration)}</td>
      <td colSpan={4}>
        <b>{e.break_title}</b> · {spots.length} spots {e.scte && <span className="chip chip-scte">SCTE-35</span>}
        {e.start_mode === "hard" && <span className="chip chip-hard">HARD {clock(e.hard_start, tz)}</span>}
      </td>
    </tr>
  );
}

function Row({ e, now, tz, issues, onError, canStartHere }: {
  e: PlayEvent; now: number; tz: string; issues?: string[]; onError: (e: unknown) => void; canStartHere: boolean;
}) {
  const live = e.status === "on_air";
  const rem = live && e.p_end ? e.p_end - now : null;
  return (
    <tr className={`row st-${e.status || "none"} ${e.kind === "spot" ? "spot" : ""} ${issues ? "has-issue" : ""}`}>
      <td><span className={`status st-${e.status || "none"}`}>{STATUS_LABEL[e.status] ?? ""}</span></td>
      <td className="mono">
        {e.start_mode === "hard" && !e.break_first && <span className="hard" title="hard start">⏱</span>}
        {clock(e.p_start, tz)}
      </td>
      <td className="mono">{live && rem !== null ? <span className="remain">-{tc(rem)}</span> : tc(e.planned)}</td>
      <td className="title-cell">
        <div className="title">{e.title}</div>
        {issues && <div className="issue">⚠ {issues.join(" · ")}</div>}
        {e.truncated_by > 0.04 && <div className="issue">✂ cut by {tc(e.truncated_by, false)} (next hard start)</div>}
      </td>
      <td>
        <span className={`chip kind-${e.kind}`}>{e.kind}</span>
        {e.category && e.kind !== "spot" && <span className="chip">{e.category}</span>}
      </td>
      <td className="src">
        <div className="mono small">{e.source}{e.in_point ? ` @${tc(e.in_point)}` : ""}</div>
        <div className="muted small">{e.media_info}{e.cued ? " · cued" : ""}</div>
      </td>
      <td className="actions">
        {canStartHere && e.status !== "on_air" && (
          <button className="btn small ghost" title="Start automation from this item"
            onClick={() => api("/control/start", { index: e.index }).catch(onError)}>▶ here</button>
        )}
        {e.status !== "on_air" && e.status !== "done" && (
          <button className="btn small ghost" onClick={() => api("/control/skip", { uid: e.uid }).catch(onError)}>
            {e.status === "skipped" ? "unskip" : "skip"}
          </button>
        )}
      </td>
    </tr>
  );
}
