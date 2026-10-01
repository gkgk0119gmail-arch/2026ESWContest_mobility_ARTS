#!/usr/bin/env bash
# POST 완료 후: RSCD 텍스처 실험 3건을 40 km/h 로 재실행 (기본 60 km/h 로 돌아 곡선 오탐으로 조기 종료됐음) → 정리
set -u
SP=${SP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}; DESK=${DESK_HOST:-user@desk-host}; ST=/tmp/after_batch_status.txt
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }
until grep -q "=== POST 완료" $ST 2>/dev/null; do sleep 15; done
say "=== POST2: RSCD 텍스처 실험 재실행 (40 km/h) ==="
timeout 600 ssh -o BatchMode=yes "$DESK" 'cd ~/icepredict/code && timeout 560 ~/icepredict/venv/bin/python sw/scripts/sim/carla_demo.py --weather ClearNoon --tag rscdtex_visual --target-kph 40 --views split,bev,lidar,lidar_sem --disable-primary --fusion local --slip-local --no-ice-render --road-texture ~/icepredict/dataset/rscd --export-labels ~/icepredict/logs/labels_rscdtex --traffic 6 --max-steps 650 2>&1 | grep -E "\[tex\]|\[world\]|진입|2차|정지|요약|Traceback|Error" | cut -c1-150' | tee -a $ST
for W in ClearNoon WetNoon; do
  say "--- $W: 합성 없이 엔진 RSCD 얼음 타일만 — 보드 NPU 인식 시험 (40 km/h)"
  timeout 30 ssh -o BatchMode=yes "$DESK" "cd ~/icepredict/code && setsid nohup timeout 900 ~/icepredict/venv/bin/python sw/scripts/sim/carla_demo.py --weather $W --tag ${W}_rscdtex_detect --target-kph 40 --views split,bev,lidar,lidar_sem --fusion n6npu --no-ice-render --road-texture ~/icepredict/dataset/rscd > ~/icepredict/logs/demo_${W}_rscdtex_detect.log 2>&1 < /dev/null &" || true
  sleep 40; bash $SP/sw/scripts/deploy/n6_bridge_restart.sh 150 /tmp/n6b_rscdtex_$W.log > /dev/null 2>&1
  for i in $(seq 1 60); do timeout 20 ssh -o BatchMode=yes "$DESK" "grep -qE '=== 요약|Traceback' ~/icepredict/logs/demo_${W}_rscdtex_detect.log" && break; sleep 10; done
  timeout 20 ssh -o BatchMode=yes "$DESK" "grep -E '\[tex\]|경고|진입|2차|정지|Traceback|무응답' ~/icepredict/logs/demo_${W}_rscdtex_detect.log | tail -6 | cut -c1-150" | tee -a $ST
  timeout 30 ssh -o BatchMode=yes "$DESK" 'for p in $(pgrep -f "carla_demo.p[y]"); do kill $p; done' || true; sleep 3
done
rsync -az "$DESK":~/icepredict/logs/carla_demo/ $SP/logs/carla_demo/ 2>/dev/null
rsync -az "$DESK":~/icepredict/logs/labels_rscdtex/ $SP/logs/labels_rscdtex/ 2>/dev/null
python3 $SP/sw/scripts/sim/extract_photos.py >> $ST 2>&1; python3 $SP/sw/scripts/sim/organize_media.py >> $ST 2>&1
python3 $SP/sw/scripts/analysis/make_figures.py >> $ST 2>&1; python3 $SP/sw/scripts/analysis/summarize_runs.py > /dev/null 2>&1
say "=== POST2 완료 — 전체 종료 ==="
