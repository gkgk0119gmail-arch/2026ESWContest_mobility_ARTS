#!/usr/bin/env bash
# 전체 배치 완료 후: (1) RSCD 실제 노면 텍스처 엔진 적용 시각 확인 + 2D 분할 라벨 내보내기,
#                   (2) 합성 얼음 없이(엔진의 RSCD 얼음 타일만) 보드 NPU 모델이 인식하는지 (실제 질감 인식 시험)
set -u
SP=/mnt/ssd/icepredict; DESK=yax@165.132.135.77; ST=/tmp/after_batch_status.txt
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }
until grep -q "=== 전체 완료 ===" $ST 2>/dev/null; do sleep 15; done
sleep 20
say "=== batch_C: RSCD 실제 노면 텍스처 실험 ==="
scp -q $SP/scripts/carla_demo.py "$DESK":~/icepredict/code/scripts/carla_demo.py
timeout 600 ssh -o BatchMode=yes "$DESK" 'cd ~/icepredict/code && timeout 560 ~/icepredict/venv/bin/python scripts/carla_demo.py --weather ClearNoon --tag rscdtex_visual --views split,bev,lidar --disable-primary --fusion local --slip-local --no-ice-render --road-texture ~/icepredict/dataset/rscd --export-labels ~/icepredict/logs/labels_rscdtex --max-steps 650 2>&1 | grep -E "\[tex\]|\[world\]|진입|2차|정지|요약|Traceback|Error" | cut -c1-150' | tee -a $ST
for W in ClearNoon WetNoon; do
  say "=== $W: 합성 없이 엔진 RSCD 얼음 타일만 — 보드 NPU 인식 시험 ==="
  timeout 30 ssh -o BatchMode=yes "$DESK" "cd ~/icepredict/code && setsid nohup timeout 900 ~/icepredict/venv/bin/python scripts/carla_demo.py --weather $W --tag ${W}_rscdtex_detect --views split,bev,lidar --fusion n6npu --no-ice-render --road-texture ~/icepredict/dataset/rscd > ~/icepredict/logs/demo_${W}_rscdtex_detect.log 2>&1 < /dev/null &" || true
  sleep 40; bash $SP/scripts/n6_bridge_restart.sh 150 /tmp/n6b_rscdtex_$W.log > /dev/null 2>&1
  for i in $(seq 1 60); do timeout 20 ssh -o BatchMode=yes "$DESK" "grep -qE '=== 요약|Traceback' ~/icepredict/logs/demo_${W}_rscdtex_detect.log" && break; sleep 10; done
  timeout 20 ssh -o BatchMode=yes "$DESK" "grep -E '\[tex\]|경고|진입|2차|정지|Traceback|무응답' ~/icepredict/logs/demo_${W}_rscdtex_detect.log | tail -6 | cut -c1-150" | tee -a $ST
  timeout 30 ssh -o BatchMode=yes "$DESK" 'for p in $(pgrep -f "carla_demo.p[y]"); do kill $p; done' || true; sleep 3
done
rsync -az "$DESK":~/icepredict/logs/carla_demo/ $SP/logs/carla_demo/ 2>/dev/null
rsync -az "$DESK":~/icepredict/logs/labels_rscdtex/ $SP/logs/labels_rscdtex/ 2>/dev/null
python3 $SP/scripts/extract_photos.py >> $ST 2>&1; python3 $SP/scripts/organize_media.py >> $ST 2>&1
say "라벨 샘플 $(ls $SP/logs/labels_rscdtex/*_label.png 2>/dev/null | wc -l)장"
say "=== batch_C 완료 ==="
