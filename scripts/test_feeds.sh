#!/usr/bin/env bash
# Keeps two test SRT feeds running into the playout live inputs, reconnecting
# whenever a connection drops (e.g. while the playout is starting or restarting).
#   LIVE-1 :9001  "STUDIO B"  1 kHz tone
#   LIVE-2 :9002  "FOOTBALL"  440 Hz tone
#   LIVE-3 :9003  Big Buck Bunny loop (samples/live/bbb_loop.ts, no re-encode)
# Env: HOST (default 127.0.0.1), SIZE (default 1280x720 to keep CPU use low)
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
export HOST="${HOST:-127.0.0.1}" SIZE="${SIZE:-1280x720}"

feed() {   # port label freq
  while true; do
    FREQ="$3" "$DIR/send_test_srt.sh" "$1" "$2" || true
    echo "test feed $2 -> $HOST:$1 disconnected, retrying in 2s" >&2
    sleep 2
  done
}

file_feed() {   # port file
  while true; do
    "$DIR/send_file_srt.sh" "$1" "$2" || true
    echo "file feed $2 -> $HOST:$1 disconnected, retrying in 2s" >&2
    sleep 2
  done
}

feed 9001 "STUDIO B" 1000 &
feed 9002 "FOOTBALL" 440 &
file_feed 9003 "$DIR/../samples/live/bbb_loop.ts" &
trap 'kill 0' INT TERM
wait
