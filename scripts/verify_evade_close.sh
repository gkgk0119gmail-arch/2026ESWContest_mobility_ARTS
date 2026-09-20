#!/usr/bin/env bash
# 회피(evade) 동작을 실제로 끌어내는 주행.
# 간격 수정 뒤 앞차가 69.6 m 로 보이기 시작했지만, µ=0.08 에서 38 km/h 제동거리가 약 71 m 라
# 보드는 "차선 유지 후 정지"로 충분하다고 판단했다 (lane_keep). 맞는 판단이다.
# 회피를 보려면 간격이 제동거리보다 확실히 짧아야 한다 → 정차 차량을 빙판 중심 +30 m 로 당긴다.
# 서사도 더 현실적이다: "앞차가 이미 빙판 위에서 멈춰 서 있다."
set -u
SP=/mnt/ssd/icepredict; H5=dlab27@165.132.135.75; ST=/tmp/after_batch_status.txt
PY='$HOME/miniconda3/envs/icepredict/bin/python'
BOARD=192.168.50.158
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }

say "=== 회피 강제 주행: 앞 체인 전부 종료 대기 ==="
for i in $(seq 1 400); do
  pgrep -f "batch_5090.sh|after_5090_control.sh|verify_evade.sh" > /dev/null 2>&1 || break
  sleep 30
done
pgrep -f "batch_5090.sh|after_5090_control.sh|verify_evade.sh" > /dev/null 2>&1 && { say "앞 체인 미종료 — 중단"; exit 1; }

ssh -o BatchMode=yes $H5 'ss -ltn | grep -q ":2000 "' || {
  say "CARLA 재기동"
  ssh -o BatchMode=yes $H5 'cd ~/CARLA && (DISPLAY= setsid nohup ./CarlaUE4.sh -quality-level=Epic -RenderOffScreen -nosound > ~/icepredict/logs/carla_server.log 2>&1 < /dev/null &)' || true
  for i in $(seq 1 18); do sleep 10; ssh -o BatchMode=yes $H5 'ss -ltn | grep -q ":2000 "' && break; done
}
ping -c 1 -W 2 "$BOARD" > /dev/null 2>&1 || { say "보드 ping 실패 — 중단"; exit 1; }

for LS in 30 45; do
  TAG="ClearNoon_evade_lead${LS}"
  say "=== $TAG (정차 차량 빙판 중심 +${LS} m) ==="
  ssh -o BatchMode=yes $H5 "cd ~/icepredict/code && setsid nohup timeout 900 $PY scripts/carla_demo.py \
    --target-kph 40 --weather ClearNoon --tag $TAG --views split,bev \
    --disable-primary --fusion n6npu --traffic 8 --lead-stop $LS --friction 0.08 --patch-len 100 \
    > ~/icepredict/logs/demo_$TAG.log 2>&1 < /dev/null &" || true
  sleep 25
  bash "$SP/scripts/n6_bridge_restart.sh" 150 "/tmp/n6b_$TAG.log" "165.132.135.75" > /dev/null 2>&1
  for i in $(seq 1 90); do
    ssh -o BatchMode=yes $H5 "grep -qE '=== 요약|Traceback' ~/icepredict/logs/demo_$TAG.log 2>/dev/null" && break
    sleep 10
  done
  ssh -o BatchMode=yes $H5 "F=~/icepredict/logs/demo_$TAG.log
    echo '  앞차 간격:'; grep -oE 'gap_front_m.: [0-9.]+' \$F | sort -u | head -4
    echo '  비상 제어 모드:'; grep -oE 'mode.: .[a-z_]+' \$F | sort -u
    echo '  충돌:'; grep -c 'collision' \$F" | tee -a "$ST"
  ssh -o BatchMode=yes $H5 'for p in $(pgrep -f "carla_demo.p[y]"); do kill $p; done' || true
  sleep 3
done

rsync -az $H5:~/icepredict/logs/carla_demo/ "$SP/logs/carla_demo/" 2>/dev/null
python3 "$SP/scripts/analyze_detection.py" > /dev/null 2>&1
python3 "$SP/scripts/summarize_runs.py" > /dev/null 2>&1
say "=== 회피 강제 주행 완료 ==="
