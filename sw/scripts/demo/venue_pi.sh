#!/usr/bin/env bash
# 결선 현장 — 파이 쪽 한 번에 켜기. 폰 핫스팟에 파이와 데스크톱(USB 와이파이 동글)을 같이 붙인 뒤 실행한다.
# 데스크톱 IP 는 같은 망을 훑어 SSH 호스트 키로 찾는다 (mDNS 는 도커 주소를 줄 때가 있다). 못 찾으면 인자로 준다: venue_pi.sh <데스크톱IP>
# 켜는 것: 2차 방어 격리 코어 + 메인 프로그램 대역(pi_demo_up.sh), RealSense 1차 방어 창(판정을 데스크톱으로 보낸다).
set -u
D=$(cd "$(dirname "$0")/../../.." && pwd)
[ -f "$D/sw/scripts/demo/venue.env" ] && . "$D/sw/scripts/demo/venue.env"
export DESK_USER=${DESK_USER:-yax} DESK_HOSTKEY=${DESK_HOSTKEY:-}; PI_WIRED_PROFILE=${PI_WIRED_PROFILE:-venue-wired}
# 랜선 직결(권장): 데스크톱 랜 포트에는 원래 고정 주소가 자동으로 붙는다 → 파이에 같은 대역 주소를 준다 (프로필 이름은 venue.env 의 PI_WIRED_PROFILE).
# 연구실 주소·계정은 저장소 밖 sw/scripts/demo/venue.env 에 둔다 (.gitignore).
# 폰 핫스팟은 기기끼리 중계가 느려(2026-10-30 측정 평균 1.3 s, 손실 최대 90 %) 2차 방어 20 ms 루프가 끊긴다 — 시연 통신에 쓰지 않는다.
if [ "$(cat /sys/class/net/eth0/carrier 2>/dev/null)" = 1 ]; then
  sudo ethtool --set-eee eth0 eee off 2>/dev/null          # EEE 가 켜져 있으면 데스크톱 I210 이 링크를 못 잡은 적이 있다
  nmcli -t -f NAME con show --active | grep -qx "$PI_WIRED_PROFILE" || sudo nmcli con up "$PI_WIRED_PROFILE" >/dev/null 2>&1
fi
DESK=${1:-}
find_desk() {   # mDNS 는 데스크톱의 도커·캠퍼스 주소를 줄 때가 있어 못 믿는다 → 같은 망의 주소를 훑어 SSH 로 우리 데스크톱(호스트 키)을 확인
  local cidr net
  for cidr in $(ip -4 -o addr show scope global | awk '$2!="tailscale0"{print $4}'); do
    net=$(python3 -c "import ipaddress,sys; n=ipaddress.ip_network(sys.argv[1],strict=False); print(' '.join(str(h) for h in list(n.hosts())[:254]))" "$cidr")
    printf '%s\n' $net | xargs -P 64 -I{} sh -c 'ssh -o BatchMode=yes -o ConnectTimeout=2 ${DESK_HOSTKEY:+-o HostKeyAlias=$DESK_HOSTKEY} $DESK_USER@{} true 2>/dev/null && echo {}; exit 0' | head -1
  done | head -1
}
if [ -z "$DESK" ]; then
  for i in $(seq 15); do
    DESK=$(find_desk); [ -n "$DESK" ] && break
    echo "[venue] 데스크톱 찾는 중... ($i/15) — 랜선이 데스크톱 위쪽 포트에 꽂혔는지 확인"; sleep 2
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
