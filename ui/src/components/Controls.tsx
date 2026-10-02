import { useEffect, useRef, useState } from "react";
import { api, uploadPlaylist } from "../api";
import type { State } from "../types";

export function Controls({ state, onError, onInfo }: { state: State | null; onError: (e: unknown) => void; onInfo: (m: string) => void }) {
  const mode = state?.mode ?? "stopped";
  const [samples, setSamples] = useState<string[]>([]);
  const [liveInput, setLiveInput] = useState("");
  const [scteDur, setScteDur] = useState("30");
  const [sctePre, setSctePre] = useState("0");
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api<string[]>("/samples", undefined, "GET").then(setSamples).catch(() => undefined);
  }, []);
  useEffect(() => {
    if (!liveInput && state?.live_inputs.length) setLiveInput(state.live_inputs[0]);
  }, [state?.live_inputs, liveInput]);

  const run = (path: string, body?: unknown, msg?: string) => () =>
    api(path, body ?? {}).then(() => msg && onInfo(msg)).catch(onError);

  const loaded = (r: unknown) => {
    const x = r as { events: number; errors: number; warnings: number };
    onInfo(`Playlist loaded: ${x.events} events, ${x.errors} errors, ${x.warnings} warnings`);
  };

  return (
    <section className="panel controls">
      <div className="ctl-group">
        <span className="ctl-label">Automation</span>
        <button className="btn go" disabled={!state?.events.length || mode === "auto"} onClick={run("/control/start")}>
          ▶ {mode === "hold" ? "Resume" : "Start"}
        </button>
        <button className="btn" disabled={!state?.events.length} onClick={run("/control/take")}>⏭ Take next</button>
        <button className={`btn ${mode === "hold" ? "warn active" : ""}`} disabled={mode !== "auto" && mode !== "hold"}
          onClick={run("/control/hold", { on: mode !== "hold" })}>⏸ Hold</button>
        <button className="btn danger" disabled={mode === "stopped"}
          onClick={() => confirm("Stop automation and put the slate on air?") && run("/control/stop")()}>■ Stop</button>
      </div>

      <div className="ctl-group">
        <span className="ctl-label">Live</span>
        <select value={liveInput} onChange={(e) => setLiveInput(e.target.value)}>
          {(state?.live_inputs ?? []).map((l) => {
            const s = state?.stats.live_inputs.find((x) => x.name === l);
            return <option key={l} value={l}>{l} {s?.signal ? "● signal" : "○ no signal"}</option>;
          })}
        </select>
        <button className="btn live" onClick={run("/control/live", { input: liveInput }, `Live override: ${liveInput}`)}>● Go live</button>
        <button className="btn" disabled={mode !== "live"} onClick={run("/control/return")}>↩ Return to playlist</button>
      </div>

      <div className="ctl-group">
        <span className="ctl-label">SCTE-35</span>
        <label className="mini">dur s<input className="mono num" value={scteDur} onChange={(e) => setScteDur(e.target.value)} /></label>
        <label className="mini">preroll s<input className="mono num" value={sctePre} onChange={(e) => setSctePre(e.target.value)} /></label>
        <button className="btn scte" onClick={run("/scte/out", { duration: Number(scteDur), preroll: Number(sctePre) }, "SCTE-35 splice OUT queued")}>Splice OUT</button>
        <button className="btn scte" onClick={run("/scte/in", { preroll: Number(sctePre) }, "SCTE-35 splice IN queued")}>Splice IN</button>
      </div>

      <div className="ctl-group">
        <span className="ctl-label">Playlist</span>
        <select defaultValue="" onChange={(e) => {
          const v = e.target.value;
          e.target.value = "";
          if (v) api(`/samples/${v}`).then(loaded).catch(onError);
        }}>
          <option value="" disabled>Load sample…</option>
          {samples.map((s) => <option key={s}>{s}</option>)}
        </select>
        <button className="btn" onClick={() => fileRef.current?.click()}>Import file…</button>
        <input ref={fileRef} type="file" accept=".json,.csv" hidden onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) uploadPlaylist(f).then(loaded).catch(onError);
        }} />
        <button className="btn" onClick={run("/checks", undefined, "Pre-air checks re-run")}>Re-check</button>
        <a className="btn" href="/api/asrun.csv" download>As-run ⤓</a>
      </div>
    </section>
  );
}
