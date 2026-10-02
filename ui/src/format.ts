const FPS = 25;

export function tc(secs: number | null | undefined, frames = true): string {
  if (secs === null || secs === undefined || !isFinite(secs)) return frames ? "--:--:--:--" : "--:--:--";
  const neg = secs < 0;
  const total = Math.round(Math.abs(secs) * FPS);
  const f = total % FPS;
  const s = Math.floor(total / FPS);
  const p = (n: number) => String(n).padStart(2, "0");
  const base = `${p(Math.floor(s / 3600))}:${p(Math.floor(s / 60) % 60)}:${p(s % 60)}`;
  return (neg ? "-" : "") + (frames ? `${base}:${p(f)}` : base);
}

/** Short countdown: M:SS under an hour, H:MM:SS otherwise. */
export function cd(secs: number): string {
  const s = Math.max(0, Math.ceil(secs));
  const p = (n: number) => String(n).padStart(2, "0");
  if (s >= 3600) return `${Math.floor(s / 3600)}:${p(Math.floor(s / 60) % 60)}:${p(s % 60)}`;
  return `${Math.floor(s / 60)}:${p(s % 60)}`;
}

export function clock(ts: number | null | undefined, tz: string, withFrames = false): string {
  if (!ts) return "--:--:--";
  const d = new Date(ts * 1000);
  const base = d.toLocaleTimeString("en-GB", { hour12: false, timeZone: tz });
  if (!withFrames) return base;
  const f = Math.floor((ts % 1) * FPS);
  return `${base}:${String(f).padStart(2, "0")}`;
}

export function kbps(v: number | undefined): string {
  if (v === undefined) return "-";
  return v >= 1000 ? `${(v / 1000).toFixed(2)} Mb/s` : `${Math.round(v)} kb/s`;
}

export function uptime(s: number): string {
  const h = Math.floor(s / 3600);
  const m = Math.floor(s / 60) % 60;
  return h ? `${h}h ${m}m` : `${m}m ${Math.floor(s % 60)}s`;
}
