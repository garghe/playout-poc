#!/usr/bin/env bash
# Sends a live test signal (bars-like pattern + clock + 1 kHz tone) as an SRT caller
# into one of the playout's live inputs.
#   scripts/send_test_srt.sh                 -> srt://127.0.0.1:9001 (default "LIVE-1" input)
#   scripts/send_test_srt.sh 9002 "FOOTBALL" -> the sample playlist's live item
set -euo pipefail
PORT="${1:-9001}"
LABEL="${2:-LIVE $PORT}"
HOST="${HOST:-127.0.0.1}"
FONT="$(fc-match -f '%{file}' 'DejaVu Sans:bold' 2>/dev/null || echo /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf)"
exec ffmpeg -hide_banner -loglevel warning -re \
  -f lavfi -i "smptehdbars=size=1920x1080:rate=25" \
  -f lavfi -i "sine=frequency=1000:sample_rate=48000,volume=0.25,aformat=channel_layouts=stereo" \
  -vf "drawtext=fontfile=$FONT:text='$LABEL':fontsize=96:fontcolor=white:borderw=5:x=(w-tw)/2:y=h*0.25,\
drawtext=fontfile=$FONT:text='%{localtime\:%H\\\\\:%M\\\\\:%S}':fontsize=72:fontcolor=yellow:borderw=4:x=(w-tw)/2:y=h*0.45" \
  -c:v libx264 -preset ultrafast -tune zerolatency -g 25 -b:v 4M -pix_fmt yuv420p \
  -c:a aac -b:a 128k -f mpegts "srt://$HOST:$PORT?mode=caller&latency=200000"
