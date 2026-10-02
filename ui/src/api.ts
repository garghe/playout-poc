import { useEffect, useRef, useState } from "react";
import type { LogEvent, Meters, State } from "./types";

export async function api<T = unknown>(path: string, body?: unknown, method = "POST"): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body !== undefined ? { "content-type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      msg = (await res.json()).detail ?? msg;
    } catch {
      /* not json */
    }
    throw new Error(msg);
  }
  return res.json();
}

export async function uploadPlaylist(file: File): Promise<unknown> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/playlist", { method: "POST", body: fd });
  if (!res.ok) throw new Error((await res.json()).detail ?? res.statusText);
  return res.json();
}

const MAX_LOG = 500;

/** One WebSocket carrying state (4 Hz), meters (10 Hz), log events and JPEG preview frames. */
export function usePlayout() {
  const [state, setState] = useState<State | null>(null);
  const [meters, setMeters] = useState<Meters | null>(null);
  const [log, setLog] = useState<LogEvent[]>([]);
  const [preview, setPreview] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [clockOffset, setClockOffset] = useState(0); // server - local (seconds)
  const lastUrl = useRef<string | null>(null);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let stop = false;
    let retry: number | undefined;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.binaryType = "blob";
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!stop) retry = window.setTimeout(connect, 1500);
      };
      ws.onmessage = (ev) => {
        if (ev.data instanceof Blob) {
          const url = URL.createObjectURL(ev.data);
          if (lastUrl.current) URL.revokeObjectURL(lastUrl.current);
          lastUrl.current = url;
          setPreview(url);
          return;
        }
        const msg = JSON.parse(ev.data);
        if (msg.type === "hello") {
          setLog(msg.log);
          if (msg.state && msg.state.now) {
            setState(msg.state);
            setClockOffset(msg.state.now - Date.now() / 1000);
          }
        } else if (msg.type === "state") {
          setState(msg.data);
          setClockOffset(msg.data.now - Date.now() / 1000);
        } else if (msg.type === "meters") {
          setMeters(msg.data);
        } else if (msg.type === "log") {
          setLog((l) => {
            const n = l.length >= MAX_LOG ? l.slice(l.length - MAX_LOG + 1) : l.slice();
            n.push(msg.data);
            return n;
          });
        }
      };
    };
    connect();

    // Fallback: if the WebSocket is down, poll the REST API so the UI still works
    // (no preview frames or live log in this mode).
    let lastSeq = 0;
    const poll = window.setInterval(async () => {
      if (ws && ws.readyState === WebSocket.OPEN) return;
      try {
        const s: State = await (await fetch("/api/state")).json();
        if (s && s.now) {
          setState(s);
          if (s.meters) setMeters(s.meters);
          setClockOffset(s.now - Date.now() / 1000);
        }
        const l: LogEvent[] = await (await fetch("/api/log?n=200")).json();
        if (l.length && l[l.length - 1].seq !== lastSeq) {
          lastSeq = l[l.length - 1].seq;
          setLog(l);
        }
      } catch {
        /* backend unreachable */
      }
    }, 1000);

    return () => {
      stop = true;
      window.clearTimeout(retry);
      window.clearInterval(poll);
      ws?.close();
    };
  }, []);

  return { state, meters, log, preview, connected, clockOffset };
}

/** Re-render at ~25 fps for clocks/countdowns. */
export function useNow(offset: number, fps = 25): number {
  const [now, setNow] = useState(Date.now() / 1000 + offset);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now() / 1000 + offset), 1000 / fps);
    return () => window.clearInterval(id);
  }, [offset, fps]);
  return now;
}
