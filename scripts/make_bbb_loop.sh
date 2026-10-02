#!/usr/bin/env bash
# Prepares samples/live/bbb_loop.ts (the LIVE-3 test signal) from a Big Buck Bunny clip:
# 1080p25 H.264 (1 s GOP, so it loops cleanly) + a quiet stereo tone bed (the clip is silent),
# with a "LIVE-3" bug and the CC-BY credit burned in. Streaming it is then a cheap -c copy.
#   scripts/make_bbb_loop.sh <source.mp4> [out.ts]
set -euo pipefail
SRC="$1"
OUT="${2:-$(dirname "$0")/../samples/live/bbb_loop.ts}"
FONT="$(fc-match -f '%{file}' 'DejaVu Sans:bold' 2>/dev/null || echo /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf)"
DUR="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$SRC")"
ffmpeg -hide_banner -loglevel error -y -i "$SRC" \
  -f lavfi -i "sine=frequency=220:sample_rate=48000" -f lavfi -i "sine=frequency=330:sample_rate=48000" \
  -filter_complex "[0:v]fps=25,scale=1920:1080,format=yuv420p,\
drawtext=fontfile=$FONT:text='LIVE-3':fontsize=40:fontcolor=white:box=1:boxcolor=red@0.85:boxborderw=12:x=w-tw-60:y=50,\
drawtext=fontfile=$FONT:text='Big Buck Bunny (c) Blender Foundation | CC BY 3.0':fontsize=22:fontcolor=white@0.85:borderw=2:x=60:y=h-60[v];\
[1:a][2:a]amerge=inputs=2,volume=0.65,afade=t=in:d=0.05,afade=t=out:st=$(echo "$DUR - 0.05" | bc):d=0.05[a]" \
  -map "[v]" -map "[a]" -t "$DUR" \
  -c:v libx264 -preset medium -b:v 5M -maxrate 6M -bufsize 6M -g 25 -keyint_min 25 -sc_threshold 0 -pix_fmt yuv420p \
  -c:a aac -b:a 128k -ar 48000 -ac 2 -f mpegts "$OUT"
echo "wrote $OUT"
