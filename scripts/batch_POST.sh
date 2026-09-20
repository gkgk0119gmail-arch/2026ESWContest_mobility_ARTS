#!/usr/bin/env bash
# TAIL 완료 후: 60 km/h 주행 로그로 특성속도 보정 → slip_core/imu_slip 갱신 → v5 굽기 → 60 km/h 재실행 + 40 km/h 검증 → 정리
set -u
SP=/mnt/ssd/icepredict; DESK=yax@165.132.135.77; ST=/tmp/after_batch_status.txt
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }
until grep -q "=== TAIL 완료" $ST 2>/dev/null; do sleep 15; done
say "=== POST-1: 동역학 로그 수집 (60·40 km/h, 방어 없음, 빙판 멀리) ==="
scp -q $SP/scripts/carla_demo.py "$DESK":~/icepredict/code/scripts/carla_demo.py
for K in 60 40; do
  timeout 400 ssh -o BatchMode=yes "$DESK" "cd ~/icepredict/code && timeout 380 ~/icepredict/venv/bin/python scripts/carla_demo.py --weather ClearNoon --tag dyncal_$K --target-kph $K --disable-primary --no-secondary --fusion local --slip-local --no-video --patch-ahead 400 --max-steps 900 --log-dyn ~/icepredict/logs/dyn_$K.csv 2>&1 | grep -E '요약|Traceback' | head -2"
  scp -q "$DESK":~/icepredict/logs/dyn_$K.csv $SP/logs/ && say "dyn_$K.csv $(wc -l < $SP/logs/dyn_$K.csv)줄"
done
say "=== POST-2: 특성속도 맞춤 ==="
OUT=$(python3 $SP/scripts/fit_vch.py $SP/logs/dyn_60.csv $SP/logs/dyn_40.csv 2>&1); echo "$OUT" | tee -a $ST
VCH=$(echo "$OUT" | grep -oE "^VCH=[0-9.]+" | cut -d= -f2)
if [ -n "$VCH" ]; then
  sed -i -E "s|#define SLIP_V_CH_MPS +[0-9.]+f|#define SLIP_V_CH_MPS      ${VCH}f|" $SP/fw/npu_lib/slip_core.h
  sed -i -E "s|v_ch_mps: float = [0-9.]+|v_ch_mps: float = ${VCH}|" $SP/src/icepredict/pi/imu_slip.py
  python3 $SP/fw/npu_lib/test_slip_core.py 2>&1 | tail -1 | tee -a $ST
  say "v_ch = $VCH m/s 적용"
else
  say "v_ch 맞춤 실패 — 기본 20 m/s 유지"
fi
say "=== POST-3: v5 펌웨어 굽기 ==="
scp -q $SP/fw/npu_lib/slip_core.h $SP/fw/npu_lib/patch_fw_slip.py "$DESK":~/icepredict/fw/npu_lib/
scp -q $SP/src/icepredict/pi/imu_slip.py "$DESK":~/icepredict/code/src/icepredict/pi/imu_slip.py
timeout 30 ssh -o BatchMode=yes "$DESK" 'cd ~/icepredict/code && setsid nohup bash scripts/fw_redeploy.sh > ~/icepredict/logs/fw_redeploy.log 2>&1 < /dev/null &' || true
for i in $(seq 1 60); do R=$(timeout 20 ssh -o BatchMode=yes "$DESK" 'cat ~/icepredict/logs/fw_redeploy_status.txt 2>/dev/null'); case "$R" in *"배포 완료"*|*"중단"*) break;; esac; sleep 10; done
echo "$R" | tail -2 | cut -c1-140 | tee -a "$ST"
case "$R" in *"배포 완료"*) ;; *) say "v5 배포 실패 — 중단"; exit 1;; esac
sleep 5
say "=== POST-4: 60 km/h 재실행 + 40 km/h 검증 ==="
run() { KPH="${3:-40}" WEATHERS="$1" SCEN="$2" bash $SP/scripts/demo_batch.sh > /tmp/demo_batch_post.log 2>&1
  grep -E "^\[[0-9:]+\] ===|경고|정지|2차|비상|진입|모드|충돌|이탈|스핀|Traceback|dumped" /tmp/demo_batch_status.txt | cut -c1-150 >> $ST; }
run "ClearNoon WetNoon" detect 60; run ClearNoon detect_traffic 60; run ClearNoon nodefense 60
run ClearNoon miss_rtos; run ClearNoon miss_rtos_traffic; run ClearNoon detect_traffic
say "=== POST-5: 정리·집계 ==="
python3 $SP/scripts/extract_photos.py >> $ST 2>&1; python3 $SP/scripts/organize_media.py >> $ST 2>&1
python3 $SP/scripts/make_figures.py >> $ST 2>&1; python3 $SP/scripts/summarize_runs.py > /dev/null 2>&1
say "=== POST 완료 — 이제 정말 끝 ==="
