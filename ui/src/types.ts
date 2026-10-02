export interface PlayEvent {
  index: number;
  uid: string;
  id: string;
  title: string;
  kind: "programme" | "live" | "spot";
  source: string;
  category: string;
  start_mode: "follow" | "hard";
  hard_start: number | null;
  duration: number | null;
  planned: number | null;
  in_point: number;
  break_id: string | null;
  break_title: string | null;
  break_first: boolean;
  break_last: boolean;
  break_duration: number | null;
  scte: boolean;
  media_duration: number | null;
  media_info: string;
  status: "" | "on_air" | "next" | "done" | "error" | "skipped" | "truncated" | "short" | "interrupted" | "stopped";
  cued: boolean;
  p_start: number | null;
  p_end: number | null;
  gap_before: number;
  truncated_by: number;
}

export interface Countdown {
  key: string;
  label: string;
  category: string;
  at: number;
  remaining: number;
  source: "playlist" | "manual";
  action?: string;
  fired?: boolean;
}

export interface Alarm {
  key: string;
  level: "warning" | "error";
  msg: string;
  since: number;
}

export interface Check {
  level: "error" | "warning" | "info";
  uid: string | null;
  title: string;
  message: string;
}

export interface Meters {
  peak: [number, number];
  rms: [number, number];
  lufs_m: number | null;
  lufs_s: number | null;
  lufs_i: number | null;
}

export interface LiveInputStats {
  name: string;
  uri: string;
  signal: boolean;
  on_air: boolean;
  kbps: number;
  callers?: number;
  "rtt-ms"?: number;
  "packets-received-lost"?: number;
  "packets-received"?: number;
  "receive-rate-mbps"?: number;
  gateway_restarts?: number;
}

export interface State {
  now: number;
  mode: "stopped" | "auto" | "hold" | "live";
  channel: string | null;
  date: string | null;
  tz: string;
  on_air: {
    uid: string;
    title: string;
    kind: string;
    category: string;
    break_title?: string | null;
    start: number;
    end: number | null;
    elapsed: number;
    remaining: number | null;
    source: string;
    orphan?: boolean;
  } | null;
  next: { uid: string; title: string; kind: string; start: number | null; in: number | null } | null;
  fallback: string | null;
  pending_hard_start: number | null;
  events: PlayEvent[];
  countdowns: Countdown[];
  alarms: Alarm[];
  checks: Check[];
  meters: Meters;
  live_inputs: string[];
  stats: {
    output: {
      clients: number;
      kbps: number;
      fps: number;
      bytes_out: number;
      frames: number;
      luma: number;
      uptime: number;
      uri: string;
      scte_pid: number;
      repeated_frames: number;
      audio_underruns: number;
      gateway_restarts: number;
      srt: Record<string, number>;
    };
    live_inputs: LiveInputStats[];
    engine: { prerolled: string[]; slate_on_air: boolean };
  };
}

export interface LogEvent {
  seq: number;
  ts: number;
  cat: string;
  level: "info" | "warn" | "error";
  msg: string;
}
