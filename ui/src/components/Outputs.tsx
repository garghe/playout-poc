import { useEffect, useRef, useState } from "react";
import Hls from "hls.js";
import { kbps } from "../format";
import type { State } from "../types";

interface Urls { srt: string; hls: string | null }

/** Programme outputs: full SRT and HLS URLs, copy, open in VLC, and an in-browser HLS monitor. */
export function Outputs({ state }: { state: State | null }) {
  const [urls, setUrls] = useState<Urls>({ srt: `srt://127.0.0.1:9000`, hls: `${location.origin}/hls/master.m3u8` });
  const [play, setPlay] = useState(false);
  useEffect(() => {
    fetch("/api/output-url").then((r) => r.json()).then((d) => setUrls({ srt: d.srt, hls: d.hls })).catch(() => undefined);
  }, []);
  const o = state?.stats.output;
  const h = o?.hls;
  const hlsLive = !!h && h.age !== null && h.age < 20;

  return (
    <section className="panel outputs">
      <div className="panel-title"><span>Outputs</span><span className="muted small">open with VLC or any player</span></div>

      <OutRow label="SRT" url={urls.srt} m3u="/api/output.m3u?kind=srt"
        status={<>{o?.clients ? <span className="ok">● {o.clients} caller{o.clients > 1 ? "s" : ""}</span> : <span className="muted">○ no callers</span>} · {kbps(o?.kbps)}</>}
        hint="SRT listener (caller mode in VLC). MPEG-TS, H.264 1080p25 + AAC, SCTE-35 on PID 500." />

      {urls.hls && (
        <OutRow label="HLS" url={urls.hls} m3u="/api/output.m3u?kind=hls"
          status={<>
            {hlsLive ? <span className="ok">● live</span> : <span className="warnc">○ starting…</span>}
            {h?.last_duration ? ` · ${h.last_duration.toFixed(2)}s segs · seq ${h.last_seq}` : ""}
            {h?.in_break ? <span className="chip chip-scte" style={{ marginLeft: 6 }}>IN CUE-OUT</span> : null}
          </>}
          hint="FAST-ready HLS: 6 s segments, PROGRAM-DATE-TIME, SCTE-35 cues as CUE-OUT/IN and DATERANGE. CORS open."
          extra={<button className={`btn small ${play ? "warn active" : ""}`} onClick={() => setPlay((p) => !p)}>
            {play ? "■ Stop monitor" : "▶ Play here"}</button>} />
      )}
      {play && urls.hls && <HlsMonitor src="/hls/master.m3u8" />}
    </section>
  );
}

function OutRow({ label, url, m3u, status, hint, extra }: {
  label: string; url: string; m3u: string; status: React.ReactNode; hint: string; extra?: React.ReactNode;
}) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    const done = () => { setCopied(true); window.setTimeout(() => setCopied(false), 1500); };
    if (navigator.clipboard) navigator.clipboard.writeText(url).then(done).catch(() => undefined);
  };
  return (
    <div className="out-row">
      <div className="out-head">
        <span className={`chip out-${label.toLowerCase()}`}>{label}</span>
        <span className="small">{status}</span>
      </div>
      <input className="mono out-url" readOnly value={url} onFocus={(e) => e.target.select()} title={hint} />
      <div className="out-actions">
        <button className="btn small" onClick={copy}>{copied ? "copied ✓" : "Copy URL"}</button>
        <a className="btn small vlc" href={m3u} download title="Downloads a playlist file: double-click it to open in VLC">▶ Open in VLC</a>
        {extra}
      </div>
    </div>
  );
}

/** Plays the HLS output in the page (hls.js; Safari plays HLS natively) and shows the glass-to-glass delay. */
function HlsMonitor({ src }: { src: string }) {
  const video = useRef<HTMLVideoElement>(null);
  const [latency, setLatency] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const v = video.current!;
    let hls: Hls | null = null;
    if (Hls.isSupported()) {
      hls = new Hls({ liveSyncDurationCount: 3 });
      hls.on(Hls.Events.ERROR, (_e, d) => {
        if (!d.fatal) return;
        setErr(d.details === "manifestIncompatibleCodecsError"
          ? "this browser can't decode H.264/AAC (e.g. open-source Chromium). Use Chrome, Safari, Edge or VLC."
          : `${d.type}: ${d.details}`);
      });
      hls.loadSource(src);
      hls.attachMedia(v);
    } else if (v.canPlayType("application/vnd.apple.mpegurl")) {
      v.src = src;
    } else {
      setErr("HLS not supported in this browser");
    }
    v.play().catch(() => undefined);
    const id = window.setInterval(() => {
      const d = hls?.playingDate;
      setLatency(d ? (Date.now() - d.getTime()) / 1000 : null);
    }, 1000);
    return () => { window.clearInterval(id); hls?.destroy(); };
  }, [src]);
  return (
    <div className="hls-monitor">
      <video ref={video} muted controls playsInline />
      <div className="muted small">
        {err ? <span className="warnc">⚠ {err}</span>
          : latency !== null ? `HLS monitor · delay behind live ≈ ${latency.toFixed(1)} s (expected for 6 s segments)` : "loading…"}
      </div>
    </div>
  );
}
