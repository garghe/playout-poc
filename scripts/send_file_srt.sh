#!/usr/bin/env bash
# Loops a prepared MPEG-TS file into a playout live input as an SRT caller (no re-encode).
#   scripts/send_file_srt.sh [port] [file]      default: 9003 samples/live/bbb_loop.ts (LIVE-3)
# Env: HOST (default 127.0.0.1)
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${1:-9003}"
FILE="${2:-$DIR/../samples/live/bbb_loop.ts}"
HOST="${HOST:-127.0.0.1}"
exec ffmpeg -hide_banner -loglevel warning -re -stream_loop -1 -i "$FILE" \
  -map 0 -c copy -f mpegts "srt://$HOST:$PORT?mode=caller&latency=200000"
