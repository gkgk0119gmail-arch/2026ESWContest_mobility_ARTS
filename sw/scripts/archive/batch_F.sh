#!/usr/bin/env bash
# batch_E 후: v4 펌웨어(시리얼 출력 제거, WCET 정직 측정) 굽기 → WCET 측정 주행 3회 → RSCD 실제 사진 인-더-루프 평가 → 비교 영상·그림 → 최종 집계
set -u
SP=${SP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}; DESK=${DESK_HOST:-user@desk-host}; ST=/tmp/after_batch_status.txt
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }
until grep -q "=== batch_E 완료" $ST 2>/dev/null; do sleep 15; done
say "=== batch_F: v4 펌웨어(WCET용) 굽기 ==="
scp -q $SP/sw/fw/npu_lib/slip_core.h $SP/sw/fw/npu_lib/patch_fw_slip.py "$DESK":~/icepredict/sw/fw/npu_lib/
timeout 30 ssh -o BatchMode=yes "$DESK" 'cd ~/icepredict/code && setsid nohup bash sw/scripts/deploy/fw_redeploy.sh > ~/icepredict/logs/fw_redeploy.log 2>&1 < /dev/null &' || true
for i in $(seq 1 60); do R=$(timeout 20 ssh -o BatchMode=yes "$DESK" 'cat ~/icepredict/logs/fw_redeploy_status.txt 2>/dev/null'); case "$R" in *"배포 완료"*|*"중단"*) break;; esac; sleep 10; done
echo "$R" | tail -3 | cut -c1-140 | tee -a "$ST"
case "$R" in *"배포 완료"*) ;; *) say "v4 배포 실패 — WCET 측정은 v3 로그로 대체"; esac
sleep 5
say "=== WCET 측정 주행 (미인식+주변차량 3회, 시리얼 출력 없음) ==="
WEATHERS="ClearNoon CloudyNoon ClearSunset" SCEN="miss_rtos_traffic" bash $SP/sw/scripts/sim/demo_batch.sh > /tmp/demo_batchF.log 2>&1
grep -E "^\[[0-9:]+\] ===|2차 방어\(IMU\)|WCET|모드|Traceback" /tmp/demo_batch_status.txt | cut -c1-150 >> $ST
say "=== RSCD 실제 사진 → 보드 NPU 인-더-루프 평가 (클래스별 300장) ==="
python3 $SP/sw/scripts/deploy/rscd_in_the_loop.py --n 300 2>&1 | tail -7 | tee -a $ST
say "=== 비교 영상 (방어 없음 vs RTOS) + 그림 ==="
for W in ClearNoon WetNoon ClearNight; do
  for V in bev split; do
    [ -f $SP/logs/carla_demo/demo_${W}_nodefense_traffic_$V.mp4 ] && [ -f $SP/logs/carla_demo/demo_${W}_miss_rtos_traffic_$V.mp4 ] && \
      python3 $SP/sw/scripts/sim/compare_videos.py ${W}_nodefense_traffic ${W}_miss_rtos_traffic $V >> $ST 2>&1
  done
done
python3 $SP/sw/scripts/sim/extract_photos.py >> $ST 2>&1; python3 $SP/sw/scripts/sim/organize_media.py >> $ST 2>&1
python3 $SP/sw/scripts/analysis/make_figures.py >> $ST 2>&1; python3 $SP/sw/scripts/analysis/summarize_runs.py > /dev/null 2>&1
mkdir -p "$SP/logs/carla_demo/정리/G_비교_방어없음_vs_RTOS"; cp -f $SP/logs/carla_demo/compare_*.mp4 "$SP/logs/carla_demo/정리/G_비교_방어없음_vs_RTOS/" 2>/dev/null
say "영상 $(ls $SP/logs/carla_demo/demo_*.mp4 | wc -l)개, 비교 $(ls $SP/logs/carla_demo/compare_*.mp4 2>/dev/null | wc -l)개, 그림 $(ls $SP/logs/carla_demo/figures/*.jpg 2>/dev/null | wc -l)장"
say "=== batch_F 완료 — 모든 자동 작업 종료 ==="
