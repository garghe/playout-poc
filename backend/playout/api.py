"""HTTP + WebSocket API (FastAPI). No authentication (MVP)."""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import playlist as pl_mod
from . import schedule
from .asrun import AsRunLog
from .config import CONFIG, ROOT
from .engine.engine import Engine
from .eventlog import EventLog

SAMPLES = ROOT / "samples"
log = logging.getLogger("playout.api")


class Hub:
    """Fans engine updates out to connected WebSocket clients."""

    def __init__(self) -> None:
        self.clients: set[asyncio.Queue] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    def publish(self, msg) -> None:
        for q in list(self.clients):
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(msg)

    def publish_threadsafe(self, msg) -> None:
        if self.loop is not None and self.clients:
            self.loop.call_soon_threadsafe(self.publish, msg)


eventlog = EventLog()
asrun = AsRunLog(CONFIG.data_dir / "asrun", CONFIG.tz)
hub = Hub()
engine: Engine | None = None


def _json(obj) -> str:
    return json.dumps(obj, default=str, allow_nan=False)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    global engine
    hub.loop = asyncio.get_running_loop()
    engine = Engine(eventlog, asrun)
    engine.on_state = lambda s: hub.publish_threadsafe(("state", s))
    engine.on_meters = lambda m: hub.publish_threadsafe(("meters", m))
    eventlog.listeners.append(lambda ev: hub.publish_threadsafe(("log", ev)))
    engine.start()
    preview_task = asyncio.create_task(_preview_pump())
    yield
    preview_task.cancel()
    engine.shutdown()


async def _preview_pump() -> None:
    last = -1
    while True:
        await asyncio.sleep(0.1)
        out = engine.output
        if out.preview_seq != last and out.preview_jpeg and hub.clients:
            last = out.preview_seq
            hub.publish(("jpeg", out.preview_jpeg))


app = FastAPI(title="Playout POC", lifespan=lifespan)


async def run(fn, *args):
    try:
        return await asyncio.wrap_future(engine.call(fn, *args))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ------------------------------------------------------------------ state
@app.get("/api/state")
async def get_state():
    return JSONResponse(json.loads(_json(engine.snapshot)))


@app.get("/api/log")
async def get_log(n: int = 200):
    return eventlog.recent(n)


# --------------------------------------------------------------- playlist
async def _load(text: str, filename: str) -> dict:
    try:
        playlist = pl_mod.load(text, filename, CONFIG.tz)
    except pl_mod.PlaylistError as e:
        eventlog.add("PLAYLIST", f"import of {filename} failed: {e}", "error")
        raise HTTPException(400, str(e)) from e
    await asyncio.to_thread(schedule.probe_events, playlist.events)
    eventlog.add("PLAYLIST", f"imported {filename}")
    return await run(engine.set_playlist, playlist)


@app.post("/api/playlist")
async def upload_playlist(file: UploadFile = File(...)):
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise HTTPException(400, "playlist must be UTF-8 text") from e
    return await _load(text, file.filename or "playlist.json")


@app.get("/api/samples")
async def list_samples():
    return sorted(p.name for p in SAMPLES.glob("*") if p.suffix in (".json", ".csv"))


@app.post("/api/samples/{name}")
async def load_sample(name: str):
    path = SAMPLES / Path(name).name
    if not path.is_file():
        raise HTTPException(404, "no such sample")
    return await _load(path.read_text(), path.name)


@app.post("/api/checks")
async def rerun_checks():
    evs = engine.events
    await asyncio.to_thread(schedule.probe_events, evs)
    checks = await run(lambda: setattr(engine, "checks", schedule.preair_checks(evs, time.time())) or engine.checks)
    eventlog.add("CHECKS", f"pre-air checks re-run: {len(checks)} findings")
    return checks


# ---------------------------------------------------------------- control
class StartReq(BaseModel):
    index: int | None = None


class HoldReq(BaseModel):
    on: bool


class UidReq(BaseModel):
    uid: str


class LiveReq(BaseModel):
    input: str | None = None


@app.post("/api/control/start")
async def c_start(req: StartReq | None = None):
    await run(engine.start_auto, req.index if req else None)
    return {"ok": True}


@app.post("/api/control/stop")
async def c_stop():
    await run(engine.stop_auto)
    return {"ok": True}


@app.post("/api/control/take")
async def c_take():
    await run(engine.take_next)
    return {"ok": True}


@app.post("/api/control/hold")
async def c_hold(req: HoldReq):
    await run(engine.hold, req.on)
    return {"ok": True}


@app.post("/api/control/skip")
async def c_skip(req: UidReq):
    await run(engine.skip, req.uid)
    return {"ok": True}


@app.post("/api/control/cursor")
async def c_cursor(req: UidReq):
    await run(engine.set_cursor, req.uid)
    return {"ok": True}


@app.post("/api/control/live")
async def c_live(req: LiveReq | None = None):
    await run(engine.go_live, req.input if req else None)
    return {"ok": True}


@app.post("/api/control/return")
async def c_return():
    await run(engine.return_from_live)
    return {"ok": True}


@app.post("/api/alarms/clear")
async def c_clear_alarms():
    await run(engine.clear_alarms)
    return {"ok": True}


# ------------------------------------------------------------------- SCTE
class ScteOut(BaseModel):
    duration: float
    preroll: float = 0.0


class ScteIn(BaseModel):
    event_id: int | None = None
    preroll: float = 0.0


@app.post("/api/scte/out")
async def scte_out(req: ScteOut):
    if not 0 < req.duration <= 3600 or not 0 <= req.preroll <= 60:
        raise HTTPException(400, "duration must be 0-3600s, preroll 0-60s")
    return {"event_id": await run(engine.scte_out, req.duration, req.preroll)}


@app.post("/api/scte/in")
async def scte_in(req: ScteIn):
    return {"event_id": await run(engine.scte_in, req.event_id, req.preroll)}


# ------------------------------------------------------------- countdowns
class CountdownReq(BaseModel):
    label: str
    category: str = "manual"
    seconds: float | None = None   # countdown length from now
    at: str | None = None          # or a wall-clock time HH:MM[:SS] today
    action: str = "none"           # none | go_live | take_next | scte
    live_input: str | None = None
    scte_duration: float | None = None


@app.post("/api/countdowns")
async def add_countdown(req: CountdownReq):
    if req.seconds is not None:
        at = time.time() + req.seconds
    elif req.at:
        try:
            secs = pl_mod.parse_tc(req.at if req.at.count(":") >= 2 else req.at + ":00")
        except pl_mod.PlaylistError as e:
            raise HTTPException(400, str(e)) from e
        now = datetime.now(CONFIG.tz)
        target = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(seconds=secs)
        if target < now:
            target += timedelta(days=1)
        at = target.timestamp()
    else:
        raise HTTPException(400, "give 'seconds' or 'at'")
    return await run(engine.add_countdown, req.label, req.category.lower(), at, req.action,
                     req.live_input, req.scte_duration)


@app.delete("/api/countdowns/{cd_id}")
async def del_countdown(cd_id: str):
    await run(engine.remove_countdown, cd_id)
    return {"ok": True}


# ----------------------------------------------------------------- as-run
@app.get("/api/asrun")
async def get_asrun():
    return asrun.read()


@app.get("/api/asrun.csv")
async def get_asrun_csv():
    path = asrun.path_for()
    if not path.exists():
        return StreamingResponse(io.BytesIO(b"no as-run entries today\n"), media_type="text/plain")
    return FileResponse(path, media_type="text/csv", filename=path.name)


# -------------------------------------------------------------- websocket
@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    await sock.send_text(_json({"type": "hello", "log": eventlog.recent(200), "state": engine.snapshot}))
    hub.clients.add(q)
    try:
        while True:
            kind, payload = await q.get()
            if kind == "jpeg":
                await sock.send_bytes(payload)
            else:
                await sock.send_text(_json({"type": kind, "data": payload}))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.clients.discard(q)


# ------------------------------------------------------------------- UI
if CONFIG.ui_dir.is_dir():
    app.mount("/", StaticFiles(directory=CONFIG.ui_dir, html=True), name="ui")
else:
    @app.get("/")
    async def no_ui():
        return {"msg": "UI not built. Run: cd ui && npm install && npm run build"}
