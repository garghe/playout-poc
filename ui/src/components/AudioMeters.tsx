import { useEffect, useRef, useState } from "react";
import type { Meters } from "../types";

const MIN_DB = -60;
const SCALE = [0, -6, -12, -18, -24, -30, -40, -50, -60];

const pct = (db: number) => Math.max(0, Math.min(1, (db - MIN_DB) / -MIN_DB)) * 100;

function fmtLufs(v: number | null | undefined) {
  return v === null || v === undefined ? "–" : v.toFixed(1);
}

/** Stereo PPM-style bar meters (sample peak, dBFS) with peak hold + EBU R128 loudness. */
export function AudioMeters({ meters }: { meters: Meters | null }) {
  const [hold, setHold] = useState<[number, number]>([MIN_DB, MIN_DB]);
  const holdT = useRef<[number, number]>([0, 0]);

  useEffect(() => {
    if (!meters) return;
    const now = performance.now();
    setHold((h) => {
      const n: [number, number] = [h[0], h[1]];
      for (const ch of [0, 1] as const) {
        if (meters.peak[ch] >= n[ch] || now - holdT.current[ch] > 1500) {
          n[ch] = meters.peak[ch];
          holdT.current[ch] = now;
        }
      }
      return n;
    });
  }, [meters]);

  const peak = meters?.peak ?? [MIN_DB, MIN_DB];
  const rms = meters?.rms ?? [MIN_DB, MIN_DB];
  return (
    <section className="panel meters-panel">
      <div className="panel-title"><span>Audio</span><span className="muted">stereo · dBFS</span></div>
      <div className="meters">
        <div className="scale">
          {SCALE.map((d) => (
            <span key={d} style={{ bottom: `${pct(d)}%` }}>{d}</span>
          ))}
        </div>
        {(["L", "R"] as const).map((name, ch) => (
          <div className="meter" key={name}>
            <div className="meter-bar">
              <div className="meter-fill" style={{ clipPath: `inset(${100 - pct(peak[ch])}% 0 0 0)` }} />
              <div className="meter-rms" style={{ bottom: `${pct(rms[ch])}%` }} />
              <div className="meter-hold" style={{ bottom: `${pct(hold[ch])}%` }} />
            </div>
            <div className="meter-label">{name}</div>
            <div className="meter-val mono">{peak[ch] <= -119 ? "-∞" : peak[ch].toFixed(0)}</div>
          </div>
        ))}
        <div className="lufs">
          <div className="lufs-title">EBU R128</div>
          <div><span>M</span><b className="mono">{fmtLufs(meters?.lufs_m)}</b></div>
          <div><span>S</span><b className={`mono ${(meters?.lufs_s ?? -99) > -20 ? "hot" : ""}`}>{fmtLufs(meters?.lufs_s)}</b></div>
          <div><span>I</span><b className="mono">{fmtLufs(meters?.lufs_i)}</b></div>
          <div className="muted small">LUFS · target -23</div>
        </div>
      </div>
    </section>
  );
}
