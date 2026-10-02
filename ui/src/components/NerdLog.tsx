import { useEffect, useMemo, useRef, useState } from "react";
import { clock, kbps, uptime } from "../format";
import type { LogEvent, State } from "../types";

const CATS = ["SOURCE", "SCTE35", "BREAK", "SRT-IN", "SRT-OUT", "FALLBACK", "ALARM", "PREROLL", "CONTROL", "ASRUN", "COUNTDOWN", "PLAYLIST"];

/** "Stats for nerds": live engine numbers + a filterable, auto-scrolling event log. */
export function NerdLog({ log, state }: { log: LogEvent[]; state: State | null }) {
  const [off, setOff] = useState<Set<string>>(new Set(["PREROLL"]));
  const [follow, setFollow] = useState(true);
  const box = useRef<HTMLDivElement>(null);
  const tz = state?.tz ?? "Europe/London";

  const rows = useMemo(() => log.filter((e) => !off.has(e.cat)), [log, off]);
  useEffect(() => {
    if (follow && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [rows, follow]);

  const o = state?.stats.output;
  const srt = o?.srt ?? {};
  return (
    <section className="panel nerd">
      <div className="panel-title">
        <span>Stats for nerds</span>
        <label className="muted small"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> follow</label>
      </div>
      <div className="stats-grid mono">
        <Stat k="out" v={`${kbps(o?.kbps)} · ${o?.fps ?? 0} fps`} />
        <Stat k="srt callers" v={String(o?.clients ?? 0)} warn={(o?.clients ?? 0) === 0} />
        <Stat k="srt rtt / loss" v={srt["rtt-ms"] !== undefined ? `${srt["rtt-ms"]} ms / ${srt["packets-sent-lost"] ?? 0}` : "-"} />
        <Stat k="repeat / underrun" v={`${o?.repeated_frames ?? 0} / ${o?.audio_underruns ?? 0}`} />
        <Stat k="luma" v={String(o?.luma ?? "-")} />
        <Stat k="uptime" v={o ? uptime(o.uptime) : "-"} />
        <Stat k="cued" v={state?.stats.engine.prerolled.join(", ") || "-"} />
        <Stat k="scte pid" v={String(o?.scte_pid ?? "-")} />
        {state?.stats.live_inputs.map((l) => (
          <Stat key={l.name} k={l.name} warn={!l.signal}
            v={`${l.signal ? "●" : "○"} ${kbps(l.kbps)}${l["rtt-ms"] !== undefined ? ` · rtt ${l["rtt-ms"]}ms` : ""}${l["packets-received-lost"] ? ` · lost ${l["packets-received-lost"]}` : ""}${l.on_air ? " · ON AIR" : ""}`} />
        ))}
      </div>
      <div className="filters">
        {CATS.map((c) => (
          <button key={c} className={`flt ${off.has(c) ? "off" : ""} cat-${c}`}
            onClick={() => setOff((s) => { const n = new Set(s); if (n.has(c)) n.delete(c); else n.add(c); return n; })}>{c}</button>
        ))}
      </div>
      <div className="log mono" ref={box} onWheel={() => box.current && setFollow(box.current.scrollTop + box.current.clientHeight >= box.current.scrollHeight - 4)}>
        {rows.map((e) => (
          <div key={e.seq} className={`log-line lvl-${e.level}`}>
            <span className="ts">{clock(e.ts, tz, true)}</span>
            <span className={`cat cat-${e.cat}`}>{e.cat}</span>
            <span className="msg">{e.msg}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function Stat({ k, v, warn }: { k: string; v: string; warn?: boolean }) {
  return (
    <div className={`stat ${warn ? "warn" : ""}`}>
      <span>{k}</span>
      <b title={v}>{v}</b>
    </div>
  );
}
