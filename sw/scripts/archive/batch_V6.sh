#!/usr/bin/env bash
set -u
SP=${SP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}; DESK=${DESK_HOST:-user@desk-host}; ST=/tmp/after_batch_status.txt
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }
say "=== V6: v_ch 17 m/s 펌웨어 굽기 + 최종 검증 ==="
scp -q $SP/sw/fw/npu_lib/slip_core.h $SP/sw/fw/npu_lib/patch_fw_slip.py "$DESK":~/icepredict/sw/fw/npu_lib/; scp -q $SP/sw/src/icepredict/pi/imu_slip.py "$DESK":~/icepredict/code/sw/src/icepredict/pi/imu_slip.py; scp -q $SP/sw/scripts/sim/carla_demo.py "$DESK":~/icepredict/code/sw/scripts/sim/carla_demo.py
timeout 30 ssh -o BatchMode=yes "$DESK" 'cd ~/icepredict/code && setsid nohup bash sw/scripts/deploy/fw_redeploy.sh > ~/icepredict/logs/fw_redeploy.log 2>&1 < /dev/null &' || true
for i in $(seq 1 60); do R=$(timeout 20 ssh -o BatchMode=yes "$DESK" 'cat ~/icepredict/logs/fw_redeploy_status.txt 2>/dev/null'); case "$R" in *"배포 완료"*|*"중단"*) break;; esac; sleep 10; done
echo "$R" | tail -1 | cut -c1-120 | tee -a $ST; case "$R" in *"배포 완료"*) ;; *) say "v6 배포 실패"; exit 1;; esac; sleep 5
run() { KPH="${3:-40}" WEATHERS="$1" SCEN="$2" bash $SP/sw/scripts/sim/demo_batch.sh > /tmp/demo_batch_v6.log 2>&1
  grep -E "^\[[0-9:]+\] ===|경고|정지|2차|비상|진입|모드|충돌|이탈|스핀|Traceback" /tmp/demo_batch_status.txt | cut -c1-150 >> $ST; }
run ClearNoon detect 60; run ClearNoon detect_traffic 60; run ClearNoon miss_rtos_traffic
python3 $SP/sw/scripts/sim/extract_photos.py >> $ST 2>&1; python3 $SP/sw/scripts/sim/organize_media.py >> $ST 2>&1; python3 $SP/sw/scripts/analysis/make_figures.py >> $ST 2>&1; python3 $SP/sw/scripts/analysis/summarize_runs.py > /dev/null 2>&1
say "영상 $(ls $SP/logs/carla_demo/demo_*.mp4 | wc -l)개, 비교 $(ls $SP/logs/carla_demo/compare_*.mp4 | wc -l)개, 그림 $(ls $SP/logs/carla_demo/figures/*.jpg | wc -l)장, 사진 $(ls $SP/logs/carla_demo/photos/*.jpg | wc -l)장, 라벨 $(ls $SP/logs/labels_rscdtex/*_label.png | wc -l)장"
say "=== V6 완료 — 최종 종료 ==="
