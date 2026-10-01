#!/usr/bin/env bash
# batch_A 완료 후: v3 펌웨어(차선 유지·회피 제어) 굽기 → 확인 → 데모 v3 동기화 → 미인식(RTOS)+주변차량, 재실행, 눈 → 사진 → 정리
set -u
SP=${SP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}; DESK=${DESK_HOST:-user@desk-host}; ST=/tmp/after_batch_status.txt
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }
until grep -q "=== batch_A 완료 ===" $ST 2>/dev/null; do sleep 15; done
say "=== batch_B: v3 펌웨어 굽기 ==="
scp -q $SP/sw/fw/npu_lib/slip_core.h $SP/sw/fw/npu_lib/patch_fw_slip.py "$DESK":~/icepredict/sw/fw/npu_lib/ && scp -q $SP/sw/scripts/deploy/fw_redeploy.sh "$DESK":~/icepredict/code/sw/scripts/
timeout 30 ssh -o BatchMode=yes "$DESK" 'cd ~/icepredict/code && setsid nohup bash sw/scripts/deploy/fw_redeploy.sh > ~/icepredict/logs/fw_redeploy.log 2>&1 < /dev/null &' || true
for i in $(seq 1 60); do R=$(timeout 20 ssh -o BatchMode=yes "$DESK" 'cat ~/icepredict/logs/fw_redeploy_status.txt 2>/dev/null'); case "$R" in *"배포 완료"*|*"중단"*) break;; esac; sleep 10; done
echo "$R" | tail -4 | cut -c1-140 | tee -a "$ST"
case "$R" in *"배포 완료"*) ;; *) say "v3 펌웨어 배포 실패 — 중단"; exit 1;; esac
sleep 5
python3 - <<'PY' | tee -a "$ST"
import socket, struct
u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); u.settimeout(2.0)
u.sendto(struct.pack("<I13fI", 1, 0,0,0,0, 0.02, 6.94, 0,0, 999,999,999, 0,0, 0x30554D49), ("192.168.50.158", 5557))
try:
    d,_ = u.recvfrom(64); print("보드 v3 imu_reset 응답", len(d), "B", struct.unpack("<IBBBBffIff", d)[:4])
except socket.timeout:
    print("보드 v3 imu 무응답"); raise SystemExit(1)
PY
[ "${PIPESTATUS[0]}" = 0 ] || { say "v3 확인 실패 — 중단"; exit 1; }
say "=== 데모 v3 동기화 + 배치 스크립트 교체 ==="
scp -q $SP/sw/scripts/sim/carla_demo.py "$DESK":~/icepredict/code/sw/scripts/sim/carla_demo.py || { say "scp 실패"; exit 1; }
[ -f $SP/sw/scripts/sim/demo_batch.sh.new ] && mv $SP/sw/scripts/sim/demo_batch.sh.new $SP/sw/scripts/sim/demo_batch.sh
say "=== B1: 주변 차량 + 정차 차량, 카메라 미인식 → RTOS 회피/정지 (맑음 1회 먼저 확인) ==="
WEATHERS="ClearNoon" SCEN="miss_rtos_traffic" bash $SP/sw/scripts/sim/demo_batch.sh > /tmp/demo_batchB1.log 2>&1
grep -E "^\[[0-9:]+\] ===|경고|정지|2차|비상|진입|모드|Traceback|dumped" /tmp/demo_batch_status.txt | cut -c1-150 >> $ST
say "=== B2: 나머지 날씨 미인식+주변차량, 인식+주변차량 ==="
WEATHERS="WetNoon HardRainNoon ClearSunset CloudyNoon ClearNight MidRainyNight" SCEN="miss_rtos_traffic" bash $SP/sw/scripts/sim/demo_batch.sh > /tmp/demo_batchB2.log 2>&1
grep -E "^\[[0-9:]+\] ===|경고|정지|2차|비상|진입|모드|Traceback|dumped" /tmp/demo_batch_status.txt | cut -c1-150 >> $ST
WEATHERS="ClearNoon WetNoon ClearSunset" SCEN="detect_traffic" bash $SP/sw/scripts/sim/demo_batch.sh > /tmp/demo_batchB3.log 2>&1
grep -E "^\[[0-9:]+\] ===|경고|정지|2차|진입|Traceback|dumped" /tmp/demo_batch_status.txt | cut -c1-150 >> $ST
say "=== B3: 재실행 (밤·폭우해질녘·비오는밤 인식) + 눈 ==="
WEATHERS="ClearNight HardRainSunset MidRainyNight" SCEN="detect" bash $SP/sw/scripts/sim/demo_batch.sh > /tmp/demo_batchB4.log 2>&1
grep -E "^\[[0-9:]+\] ===|경고|정지|2차|진입|Traceback|dumped" /tmp/demo_batch_status.txt | cut -c1-150 >> $ST
WEATHERS="Snow" SCEN="detect miss_rtos_traffic" bash $SP/sw/scripts/sim/demo_batch.sh > /tmp/demo_batchB5.log 2>&1
grep -E "^\[[0-9:]+\] ===|경고|정지|2차|진입|snow|Traceback|dumped" /tmp/demo_batch_status.txt | cut -c1-150 >> $ST
say "=== 사진 추출 + 정리 ==="
python3 $SP/sw/scripts/sim/extract_photos.py >> $ST 2>&1
python3 $SP/sw/scripts/sim/organize_media.py >> $ST 2>&1
say "영상 $(ls $SP/logs/carla_demo/demo_*.mp4 | wc -l)개, 사진 $(ls $SP/logs/carla_demo/photos/*.jpg 2>/dev/null | wc -l)장"
say "=== 전체 완료 ==="
