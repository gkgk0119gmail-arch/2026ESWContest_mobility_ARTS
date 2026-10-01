#!/usr/bin/env bash
# 통합 최종 체인: batch_B 종료 대기 → v4 펌웨어 굽기·확인 → 데모/배치 스크립트 동기화 → 전체 시나리오 행렬(마찰 1.0, 조향 환산, 저마찰 지연)
#   → RSCD 텍스처 실험·라벨 → RSCD 인-더-루프 → 사진·정리·비교·그림·집계
set -u
SP=${SP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}; DESK=${DESK_HOST:-user@desk-host}; ST=/tmp/after_batch_status.txt
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }
run() { # run <WEATHERS> <SCEN> [KPH]
  KPH="${3:-40}" WEATHERS="$1" SCEN="$2" bash $SP/scripts/sim/demo_batch.sh > /tmp/demo_batch_all.log 2>&1
  grep -E "^\[[0-9:]+\] ===|경고|정지|2차|비상|진입|모드|충돌|통과|tex\]|snow\]|Traceback|dumped" /tmp/demo_batch_status.txt | cut -c1-150 >> $ST
}
until grep -q "=== 전체 완료 ===" $ST 2>/dev/null; do sleep 15; done
sleep 10
say "=== ALL-1: v4 펌웨어(저마찰 지연·제동 강화·시리얼 출력 제거) 굽기 ==="
scp -q $SP/fw/npu_lib/slip_core.h $SP/fw/npu_lib/patch_fw_slip.py "$DESK":~/icepredict/fw/npu_lib/
timeout 30 ssh -o BatchMode=yes "$DESK" 'cd ~/icepredict/code && setsid nohup bash scripts/deploy/fw_redeploy.sh > ~/icepredict/logs/fw_redeploy.log 2>&1 < /dev/null &' || true
for i in $(seq 1 60); do R=$(timeout 20 ssh -o BatchMode=yes "$DESK" 'cat ~/icepredict/logs/fw_redeploy_status.txt 2>/dev/null'); case "$R" in *"배포 완료"*|*"중단"*) break;; esac; sleep 10; done
echo "$R" | tail -3 | cut -c1-140 | tee -a "$ST"
case "$R" in *"배포 완료"*) ;; *) say "v4 배포 실패 — 중단"; exit 1;; esac
sleep 5
python3 - <<'PY' | tee -a "$ST"
import socket, struct
u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); u.settimeout(2.0)
u.sendto(struct.pack("<I13fI", 1, 0,0,0,0, 0.02, 6.94, 0,0, 999,999,999, 0,0, 0x30554D49), ("192.168.50.158", 5557))
try: d,_ = u.recvfrom(64); print("보드 v4 imu_reset 응답", len(d), "B")
except socket.timeout: print("보드 v4 imu 무응답"); raise SystemExit(1)
PY
[ "${PIPESTATUS[0]}" = 0 ] || { say "v4 확인 실패 — 중단"; exit 1; }
say "=== ALL-2: 데모·배치 스크립트 동기화 ==="
scp -q $SP/scripts/sim/carla_demo.py "$DESK":~/icepredict/code/scripts/sim/carla_demo.py || { say "scp 실패"; exit 1; }
[ -f $SP/scripts/sim/demo_batch.sh.new ] && mv $SP/scripts/sim/demo_batch.sh.new $SP/scripts/sim/demo_batch.sh
say "=== ALL-3: 검증 3회 (인식 / 미인식+주변차량 RTOS / 방어없음+주변차량) ==="
run ClearNoon detect; run ClearNoon miss_rtos_traffic; run ClearNoon nodefense_traffic
say "=== ALL-4: 1차 인식 (날씨 10종) ==="
run "WetNoon HardRainNoon ClearSunset CloudyNoon ClearNight HardRainSunset WetCloudySunset MidRainyNight SoftRainNoon" detect
say "=== ALL-5: 인식 + 주변 차량 ==="
run "ClearNoon WetNoon ClearSunset" detect_traffic
say "=== ALL-6: 미인식 → RTOS (교통 없음 / 있음) ==="
run "ClearNoon WetNoon ClearNight" miss_rtos
run "WetNoon HardRainNoon ClearSunset CloudyNoon ClearNight MidRainyNight" miss_rtos_traffic
say "=== ALL-7: 방어 없음 기준선 ==="
run "WetNoon ClearNight" nodefense_traffic; run "ClearNoon WetNoon ClearNight" nodefense
say "=== ALL-8: 60 km/h ==="
run "ClearNoon WetNoon" detect 60; run "ClearNoon" detect_traffic 60; run "ClearNoon" nodefense 60
say "=== ALL-9: 눈 ==="
run Snow "detect miss_rtos_traffic nodefense_traffic"
say "=== ALL-10: RSCD 실제 노면 텍스처 실험 + 2D 라벨 ==="
timeout 600 ssh -o BatchMode=yes "$DESK" 'cd ~/icepredict/code && timeout 560 ~/icepredict/venv/bin/python scripts/sim/carla_demo.py --weather ClearNoon --tag rscdtex_visual --views split,bev,lidar,lidar_sem --disable-primary --fusion local --slip-local --no-ice-render --road-texture ~/icepredict/dataset/rscd --export-labels ~/icepredict/logs/labels_rscdtex --traffic 6 --max-steps 650 2>&1 | grep -E "\[tex\]|\[world\]|진입|2차|정지|요약|Traceback|Error" | cut -c1-150' | tee -a $ST
for W in ClearNoon WetNoon; do
  say "--- $W: 합성 없이 엔진 RSCD 얼음 타일만 — 보드 NPU 인식 시험"
  timeout 30 ssh -o BatchMode=yes "$DESK" "cd ~/icepredict/code && setsid nohup timeout 900 ~/icepredict/venv/bin/python scripts/sim/carla_demo.py --weather $W --tag ${W}_rscdtex_detect --views split,bev,lidar,lidar_sem --fusion n6npu --no-ice-render --road-texture ~/icepredict/dataset/rscd > ~/icepredict/logs/demo_${W}_rscdtex_detect.log 2>&1 < /dev/null &" || true
  sleep 40; bash $SP/scripts/deploy/n6_bridge_restart.sh 150 /tmp/n6b_rscdtex_$W.log > /dev/null 2>&1
  for i in $(seq 1 60); do timeout 20 ssh -o BatchMode=yes "$DESK" "grep -qE '=== 요약|Traceback' ~/icepredict/logs/demo_${W}_rscdtex_detect.log" && break; sleep 10; done
  timeout 20 ssh -o BatchMode=yes "$DESK" "grep -E '\[tex\]|경고|진입|2차|정지|Traceback|무응답' ~/icepredict/logs/demo_${W}_rscdtex_detect.log | tail -6 | cut -c1-150" | tee -a $ST
  timeout 30 ssh -o BatchMode=yes "$DESK" 'for p in $(pgrep -f "carla_demo.p[y]"); do kill $p; done' || true; sleep 3
done
rsync -az "$DESK":~/icepredict/logs/carla_demo/ $SP/logs/carla_demo/ 2>/dev/null
rsync -az "$DESK":~/icepredict/logs/labels_rscdtex/ $SP/logs/labels_rscdtex/ 2>/dev/null
say "=== ALL-11: RSCD 실제 사진 → 보드 NPU 인-더-루프 (클래스별 300장) ==="
python3 $SP/scripts/deploy/rscd_in_the_loop.py --n 300 2>&1 | tail -7 | tee -a $ST
say "=== ALL-12: 사진·정리·비교 영상·그림·집계 ==="
python3 $SP/scripts/sim/extract_photos.py >> $ST 2>&1; python3 $SP/scripts/sim/organize_media.py >> $ST 2>&1
for W in ClearNoon WetNoon ClearNight Snow; do for V in bev split; do
  [ -f $SP/logs/carla_demo/demo_${W}_nodefense_traffic_$V.mp4 ] && [ -f $SP/logs/carla_demo/demo_${W}_miss_rtos_traffic_$V.mp4 ] && \
    python3 $SP/scripts/sim/compare_videos.py ${W}_nodefense_traffic ${W}_miss_rtos_traffic $V >> $ST 2>&1
done; done
mkdir -p "$SP/logs/carla_demo/정리/G_비교_방어없음_vs_RTOS"; cp -f $SP/logs/carla_demo/compare_*.mp4 "$SP/logs/carla_demo/정리/G_비교_방어없음_vs_RTOS/" 2>/dev/null
python3 $SP/scripts/analysis/make_figures.py >> $ST 2>&1; python3 $SP/scripts/analysis/summarize_runs.py > /dev/null 2>&1
say "영상 $(ls $SP/logs/carla_demo/demo_*.mp4 | wc -l)개, 비교 $(ls $SP/logs/carla_demo/compare_*.mp4 2>/dev/null | wc -l)개, 그림 $(ls $SP/logs/carla_demo/figures/*.jpg 2>/dev/null | wc -l)장, 사진 $(ls $SP/logs/carla_demo/photos/*.jpg 2>/dev/null | wc -l)장"
say "=== ALL 완료 — 모든 자동 작업 종료 ==="
