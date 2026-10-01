#!/usr/bin/env bash
# V7b 완료 후: 정차 차량을 자차 차로에 두는 수정 반영 → 미인식+주변차량 8종 재실행 → 비교 영상·정리
set -u
SP=${SP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}; DESK=${DESK_HOST:-user@desk-host}; ST=/tmp/after_batch_status.txt
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }
until grep -q "=== V7b 완료" $ST 2>/dev/null; do sleep 15; done
say "=== V7c: 정차 차량 자차 차로 배치 — 미인식+주변차량 8종 재실행 ==="
scp -q $SP/sw/scripts/sim/carla_demo.py "$DESK":~/icepredict/code/sw/scripts/sim/carla_demo.py
run() { KPH="${3:-40}" WEATHERS="$1" SCEN="$2" bash $SP/sw/scripts/sim/demo_batch.sh > /tmp/demo_batch_v7c.log 2>&1
  grep -E "^\[[0-9:]+\] ===|경고|정지|2차|비상|진입|모드|충돌|이탈|스핀|Traceback" /tmp/demo_batch_status.txt | cut -c1-150 >> $ST; }
run "ClearNoon WetNoon HardRainNoon ClearSunset CloudyNoon ClearNight MidRainyNight Snow" miss_rtos_traffic
run ClearNoon detect_traffic 60
for W in ClearNoon WetNoon ClearNight Snow; do for V in bev split; do
  [ -f $SP/logs/carla_demo/demo_${W}_nodefense_traffic_$V.mp4 ] && python3 $SP/sw/scripts/sim/compare_videos.py ${W}_nodefense_traffic ${W}_miss_rtos_traffic $V > /dev/null 2>&1
done; done
cp -f $SP/logs/carla_demo/compare_*.mp4 "$SP/logs/carla_demo/정리/G_비교_방어없음_vs_RTOS/" 2>/dev/null
python3 $SP/sw/scripts/sim/extract_photos.py >> $ST 2>&1; python3 $SP/sw/scripts/sim/organize_media.py >> $ST 2>&1; python3 $SP/sw/scripts/analysis/make_figures.py >> $ST 2>&1; python3 $SP/sw/scripts/analysis/summarize_runs.py > /dev/null 2>&1
say "=== V7c 완료 ==="
