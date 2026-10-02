import { useCallback, useState } from "react";
import { api, usePlayout, useNow } from "./api";
import { clock, tc } from "./format";
import { Alarms } from "./components/Alarms";
import { AudioMeters } from "./components/AudioMeters";
import { Controls } from "./components/Controls";
import { Countdowns } from "./components/Countdowns";
import { NerdLog } from "./components/NerdLog";
import { Playlist } from "./components/Playlist";
import { Preview } from "./components/Preview";

const MODE_LABEL = { stopped: "STOPPED", auto: "AUTO", hold: "HOLD", live: "LIVE OVERRIDE" } as const;

export default function App() {
  const { state, meters, log, preview, connected, clockOffset } = usePlayout();
  const now = useNow(clockOffset);
  const [toast, setToast] = useState<{ msg: string; err: boolean } | null>(null);

  const notify = useCallback((msg: string, err = false) => {
    setToast({ msg, err });
    window.setTimeout(() => setToast((t) => (t?.msg === msg ? null : t)), 4000);
  }, []);
  const onError = useCallback((e: unknown) => notify(e instanceof Error ? e.message : String(e), true), [notify]);
  const onInfo = useCallback((m: string) => notify(m), [notify]);

  const mode = state?.mode ?? "stopped";
  const alarmCount = state?.alarms.length ?? 0;
  const tech = state?.tech_slate ?? null;
  const toggleTech = () =>
    api("/control/tech", { on: !tech })
      .then(() => onInfo(tech ? "Technical difficulties slate released" : "Technical difficulties slate ON AIR"))
      .catch(onError);
  return (
    <div className={`app ${tech ? "tech-on" : ""}`}>
      <header className="topbar">
        <div className="brand">
          <span className="dot" />
          <b>PLAYOUT</b>
          <span className="muted">{state?.channel ?? "no playlist"}</span>
        </div>
        <span className={`mode mode-${mode}`}>{MODE_LABEL[mode]}</span>
        {state?.fallback && <span className="mode mode-fallback">FALLBACK</span>}
        <div className="spacer" />
        <button className={`tech-btn ${tech ? "active" : ""}`} onClick={toggleTech}
          title={tech ? "Release the slate and return to the scheduled output" : "Put the 'technical difficulties' slate on air immediately"}>
          {tech
            ? <>■ TECH SLATE ON AIR · {tc(now - tech.since, false)} · <u>RELEASE</u></>
            : <>⚠ TECHNICAL DIFFICULTIES</>}
        </button>
        {alarmCount > 0 && <span className="alarm-pill">⚠ {alarmCount} alarm{alarmCount > 1 ? "s" : ""}</span>}
        <span className={`conn ${connected ? "ok" : "bad"}`}
          title={connected ? "live updates over WebSocket" : "WebSocket unavailable: polling the API every second (no preview)"}>
          {connected ? "● connected" : state ? "○ polling (no live preview)" : "○ connecting…"}
        </span>
        <div className="clock mono" title={state?.tz}>{clock(now, state?.tz ?? "Europe/London", true)}</div>
      </header>

      <Controls state={state} onError={onError} onInfo={onInfo} />

      <main className="grid">
        <Preview src={preview} state={state} now={now} />
        <AudioMeters meters={meters} />
        <Countdowns state={state} now={now} onError={onError} />
        <Playlist state={state} now={now} onError={onError} />
        <div className="side">
          <Alarms state={state} onError={onError} />
          <NerdLog log={log} state={state} />
        </div>
      </main>

      {toast && <div className={`toast ${toast.err ? "err" : ""}`}>{toast.msg}</div>}
    </div>
  );
}
