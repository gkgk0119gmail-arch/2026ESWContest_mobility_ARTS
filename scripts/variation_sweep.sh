#!/usr/bin/env bash
# 2차 방어 결과의 **표본을 만든다** — 지금까지는 사실상 n = 1 이었다.
#
# 왜: 날씨 8종으로 돌린 `miss_rtos_traffic` 결과가 바이트 단위로 같았다. CARLA 날씨는
# 물리에 영향을 주지 않으므로 당연하다. 그래서 "진입 → 미끄러짐 확정 1.78 s" 같은 수치가
# 평균이 아니라 한 번 잰 값이었다. 발표에서 분산을 물으면 답할 게 없다.
#
# 진짜 변동을 만들려면 물리에 닿는 값을 흔들어야 한다.
#   --ice-seed    주변 차량 배치와 얼음 외관이 바뀐다
#   --target-kph  진입 속도가 바뀐다 → 미끄러짐 크기와 제동거리가 바뀐다
#   --friction    빙판 마찰이 바뀐다 → 미끄러짐 강도가 바뀐다
#
# 사용: setsid nohup bash scripts/variation_sweep.sh > /tmp/varsweep.log 2>&1 &
#       SEEDS="7 11 23" KPHS="35 40 45" MUS="0.06 0.08" bash scripts/variation_sweep.sh
set -u
SP=/mnt/ssd/icepredict; H5=dlab27@165.132.135.75; ST=/tmp/after_batch_status.txt
PY='$HOME/miniconda3/envs/icepredict/bin/python'
BOARD=192.168.50.158
SEEDS=${SEEDS:-"7 11 23 41 59"}
KPHS=${KPHS:-"35 40 45"}
MUS=${MUS:-"0.08"}
say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }

carla_ok(){ timeout 15 ssh -o BatchMode=yes $H5 'ss -ltn | grep -q ":2000 "'; }
restart_carla(){
  timeout 30 ssh -o BatchMode=yes $H5 "pkill -9 -f '[C]arlaUE4' 2>/dev/null; sleep 4; true" || true
  timeout 30 ssh -o BatchMode=yes $H5 'cd ~/CARLA && (DISPLAY= setsid nohup ./CarlaUE4.sh -quality-level=Epic -RenderOffScreen -nosound > ~/icepredict/logs/carla_server.log 2>&1 < /dev/null &)' || true
  for i in $(seq 1 24); do sleep 10; carla_ok && { say "CARLA 재기동 완료"; return 0; }; done
  say "CARLA 재기동 실패"; return 1
}

say "=== 변동 스윕 시작 (시드 $(echo $SEEDS | wc -w) × 속도 $(echo $KPHS | wc -w) × 마찰 $(echo $MUS | wc -w)) ==="
carla_ok || restart_carla || exit 1

N=0
for MU in $MUS; do
for KPH in $KPHS; do
for SD in $SEEDS; do
  TAG="var_s${SD}_k${KPH}_mu${MU/./}"
  carla_ok || restart_carla || { say "CARLA 없음 — 중단"; break 3; }
  ping -c 1 -W 2 "$BOARD" > /dev/null 2>&1 || { say "보드 무응답 — 중단"; break 3; }

  say "=== $TAG (시드 $SD, ${KPH} km/h, µ $MU) ==="
  timeout 30 ssh -o BatchMode=yes $H5 "cd ~/icepredict/code && setsid nohup timeout 900 $PY scripts/carla_demo.py \
    --target-kph $KPH --weather ClearNoon --tag $TAG --views split \
    --disable-primary --fusion n6npu --traffic 8 --lead-stop 45 \
    --friction $MU --patch-len 100 --ice-seed $SD \
    > ~/icepredict/logs/demo_$TAG.log 2>&1 < /dev/null &" || true
  sleep 25
  bash "$SP/scripts/n6_bridge_restart.sh" 150 "/tmp/n6b_$TAG.log" "165.132.135.75" > /dev/null 2>&1

  DIED=0
  for i in $(seq 1 90); do
    timeout 20 ssh -o BatchMode=yes $H5 "grep -qE '=== 요약|Traceback' ~/icepredict/logs/demo_$TAG.log 2>/dev/null" && break
    timeout 20 ssh -o BatchMode=yes $H5 "grep -qE 'TimeoutException|time-out of|terminate called|Segmentation fault' ~/icepredict/logs/demo_$TAG.log 2>/dev/null" \
      && { DIED=1; say "$TAG: CARLA 무응답으로 죽음"; break; }
    if [ "$i" -gt 3 ] && ! timeout 20 ssh -o BatchMode=yes $H5 'pgrep -f "carla_demo.p[y]" > /dev/null'; then
      DIED=1; say "$TAG: 프로세스 소멸 — 죽은 것으로 본다"; break
    fi
    sleep 10
  done
  [ "$DIED" = 1 ] && { restart_carla; continue; }

  timeout 20 ssh -o BatchMode=yes $H5 "grep -E '2차 방어|미끄러짐|정지 완료|비상 제어' ~/icepredict/logs/demo_$TAG.log | tail -3 | cut -c1-130" | tee -a "$ST"
  timeout 20 ssh -o BatchMode=yes $H5 'for p in $(pgrep -f "carla_demo.p[y]"); do kill $p; done' || true
  N=$((N+1)); sleep 3
done; done; done

say "=== 회수 ($N 주행) ==="
rsync -az $H5:~/icepredict/logs/carla_demo/ "$SP/logs/carla_demo/" 2>/dev/null
python3 "$SP/scripts/merge_wcet.py" > /dev/null 2>&1
python3 "$SP/scripts/variation_report.py" 2>&1 | tail -30 | tee -a "$ST"
say "=== 변동 스윕 완료 ==="
