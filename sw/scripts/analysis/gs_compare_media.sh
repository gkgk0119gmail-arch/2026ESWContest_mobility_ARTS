#!/usr/bin/env bash
# 게인 스케줄링: 같은 시드·같은 빙판에서 고정 게인(왼쪽) vs 탐색 후보(오른쪽) 조감 영상을 나란히 붙인다.
# 입력: logs/gain_sched/runs/vid/demo_vid_<쌍>_{fixed,cand}_bev.mp4  (gs_runner.py --video 로 녹화)
# 라벨은 충돌 직전(0.1 s 전) 속도다 — 충돌 이벤트의 속도는 충격 뒤 값이라 쓰지 않는다.
# 출력: sw/media/gs_<쌍>.webp (README 인라인) + sw/media/clips/gs_<쌍>.mp4
set -eu
label() { python3 - "$SRC" "$1" "$2" <<'PY'
import json, sys, numpy as np
src, tag, name = sys.argv[1:4]
e = json.load(open(f"{src}/events_vid_{tag}.json"))["events"]; tr = json.load(open(f"{src}/trace_vid_{tag}.json"))
c = [x for x in e if x["event"] == "collision"]
if not c: print(f"{name}  충돌 없음"); sys.exit()
t = np.array([r["t"] for r in tr]); v = np.array([r["speed_kph"] for r in tr])
print(f"{name}  {c[0]['with']} 충돌 직전 {v[np.argmin(np.abs(t - (c[0]['t'] - 0.1)))]:.0f} km/h")
PY
}
ROOT=${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}
SRC=$ROOT/logs/gain_sched/runs/vid; OUT=$ROOT/sw/media
mkdir -p "$OUT/clips"
FONT=$(fc-match -f '%{file}' 'NanumGothic:bold' 2>/dev/null || true)
for pair in "$@"; do
  L="$SRC/demo_vid_${pair}_fixed_bev.mp4"; R="$SRC/demo_vid_${pair}_cand_bev.mp4"
  ll=$(label "${pair}_fixed" "고정 게인")
  rl=$(label "${pair}_cand" "탐색 후보")
  # 확정 0.5 s 전부터 (같은 시드라 두 주행의 확정 시각이 같다)
  t0=$(python3 -c "import json;e=json.load(open('$SRC/events_vid_${pair}_fixed.json'))['events'];print(max(0,[x['t'] for x in e if x['event']=='secondary_slip'][0]-3.0-0.5))")
  dt="drawtext=fontfile=$FONT:fontsize=26:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=8:x=14:y=h-48"
  ffmpeg -nostdin -v error -y -ss "$t0" -i "$L" -ss "$t0" -i "$R" -filter_complex \
    "[0:v]scale=480:-2,${dt}:text='$ll'[a];[1:v]scale=480:-2,${dt}:text='$rl'[b];[a][b]hstack=inputs=2,fps=10,tpad=stop_mode=clone:stop_duration=1.5[v]" \
    -map "[v]" -c:v libwebp -loop 0 -q:v 62 -an "$OUT/gs_${pair}.webp"
  ffmpeg -nostdin -v error -y -ss "$t0" -i "$L" -ss "$t0" -i "$R" -filter_complex \
    "[0:v]scale=480:-2,${dt}:text='$ll'[a];[1:v]scale=480:-2,${dt}:text='$rl'[b];[a][b]hstack=inputs=2,format=yuv420p[v]" \
    -map "[v]" -c:v libx264 -crf 26 -preset medium -movflags +faststart -an "$OUT/clips/gs_${pair}.mp4"
  echo "$pair  $(du -h "$OUT/gs_${pair}.webp" | cut -f1)  — 왼쪽 $ll / 오른쪽 $rl"
done
