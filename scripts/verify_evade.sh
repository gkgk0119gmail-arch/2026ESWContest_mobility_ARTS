#!/usr/bin/env bash
# 회피 시나리오 최종 검증. 앞 체인들이 모두 끝난 뒤 한 번 돌린다.
# 확인할 것: (1) 정차 차량이 같은 차로에 놓이는가, (2) 앞차 간격이 999 가 아닌 값으로 나오는가,
#            (3) 비상 제어 모드에 evade 가 등장하는가.
set -u
SP=/mnt/ssd/icepredict; H5=dlab27@165.132.135.75; ST=/tmp/after_batch_status.txt
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }

say "=== 회피 검증: 앞 체인 종료 대기 ==="
for i in $(seq 1 360); do
  pgrep -f "batch_5090.sh|after_5090_control.sh" > /dev/null 2>&1 || break
  sleep 30
done
pgrep -f "batch_5090.sh|after_5090_control.sh" > /dev/null 2>&1 && { say "앞 체인이 안 끝남 — 회피 검증 중단"; exit 1; }

ssh -o BatchMode=yes $H5 'ss -ltn | grep -q ":2000 "' || {
  say "CARLA 재기동"
  ssh -o BatchMode=yes $H5 'cd ~/CARLA && (DISPLAY= setsid nohup ./CarlaUE4.sh -quality-level=Epic -RenderOffScreen -nosound > ~/icepredict/logs/carla_server.log 2>&1 < /dev/null &)' || true
  for i in $(seq 1 18); do sleep 10; ssh -o BatchMode=yes $H5 'ss -ltn | grep -q ":2000 "' && break; done
}

say "=== 회피 검증 주행 (ClearNoon, 미인식+주변차량) ==="
DESK=$H5 WEATHERS=ClearNoon SCEN=miss_rtos_traffic VIEWS=split,bev \
  bash "$SP/scripts/demo_batch_5090.sh" > /tmp/demo_evade.log 2>&1

say "--- 결과 ---"
ssh -o BatchMode=yes $H5 'F=~/icepredict/logs/demo_ClearNoon_miss_rtos_traffic.log
  grep -E "틱 후 확인" "$F"
  echo "앞차 간격에 나온 값:"; grep -oE "gap_front_m.: [0-9.]+" "$F" | sort -u | head -5
  echo "비상 제어 모드:"; grep -oE "mode.: .[a-z_]+" "$F" | sort -u' | tee -a "$ST"

rsync -az $H5:~/icepredict/logs/carla_demo/ "$SP/logs/carla_demo/" 2>/dev/null
python3 "$SP/scripts/analyze_detection.py" > /dev/null 2>&1
python3 "$SP/scripts/summarize_runs.py" > /dev/null 2>&1
say "=== 회피 검증 완료 ==="
