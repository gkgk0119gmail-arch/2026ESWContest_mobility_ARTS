#!/usr/bin/env bash
# 속도에 따른 2차 방어 지연의 **원인을 가른다**.
#
# 변동 스윕에서 35 km/h 의 "빙판 진입 → 미끄러짐 확정"이 4.23 s, 40 km/h 는 1.78 s 였다.
# 두 가지 설명이 가능하다.
#   (가) 감지기가 둔하다 — 임계값이 고정 0.3 g / 0.35 rad/s 라 저속에서 잔차가 못 닿는다
#   (나) 물리가 늦다   — 저속에서는 요구 횡가속도 v²/R 이 작아 실제 미끄러짐 자체가 늦게 시작된다
# 둘은 처방이 완전히 다르다. (가)면 임계값을 속도로 정규화해야 하고, (나)면 감지기는 죄가 없다.
#
# 가르는 방법: --log-dyn 으로 원시 동역학을 남기고, 오프라인에서
#   미끄러짐 "시작"(잔차가 바닥 잡음을 벗어난 순간) 과 "확정"(임계 N샘플) 을 따로 잰다.
#   시작이 늦으면 (나), 시작은 같은데 확정이 늦으면 (가).
set -u
SP=${SP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}; H5=${RENDER_HOST:-user@render-host}; ST=/tmp/after_batch_status.txt
PY='$HOME/miniconda3/envs/icepredict/bin/python'
BOARD=192.168.50.158
KPHS=${KPHS:-"30 35 40 45 50"}
say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }
carla_ok(){ timeout 15 ssh -o BatchMode=yes $H5 'ss -ltn | grep -q ":2000 "'; }
restart_carla(){
  timeout 30 ssh -o BatchMode=yes $H5 "pkill -9 -f '[C]arlaUE4' 2>/dev/null; sleep 4; true" || true
  timeout 30 ssh -o BatchMode=yes $H5 'cd ~/CARLA && (DISPLAY= setsid nohup ./CarlaUE4.sh -quality-level=Epic -RenderOffScreen -nosound > ~/icepredict/logs/carla_server.log 2>&1 < /dev/null &)' || true
  for i in $(seq 1 24); do sleep 10; carla_ok && { say "CARLA 재기동 완료"; return 0; }; done
  say "CARLA 재기동 실패"; return 1
}

say "=== 속도별 동역학 스윕 시작 ($KPHS km/h × 빙판/대조군) ==="
carla_ok || restart_carla || exit 1
# 빙판 주행과 **같은 속도의 빙판 없는 대조군**을 짝지어 돌린다.
# 임계값을 속도로 정규화하면 저속에서 민감해진다 — 그 대가로 오경보가 느는지 재려면
# 같은 속도의 정상 주행 잔차가 어디까지 올라가는지를 알아야 한다.
for CASE in ice ctrl; do
for KPH in $KPHS; do
  if [ "$CASE" = ctrl ]; then EXTRA="--control-no-ice"; TAG="dync_k${KPH}"; CSV="dync_k${KPH}.csv"
  else EXTRA="--friction 0.08 --patch-len 140"; TAG="dyn_k${KPH}"; CSV="dyn_k${KPH}.csv"; fi
  carla_ok || restart_carla || { say "CARLA 없음 — 중단"; break 2; }
  ping -c 1 -W 2 "$BOARD" > /dev/null 2>&1 || { say "보드 무응답 — 중단"; break 2; }
  say "=== $TAG (${KPH} km/h) ==="
  timeout 30 ssh -o BatchMode=yes $H5 "cd ~/icepredict/code && setsid nohup timeout 900 $PY scripts/sim/carla_demo.py \
    --target-kph $KPH --weather ClearNoon --tag $TAG --views split \
    --disable-primary --fusion n6npu --traffic 0 --ice-seed 7 $EXTRA \
    --log-dyn ~/icepredict/logs/$CSV \
    > ~/icepredict/logs/demo_$TAG.log 2>&1 < /dev/null &" || true
  sleep 25
  bash "$SP/scripts/deploy/n6_bridge_restart.sh" 150 "/tmp/n6b_$TAG.log" "${RENDER_IP:-render-host}" > /dev/null 2>&1
  DIED=0
  for i in $(seq 1 90); do
    timeout 20 ssh -o BatchMode=yes $H5 "grep -qE '=== 요약|Traceback' ~/icepredict/logs/demo_$TAG.log 2>/dev/null" && break
    timeout 20 ssh -o BatchMode=yes $H5 "grep -qE 'TimeoutException|time-out of|terminate called|Segmentation fault' ~/icepredict/logs/demo_$TAG.log 2>/dev/null" \
      && { DIED=1; say "$TAG: CARLA 무응답으로 죽음"; break; }
    if [ "$i" -gt 3 ] && ! timeout 20 ssh -o BatchMode=yes $H5 'pgrep -f "carla_demo.p[y]" > /dev/null'; then
      DIED=1; say "$TAG: 프로세스 소멸"; break
    fi
    sleep 10
  done
  [ "$DIED" = 1 ] && { restart_carla; continue; }
  timeout 20 ssh -o BatchMode=yes $H5 "grep -E '빙판 진입|미끄러짐|정지 완료' ~/icepredict/logs/demo_$TAG.log | tail -3 | cut -c1-130" | tee -a "$ST"
  timeout 20 ssh -o BatchMode=yes $H5 'for p in $(pgrep -f "carla_demo.p[y]"); do kill $p; done' || true
  sleep 3
done; done
say "=== 회수 ==="
rsync -az $H5:~/icepredict/logs/carla_demo/ "$SP/logs/carla_demo/" 2>/dev/null
rsync -az $H5:~/icepredict/logs/dyn*_k*.csv "$SP/logs/" 2>/dev/null
python3 "$SP/scripts/analysis/merge_wcet.py" > /dev/null 2>&1
say "=== 속도별 동역학 스윕 완료 ==="
