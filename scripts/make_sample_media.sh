#!/usr/bin/env bash
# Generates short test clips used by samples/playlist.json.
# Each clip shows its title, a running timecode and has a distinct tone so
# cuts are easy to see and hear.
set -euo pipefail

OUT="${1:-$(dirname "$0")/../media}"
mkdir -p "$OUT"

FONT="$(fc-match -f '%{file}' 'DejaVu Sans:bold' 2>/dev/null || echo /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf)"

# name  seconds  colour  tone_hz  size
make_clip() {
  local name="$1" secs="$2" colour="$3" hz="$4" size="${5:-1920x1080}"
  local file="$OUT/$name.mp4"
  [[ -f "$file" ]] && { echo "exists: $file"; return; }
  echo "making $file (${secs}s)"
  ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "testsrc2=size=$size:rate=25,hue=h=0:s=0.4,format=yuv420p" \
    -f lavfi -i "color=c=$colour:size=$size:rate=25" \
    -f lavfi -i "sine=frequency=$hz:sample_rate=48000,aformat=channel_layouts=stereo" \
    -filter_complex "[1:v][0:v]blend=all_mode=overlay:all_opacity=0.35[b]; \
      [b]drawtext=fontfile=$FONT:text='${name//_/ }':fontsize=h/9:fontcolor=white:borderw=4:x=(w-tw)/2:y=h*0.3, \
      drawtext=fontfile=$FONT:timecode='00\:00\:00\:00':rate=25:fontsize=h/14:fontcolor=yellow:borderw=3:x=(w-tw)/2:y=h*0.6[v]" \
    -map "[v]" -map 2:a -t "$secs" \
    -c:v libx264 -preset veryfast -pix_fmt yuv420p -g 25 -c:a aac -b:a 128k \
    "$file"
}

make_clip opening_titles     15 navy      440
make_clip morning_news       45 darkred   523
make_clip spot_cola          10 orange    660
make_clip spot_car           10 green     700
make_clip spot_bank          10 purple    750
make_clip documentary_4x3    30 teal      392 1440x1080
make_clip feature_film       60 saddlebrown 330
make_clip spot_insurance     10 olive     800
make_clip match_preview      20 darkgreen 587

echo "done -> $OUT"
