# Playlist format

Playlists are imported as **JSON** (full feature set) or **CSV** (simple, spreadsheet-friendly).
Examples: [`samples/playlist.json`](../samples/playlist.json), [`samples/playlist.csv`](../samples/playlist.csv).

## JSON

```json
{
  "channel": "Demo Channel",
  "date": "2026-10-02",
  "items": [
    {"id": "001", "title": "Opening Titles", "type": "programme", "category": "promo",
     "source": "opening_titles.mp4"},
    {"id": "B01", "title": "Break 1", "type": "break", "scte": true, "spots": [
      {"id": "S101", "title": "Cola 10\"", "source": "spot_cola.mp4", "duration": 10},
      {"id": "S102", "title": "Car 10\"",  "source": "spot_car.mp4",  "duration": 10}
    ]},
    {"id": "005", "title": "Premier League LIVE", "type": "live", "category": "football",
     "source": "srt://:9002?mode=listener", "start_mode": "hard", "start": "20:00:00", "duration": "01:45:00:00"}
  ]
}
```

### Top level

| Field     | Required | Notes |
|-----------|----------|-------|
| `channel` | no       | Shown in the UI. |
| `date`    | no       | `YYYY-MM-DD` the hard start times refer to. Default: today (`PLAYOUT_TZ`, default Europe/London). |
| `items`   | yes      | Ordered list of items. A bare list (no wrapper object) is also accepted. |

### Item fields

| Field        | Applies to        | Notes |
|--------------|-------------------|-------|
| `id`         | all               | Unique. Defaults to the position (`001`, `002`, …). |
| `title`      | all               | Display name. |
| `type`       | all               | `programme` (file), `live` (SRT feed) or `break` (container of spots). Default `programme`. |
| `category`   | programme, live   | Free text, e.g. `news`, `football`, `film`, `breaking news`. Each category gets its own "next …" countdown. |
| `source`     | programme, live, spot | File: name in `media/`, a relative/absolute path or `file://` URI. Live: `srt://…` URI (listener or caller). |
| `start_mode` | all               | `follow` (starts when the previous item ends) or `hard` (fixed time). Default `follow`, or `hard` if `start` is given. |
| `start`      | hard items        | `HH:MM:SS` / `HH:MM:SS:FF` time of day, **or** `+HH:MM:SS` = relative to when the playlist is loaded (handy for demos). |
| `duration`   | all               | `HH:MM:SS:FF`, `HH:MM:SS`, `HH:MM:SS.mmm` or seconds. Files default to the media length minus `in`. Live items without a duration are open-ended (they run until the operator presses *Take next*). |
| `in`         | programme, spot   | In point (SOM) inside the file. Combine with `duration` for an out point. |
| `scte`       | break             | `true` (default): send SCTE-35 splice OUT (with the break duration) when the break starts and splice IN when it ends. |
| `spots`      | break             | List of spots: `id`, `title`, `source`, `duration`, `in`. |

Timecodes use 25 fps (`FF` = frames).

### Timing rules

* **Follow** items start when the previous one ends.
* A **hard** start wins: if the previous item would still be running it is **cut** at the hard time;
  if it ends early, the **slate** fills the gap until the hard time. Both cases are flagged by the pre-air checks
  and shown in the playlist.
* Times in the UI are *projected* from what is on air now and update live.

## CSV

One row per item. `spot` rows belong to the `break` row above them. Header names are case-insensitive;
columns can be in any order and unused ones omitted.

```csv
id,title,type,category,source,start_mode,start,duration,in,scte
001,Opening Titles,programme,promo,opening_titles.mp4,follow,,,,
B01,Break 1,break,,,follow,,,,true
S101,Cola,spot,,spot_cola.mp4,,,10,,
S102,Bank,spot,,spot_bank.mp4,,,10,,
004,Studio Live,live,news,srt://:9001?mode=listener,follow,,00:01:00:00,,
```

Rows whose `id` starts with `#` are ignored (comments).

## Pre-air checks

On import (and on **Re-check**) every item is validated:

| Level   | Check |
|---------|-------|
| error   | Media file missing / unreadable / no video stream |
| error   | Live source is not an `srt://` (or udp/rtmp/rtsp) URI |
| warning | Media shorter than the planned duration (will end early) |
| warning | No audio stream (silence will be played) |
| warning | Gap before a hard start (slate will fill) |
| warning | Item cut short by the next hard start |
| info    | Open-ended live item, hard start already in the past |
