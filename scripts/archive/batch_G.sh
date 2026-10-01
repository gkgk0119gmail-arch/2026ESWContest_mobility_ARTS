#!/usr/bin/env bash
# batch_F 후: 조향각 환산 수정 반영해 '인식 + 주변 차량' 40 km/h 3건 재실행 (이전 3건은 마른 노면 2차 오탐으로 무효) → 최종 정리
SP=${SP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}; ST=/tmp/after_batch_status.txt; DESK=${DESK_HOST:-user@desk-host}
until grep -q "=== batch_F 완료" $ST 2>/dev/null; do sleep 15; done
echo "[$(date +%H:%M:%S)] === batch_G: 인식+주변차량 재실행 (조향각 환산 수정) ===" >> $ST
scp -q $SP/scripts/sim/carla_demo.py "$DESK":~/icepredict/code/scripts/sim/carla_demo.py
WEATHERS="ClearNoon WetNoon ClearSunset" SCEN="detect_traffic" bash $SP/scripts/sim/demo_batch.sh > /tmp/demo_batchG.log 2>&1
grep -E "^\[[0-9:]+\] ===|경고|정지|2차|비상|진입|모드|Traceback|dumped" /tmp/demo_batch_status.txt | cut -c1-150 >> $ST
python3 $SP/scripts/sim/extract_photos.py >> $ST 2>&1; python3 $SP/scripts/sim/organize_media.py >> $ST 2>&1
python3 $SP/scripts/analysis/make_figures.py >> $ST 2>&1; python3 $SP/scripts/analysis/summarize_runs.py > /dev/null 2>&1
echo "[$(date +%H:%M:%S)] === batch_G 완료 — 전부 끝 ===" >> $ST
