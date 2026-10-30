#!/usr/bin/env bash
# 결선 현장 — 데스크톱 쪽 한 번에 켜기: CARLA → 시연 프로그램(파이 격리 코어·메인 대역·RealSense 의 인사를 기다린다).
# 사용: venue_desktop.sh            (2차 방어 — 사람이 Moza 로 운전, RealSense 안 받음, 키: C 영점 · D 2차 방어 · R 다시 · K 파이 메인 죽이기 · V 시점 · I 빙판 · T 3인칭)
#       venue_desktop.sh auto off   (자동 주행 2차 방어 OFF)   venue_desktop.sh auto on   (ON)
#       venue_desktop.sh assist     (2차 방어 — 조향 자동, 페달만 사람)
#       venue_desktop.sh first      (1차 방어: 자동 주행 중 RealSense 로 빙판 사진을 비추면 빙판 앞 정지)
set -u
cd ~/icepredict_demo
PY=~/miniconda3/envs/carla/bin/python
if ! pgrep -f "CarlaUE4-Linux-Shippin[g]" >/dev/null; then
  echo "[venue] CARLA 켜는 중"; (cd ~/CARLA && ./CarlaUE4.sh -carla-rpc-port=2000 -quality-level=Epic -RenderOffScreen -nosound > /tmp/carla.log 2>&1 &)
  for i in $(seq 60); do $PY -c "import carla; c=carla.Client('localhost',2000); c.set_timeout(2); c.get_world()" 2>/dev/null && break; sleep 2; done
fi
for p in $(pgrep -f "sw/scripts/demo/moza_dem[o]"); do kill $p; done; sleep 2
COMMON="--island listen:5700 --pi-main listen:5701 --fullscreen --show-ice"
case "${1:-live}" in
  auto)  exec $PY sw/scripts/demo/moza_demo.py --auto --defense "${2:-on}" --primary none $COMMON --max-time 30 ;;   # 2차 방어 장면엔 RealSense 를 안 받는다
  first) exec $PY sw/scripts/demo/moza_demo.py --auto --defense off --primary listen:5702 --primary-stop --patch-ahead 250 --max-time 40 $COMMON ;;
  assist) exec $PY sw/scripts/demo/moza_demo.py --assist-steer --primary none $COMMON ;;   # 2차 방어: 조향은 자동(차선 유지), 페달만 사람
  *)     exec $PY sw/scripts/demo/moza_demo.py --primary none $COMMON ;;                  # 2차 방어: 사람이 Moza 로 운전 (카메라가 못 본 상황 → RealSense 안 받음)
esac
