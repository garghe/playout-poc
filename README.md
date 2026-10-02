# Playout POC

A basic broadcast playout system: imports a playlist, plays files and live SRT feeds to a
1080p25 SRT programme output, handles ad breaks with SCTE-35, and gives the operator a web UI
with preview, audio meters, countdowns and a "stats for nerds" event log.

No authentication. This is an MVP / proof of concept.

![Operator UI](docs/screenshot.png)

## Features

| Area | What it does |
|------|--------------|
| **Playlist import** | JSON or CSV ([format](docs/playlist-format.md)). Programmes, live items, breaks with spots, follow/hard starts, in points. Load a sample or upload a file from the UI. |
| **Pre-recorded + live** | Files are cued (prerolled) 3s ahead for clean cuts. Live items take an SRT feed. Any input format/resolution is normalised to 1080p25 / 48 kHz stereo. |
| **Breaks** | A break groups spots. On break start: SCTE-35 `splice_insert` OUT with the break duration. On break end: splice IN. Skipped spots are handled. |
| **SCTE-35** | Inserted on PID 500 of the output TS (with null heartbeats). Manual **Splice OUT** (duration + preroll) and **Splice IN** buttons. |
| **Countdowns** | Automatic: next break, next live, next item of every category (news, football, film…), all from the projected timeline. Manual: from a button, "in mm:ss" or "at HH:MM", optionally with an action at zero (**go live**, take next, SCTE out). |
| **UI** | Playlist with live projected times, programme preview, stereo peak/RMS meters with peak hold, EBU R128 loudness (M/S/I), on-air/next with timers, alarms, pre-air checks. |
| **As-run log** | What actually went to air, with frame-accurate times and status (complete / truncated / interrupted / error / fallback). Daily CSV in `data/asrun/`, downloadable from the UI. |
| **Fallback slate** | If a live feed has no signal or drops, or a file fails, a slate goes on air automatically. When the signal returns, the live feed comes back. The slate also fills gaps before hard starts. |
| **Manual control** | Start (from any item) / Stop, Take next, Hold (stop auto-advance), Skip/unskip, Go live (live override) / Return to playlist. |
| **Technical difficulties slate** | Big red button in the top bar. One click puts a "We are experiencing technical difficulties / Please stay tuned" slate on air immediately. Automation keeps running underneath, so **Release** returns to whatever should be on air at that moment. Raises an alarm and is written to the as-run log. |
| **HLS output (FAST)** | Live HLS at `http://<host>:8080/hls/master.m3u8`: exact 6 s segments (fixed 1 s GOP), `#EXT-X-PROGRAM-DATE-TIME`, SCTE-35 breaks as `#EXT-X-CUE-OUT` / `CUE-OUT-CONT` / `CUE-IN` **and** `#EXT-X-DATERANGE` with the SCTE-35 bytes, keyframe + segment boundary forced at every splice point. Ready to sit behind an SSAI service (e.g. AWS MediaTailor) as a FAST channel origin. |
| **Outputs panel** | Full SRT and HLS URLs with **Copy URL** and **▶ Open in VLC** (downloads a one-line `.m3u`), live status (SRT callers, HLS segments, in-break), and an in-browser HLS monitor. |
| **Pre-air checks** | Missing/unreadable media, media shorter than planned, no audio, gaps and overlaps around hard starts. |
| **Loudness & alarms** | Alarms for black, silence, loudness over -20 LUFS short-term, fallback on air and failed items. |
| **Stats for nerds** | Live numbers (output bitrate/fps, SRT callers/RTT/loss, repeated frames, audio underruns, input signal and bitrate). Filterable event log: source changes, SCTE markers, breaks, SRT connects, alarms, preroll, operator actions. |

## Quick start (Docker)

```bash
docker compose up --build
# open http://localhost:8080
```

This starts two containers:

* **playout**: the engine and UI. Sample media is generated into `./media` on first start (takes about a minute).
* **testfeeds**: test SRT encoders feeding the live inputs, so they all show signal straight away:
  LIVE-1 "STUDIO B" (1 kHz tone) and LIVE-2 "FOOTBALL" (440 Hz tone), 720p25, plus LIVE-3: a looping
  **Big Buck Bunny** clip (`samples/live/bbb_loop.ts`, streamed without re-encoding). They reconnect automatically.
  To run without them (e.g. with a real encoder): `docker compose up playout`.

I have not been able to test the Docker image build in my environment; the native setup below has been tested.

## Quick start (native, Ubuntu 24.04)

```bash
sudo apt install python3-venv python3-gst-1.0 gir1.2-gst-plugins-bad-1.0 gstreamer1.0-tools \
  gstreamer1.0-plugins-{base,good,bad,ugly} gstreamer1.0-libav gstreamer1.0-x ffmpeg fonts-dejavu-core
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r backend/requirements.txt
scripts/make_sample_media.sh                 # test clips -> ./media
(cd ui && npm install && npm run build)      # UI -> ui/dist (served by the backend)
cd backend && ../.venv/bin/python -m playout # http://localhost:8080
```

For UI development, run `npm run dev` in `ui/` (http://localhost:5173). It proxies `/api` and `/ws` to the backend.

### Demo in 30 seconds

1. Open http://localhost:8080 → **Playlist → Load sample… → playlist.json**, then **▶ Start**.
2. Watch the output in VLC (below). The sample has three breaks (with SCTE-35), a 4:3 programme
   (pillarboxed), and a live football item with a hard start 2m30s after loading.
3. The live item uses LIVE-2. With Docker the `testfeeds` container already feeds it. Natively, run
   `scripts/test_feeds.sh`. Without a feed, the fallback slate covers the live slot.
4. Try **+ Manual → Breaking news**: a countdown that goes live on LIVE-1 ("STUDIO B") at zero.
   Then **↩ Return to playlist**.

## Outputs: SRT and HLS (watching in VLC)

The same encoded programme (H.264 High 1080p25 ~6 Mb/s, AAC-LC stereo 192 kb/s, SCTE-35 on PID 500)
is delivered two ways. Both URLs are shown in full in the UI's **Outputs** panel:

| Output | URL | Use |
|--------|-----|-----|
| SRT | `srt://127.0.0.1:9000` | Contribution/monitoring, low latency (~0.5 s). SRT listener: players connect as callers. |
| HLS | `http://127.0.0.1:8080/hls/master.m3u8` | FAST / OTT distribution, CDN- and SSAI-friendly (~15–20 s latency, normal for 6 s segments). |

Replace `127.0.0.1` with the server's address when watching from another machine. The UI shows `127.0.0.1`
instead of `localhost` because macOS may resolve `localhost` to IPv6, which the IPv4 SRT listener and Docker's
UDP port mapping don't answer.

* **From the UI**: in the **Outputs** panel click **▶ Open in VLC** next to SRT or HLS, then double-click the
  downloaded `.m3u` (VLC opens it with 1 s network caching). Browsers can't launch `srt://` addresses directly,
  which is why it's a small playlist file. **Copy URL** copies the full address. **▶ Play here** plays the HLS
  output in the page (needs a browser with H.264: Chrome, Safari, Edge) and shows the delay behind live.
* **VLC with the HLS URL**: *Media → Open Network Stream…* → `http://127.0.0.1:8080/hls/master.m3u8`.
* Verified with VLC 3.0.20: two simultaneous SRT players and one HLS player, all decoding 1080p H.264 + AAC.
* **VLC 3.0+ manually**: *Media → Open Network Stream…* → `srt://<server-ip>:9000` → *Play*.
  From the command line: `vlc srt://127.0.0.1:9000`.
  If playback stutters, raise the network caching: *Show more options → Caching* to 500–1000 ms,
  or `vlc --network-caching=1000 srt://127.0.0.1:9000`.
* **ffplay**: `ffplay -fflags nobuffer "srt://127.0.0.1:9000?mode=caller&latency=200000"`
* **GStreamer**: `gst-launch-1.0 playbin uri="srt://127.0.0.1:9000"`

Several players can connect at the same time. Each connection shows in the log (`SRT-OUT caller connected`)
and in the stats panel. Behind a firewall or Docker, open/map **UDP** 9000.

**Check the SCTE-35 markers** in the live output:

```bash
gst-launch-1.0 -q srtsrc uri="srt://127.0.0.1:9000?mode=caller" ! fdsink fd=1 | scripts/scte_sniff.py -
# splice_insert OUT event=68546 pts=3605.581 duration=15.0s auto_return=0
# splice_insert IN  event=68546 pts=3605.693
```

(Use a raw capture like this. Remuxing with `ffmpeg -c copy` drops the SCTE-35 data stream.)
A player that joins mid-GOP may log a few "non-existing PPS" errors until the next keyframe (≤ 1 s). That's expected.

### Using it as a FAST channel origin

Point your SSAI / FAST service at `http://<host>:8080/hls/master.m3u8` as the content origin (behind a CDN).
Ad breaks from the playlist (and manual **Splice OUT/IN**) appear in the playlist as:

```
#EXT-X-PROGRAM-DATE-TIME:2026-10-02T22:26:47.747Z
#EXT-X-DATERANGE:ID="splice-79955",START-DATE="2026-10-02T22:26:47.747Z",PLANNED-DURATION=30.000,SCTE35-OUT=0xFC3025…
#EXT-X-CUE-OUT:DURATION=30.000
#EXTINF:6.000,
seg_1790979964.ts
#EXT-X-CUE-OUT-CONT:ElapsedTime=6.000,Duration=30.000
…
#EXT-X-DATERANGE:ID="splice-79955",START-DATE="…",END-DATE="…",DURATION=30.000,SCTE35-IN=0xFC3020…
#EXT-X-CUE-IN
```

AWS MediaTailor, for example, reads either style, and the break starts exactly on a segment boundary.
The SCTE-35 PID is also kept inside the segments.

## Live inputs

| Input | URI (listener) | Used by |
|-------|----------------|---------|
| LIVE-1 | `srt://:9001?mode=listener` | Always present (`PLAYOUT_DEFAULT_LIVE`). **Go live** button, breaking news countdowns. |
| LIVE-2 | `srt://:9002?mode=listener` | Present from startup (`PLAYOUT_EXTRA_LIVE`, comma-separated list for more). The sample playlist's live item. |
| LIVE-3 | `srt://:9003?mode=listener` | Present from startup. Big Buck Bunny loop from the test feeds (`scripts/send_file_srt.sh`). |
| per playlist | any other `srt://` URI in a playlist | Created when the playlist is loaded |

**"no signal" means nothing is sending to that input.** The inputs are SRT *listeners*: an encoder
(or the test feeds) must connect to them as an SRT *caller* and push MPEG-TS.

Send a feed from any SRT encoder in caller mode (MPEG-TS, H.264/HEVC/MPEG-2 + AAC/MP2/AC-3).
Test signal: `scripts/send_test_srt.sh <port> "<label>"` (env `HOST`, `FREQ`, `SIZE`). For both inputs at once
with auto-reconnect: `scripts/test_feeds.sh` (this is what the `testfeeds` container runs).
To loop any file into an input: prepare it with `scripts/make_bbb_loop.sh <clip.mp4> <out.ts>`
(converts it to 1080p25 with a 1 s GOP and adds a tone if the clip is silent), then
`scripts/send_file_srt.sh <port> <out.ts>`.
From another machine, point the encoder at `srt://<playout-host>:9001` (or `:9002`), caller mode.
A live input with no video for 1.5s (`PLAYOUT_LIVE_LOSS_S`) counts as "signal lost". While on air, that triggers the fallback slate.

## Architecture

```
            ┌──────────────── playout process (Python, FastAPI + GStreamer) ────────────────┐
 playlist → │  Scheduler (GLib thread, 20 ms tick) ── as-run log / alarms / countdowns       │
            │      │ take / preroll / fallback                                               │
            │      ▼                                                                         │
 media/*.mp4│  FileSource ×N ─┐   (decode + scale/pad/rate → 1080p25 I420, 48k stereo F32)  │
            │  LiveSource ×N ─┼─► ROUTER ──► fixed 25 fps clock ──► Output pipeline          │
            │  SlateSource ───┘   only the on-air          repeat last frame,   x264 + AAC   │
            │                     source passes            pad silence          MPEG-TS+SCTE │
            │                                                                   │ preview    │
            │  WebSocket: state 4 Hz · meters 10 Hz · log · JPEG preview 10 fps ◄┘ meters    │
            └──────▲───────────────────────────────────────────────────────┬───────────────┘
                   │ localhost UDP (TS)                     localhost UDP (TS) │ (same TS, teed)
            ┌──────┴───────┐                                ┌──────────────┐  │  ┌──────────────┐
 SRT in  ──►│ SRT gateway  │ (one per live input)    SRT ◄──│ SRT gateway  │◄─┴─►│ HLS packager │──► /hls/*.m3u8, *.ts
            └──────────────┘                         :9000  └──────────────┘     └──────────────┘    (served on :8080)
```

Key design choices:

* **One pipeline per source + a router.** Each source decodes in its own pipeline, ending in appsinks.
  The router passes on only the on-air source. A clock-paced thread pushes exactly one frame and 40 ms of audio
  per tick into the output, so cuts are clean and the output never stalls or changes format.
  Frames and audio go through a small jitter buffer (primed with 80 ms at each cut, capped at 240 ms),
  and live inputs are paced by their own timestamps. This absorbs network and decoder bursts: a cut costs
  about 2 repeated frames and 1–2 audio underruns, with none in between.
  The repeated/dropped-frame and audio-underrun counters in the stats show how clean the output is.
  (GStreamer's `inter*` elements were tried first. They wipe the shared audio format whenever any
  source stops, which silenced the output after a cut.)
* **SRT in separate gateway processes.** libsrt 1.5 deadlocked inside the main process (epoll mutex) when
  run with the web server. Each SRT input and the output now run in a small supervised subprocess
  (`playout/gateway.py`) that talks to the engine over localhost UDP and reports caller and stats events as JSON.
  A gateway problem can't stop playout, and gateways die with the engine.
* **Preroll.** The next file is cued in PAUSED 3s (`PLAYOUT_PREROLL_S`) before its start, and seeked to its in point.
* **HLS packager as its own process** (`playout/hls.py`). It reads the finished TS, so HLS and SRT are frame-identical
  and carry the same SCTE-35. GStreamer's `hlssink2` can't write SCTE-35 cues into playlists, so segmenting is done
  here: cut on keyframes at 6 s and at every splice point. The encoder uses a fixed 1 s GOP (no scene-cut keyframes)
  and is asked for an extra IDR at each splice time, so ad breaks always start on a fresh segment.
  The cut happens on the 20 ms scheduler tick, which is under one frame of jitter.

### Code map

```
backend/playout/
  playlist.py      JSON/CSV import, validation, flattening breaks into events
  schedule.py      timeline projection (hard/follow), countdowns, pre-air checks
  engine/engine.py scheduler, controls, breaks+SCTE, fallback, alarms, state snapshot
  engine/sources.py file / live / slate source pipelines
  engine/router.py on-air router (fixed-cadence frame/audio pump)
  engine/output.py encoder + TS mux + SCTE-35, preview JPEG, meters, black detect
  engine/loudness.py  EBU R128 meter (BS.1770 K-weighting, gating)
  engine/srtgw.py  supervisor for the SRT gateways and the HLS packager
  gateway.py       the SRT gateway process;  hls.py  the HLS packager process
  asrun.py, eventlog.py, api.py (REST + WebSocket), config.py
ui/src/            React UI (Preview, AudioMeters, Countdowns, Controls, Playlist, Alarms, NerdLog)
scripts/           make_sample_media.sh, send_test_srt.sh, test_feeds.sh, send_file_srt.sh,
                   make_bbb_loop.sh, scte_sniff.py
samples/live/      bbb_loop.ts (LIVE-3 test signal)
```

### API (all JSON, no auth)

| Method | Path | Body |
|--------|------|------|
| GET | `/api/state` | full engine snapshot |
| GET | `/api/log?n=200` | recent log events |
| POST | `/api/playlist` | multipart `file` (.json/.csv) |
| GET/POST | `/api/samples`, `/api/samples/{name}` | list / load a sample |
| POST | `/api/control/start` | `{"index": 0}` (optional) |
| POST | `/api/control/{stop,take,return}` | – |
| POST | `/api/control/hold` | `{"on": true}` |
| POST | `/api/control/skip`, `/api/control/cursor` | `{"uid": "B01/S101"}` |
| POST | `/api/control/live` | `{"input": "LIVE-1"}` |
| POST | `/api/control/tech` | `{"on": true, "text": null}` (technical difficulties slate) |
| GET | `/api/output-url` | `{"srt": "srt://…:9000", "hls": "http://…/hls/master.m3u8"}` |
| GET | `/api/output.m3u?kind=srt\|hls` | one-line VLC playlist for that output |
| GET | `/hls/master.m3u8`, `/hls/stream.m3u8`, `/hls/seg_N.ts` | live HLS (CORS `*`, playlists not cached) |
| POST | `/api/scte/out` | `{"duration": 30, "preroll": 0}` |
| POST | `/api/scte/in` | `{"event_id": null, "preroll": 0}` |
| POST | `/api/countdowns` | `{"label", "category", "seconds" or "at": "HH:MM", "action": "none|go_live|take_next|scte", "live_input"}` |
| DELETE | `/api/countdowns/{id}` | – |
| GET | `/api/asrun`, `/api/asrun.csv` | today's as-run |
| POST | `/api/checks`, `/api/alarms/clear` | – |
| WS | `/ws` | text: `hello`/`state`/`meters`/`log`; binary: JPEG preview frames |

### Configuration (environment variables)

| Variable | Default | |
|----------|---------|--|
| `PLAYOUT_HTTP_PORT` | 8080 | UI/API |
| `PLAYOUT_OUTPUT_SRT` | `srt://:9000?mode=listener` | programme output (any srtsink URI, e.g. caller mode to a remote) |
| `PLAYOUT_DEFAULT_LIVE` | `srt://:9001?mode=listener` | always-on LIVE-1 input |
| `PLAYOUT_EXTRA_LIVE` | `srt://:9002?mode=listener,srt://:9003?mode=listener` | more inputs created at startup (comma-separated) |
| `PLAYOUT_VIDEO_KBPS` / `PLAYOUT_X264_PRESET` | 6000 / superfast | encoder |
| `PLAYOUT_SCTE_PID` | 500 | |
| `PLAYOUT_TZ` | Europe/London | hard start and display times |
| `PLAYOUT_PREROLL_S` | 3 | cue-ahead time |
| `PLAYOUT_LIVE_LOSS_S` | 1.5 | live signal-loss threshold |
| `PLAYOUT_BLACK_ALARM_S` / `PLAYOUT_SILENCE_ALARM_S` / `PLAYOUT_SILENCE_DBFS` / `PLAYOUT_LOUDNESS_MAX` | 5 / 5 / -60 / -20 | alarms |
| `PLAYOUT_MEDIA_DIR` / `PLAYOUT_DATA_DIR` | `./media` / `./data` | |
| `PLAYOUT_UDP_BASE` | 19000 | internal engine↔gateway ports (19000 SRT out, 19001+ inputs, 19100 HLS packager) |
| `PLAYOUT_HLS` | 1 | set to 0 to disable the HLS output |
| `PLAYOUT_HLS_SEGMENT_S` / `PLAYOUT_HLS_WINDOW` | 6 / 10 | segment length (s) / segments in the live playlist |

## Tests

```bash
cd backend && ../.venv/bin/python -m pytest -q
```

Unit tests cover playlist parsing and validation, timeline projection (hard-start gaps and truncation),
countdowns, pre-air checks, and the loudness meter against the EBU Tech 3341 reference.
The full engine was also tested end to end over SRT: breaks with SCTE, manual SCTE, filler before a hard start,
live with late signal, signal loss and fallback, go-live countdown, hold/take/skip/return, and gateway crash cleanup.

## Known limitations (MVP)

* Cuts are timed by a 20 ms software tick and are not genlocked. Expect ±1 frame accuracy, with about 2 repeated frames at a cut.
* While a live override or Hold is active, the timeline can't advance, so follow-on countdowns show **paused** (frozen)
  values. Items with a hard start keep counting down.
* Live audio/video sync follows the incoming feed. There is no A/V delay compensation.
* One channel. Stereo audio only. No subtitles or graphics overlay.
* HLS is a single 1080p rendition in MPEG-TS segments. A production FAST channel would add an ABR ladder
  (e.g. 1080p/720p/540p) and often CMAF/fMP4 segments. Segments are served by the app itself; put a CDN in front for scale.
* No authentication, no redundancy (main/backup), and engine state is in memory (the as-run log is on disk).
* SCTE-35 is `splice_insert` only (no `time_signal`/segmentation descriptors). `auto_return` is not set,
  so the engine sends an explicit splice IN at the end of each break.
* The preview is a 640×360 JPEG stream at 10 fps with no audio (meters show the audio). Use VLC for full-quality monitoring.

## Credits

`samples/live/bbb_loop.ts` is derived from *Big Buck Bunny* © 2008 Blender Foundation | www.bigbuckbunny.org,
licensed under [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/). It was re-encoded and trimmed, with a
label and a tone added. The credit is burned into the picture.
