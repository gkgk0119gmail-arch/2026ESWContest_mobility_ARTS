#!/usr/bin/env bash
# 결선 현장 — 파이 쪽 한 번에 켜기. 폰 핫스팟에 파이와 데스크톱(USB 와이파이 동글)을 같이 붙인 뒤 실행한다.
# 데스크톱 IP 는 mDNS(yax-desktop.local)로 찾는다. 못 찾으면 인자로 준다: venue_pi.sh <데스크톱IP>
# 켜는 것: 2차 방어 격리 코어 + 메인 프로그램 대역(pi_demo_up.sh), RealSense 1차 방어 창(판정을 데스크톱으로 보낸다).
set -u
D=$(cd "$(dirname "$0")/../../.." && pwd)
DESK=${1:-}
if [ -z "$DESK" ]; then
  for i in $(seq 30); do
    DESK=$(getent ahostsv4 yax-desktop.local | awk 'NR==1{print $1}')
    [ -n "$DESK" ] && break
    echo "[venue] yax-desktop.local 찾는 중... ($i/30) — 데스크톱이 같은 핫스팟에 붙었는지 확인"; sleep 2
  done
fi
[ -z "$DESK" ] && { echo "[venue] 데스크톱을 못 찾았다. 데스크톱 IP 를 인자로 주세요"; exit 1; }
echo "[venue] 데스크톱 $DESK"
export DISPLAY=${DISPLAY:-:0} XAUTHORITY=${XAUTHORITY:-/home/pi/.Xauthority} WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-0} XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/1000}
( for i in 1 2 3 4 5 6; do                    # 카메라가 막 풀린 직후면 못 찾는다 — 몇 번 다시
    python3 "$D/sw/scripts/demo/realsense_primary.py" --send "$DESK:5702" > /tmp/venue_rs.log 2>&1 < /dev/null
    grep -q "찾지 못했다" /tmp/venue_rs.log || break; sleep 5
  done ) &
exec "$D/sw/scripts/demo/pi_demo_up.sh" "$DESK"
