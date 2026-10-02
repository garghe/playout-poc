import type { State } from "../types";
import { tc } from "../format";

export function Preview({ src, state, now }: { src: string | null; state: State | null; now: number }) {
  const onAir = state?.on_air;
  const remaining = onAir?.end ? onAir.end - now : null;
  const elapsed = onAir ? now - onAir.start : null;
  const progress = onAir?.end ? Math.min(1, Math.max(0, (now - onAir.start) / (onAir.end - onAir.start))) : 0;
  const nextIn = state?.next?.start ? state.next.start - now : null;
  const fallback = state?.fallback;
  return (
    <section className="panel preview-panel">
      <div className="panel-title">
        <span>Programme output</span>
        <span className="muted mono">{state?.stats.output.uri}</span>
      </div>
      <div className="preview">
        {src ? <img src={src} alt="Programme output preview" /> : <div className="no-signal">waiting for preview…</div>}
        <span className={`tally ${state?.mode === "stopped" ? "off" : "on"}`}>
          {state?.mode === "stopped" ? "OFF AIR" : "ON AIR"}
        </span>
        {fallback && <span className="fallback-badge">FALLBACK: {fallback}</span>}
      </div>
      <div className="onair">
        <div className="onair-row">
          <div className="onair-title">
            <span className="label">NOW</span>
            <strong>{onAir ? onAir.title : state?.pending_hard_start ? "Filler slate" : "—"}</strong>
            {onAir?.break_title && <span className="chip chip-break">{onAir.break_title}</span>}
            {onAir?.category && <span className="chip">{onAir.category}</span>}
          </div>
          <div className="onair-times mono">
            <span title="elapsed">{tc(elapsed)}</span>
            <span className="remain" title="remaining">{remaining !== null ? `-${tc(remaining)}` : "open"}</span>
          </div>
        </div>
        <div className="progress"><div style={{ width: `${progress * 100}%` }} /></div>
        <div className="onair-row next">
          <div className="onair-title">
            <span className="label">NEXT</span>
            <span>{state?.next ? state.next.title : "—"}</span>
          </div>
          <div className="mono">{nextIn !== null ? `in ${tc(nextIn, false)}` : ""}</div>
        </div>
      </div>
    </section>
  );
}
