#!/usr/bin/env bash
# 시연용 파이 쪽 실행: 안전 전용 RTOS(코어 3 격리·SCHED_FIFO) + 메인 프로그램 대역(죽으면 20 s 뒤 다시 띄움).
# 사용: sw/scripts/demo/pi_demo_up.sh <데스크톱IP>      (Ctrl-C 로 끝 — 코어 격리를 되돌린다)
# 현장 직결 랜선이면 데스크톱 IP 는 고정 IP (예: 10.0.0.1). 캠퍼스 망이면 데스크톱의 유선 IP.
set -u
DESK=${1:?데스크톱 IP 를 주세요}
D=$(cd "$(dirname "$0")/../../.." && pwd)
make -s -C "$D/sw/fw/pi_island" || exit 1
iso() { for s in system.slice user.slice init.scope; do sudo systemctl set-property --runtime $s AllowedCPUs=$1 >/dev/null 2>&1; done; }
cleanup() { kill $(jobs -p) 2>/dev/null; sudo pkill -f "[s]afety_island --udp 5600"; iso 0-3; echo "[pi_demo] 정리 끝 (코어 격리 해제)"; }
trap cleanup EXIT INT TERM
iso 0-2                                      # 다른 모든 작업을 코어 0~2 로
sudo systemd-run --scope -q --slice=island -p AllowedCPUs=3 \
  "$D/sw/fw/pi_island/safety_island" --udp 5600 --bind 0.0.0.0 --hello "$DESK:5700" --cpu 3 --prio 80 &
sleep 1
( while true; do
    PYTHONPATH="$D/sw/src" python3 -m icepredict.pi.pi_main --island 127.0.0.1:5600 --hello "$DESK:5701"
    echo "[pi_demo] 메인 프로그램 대역이 죽었다 — 20 s 뒤 다시 띄운다"; sleep 20
  done ) &
echo "[pi_demo] 안전 전용 RTOS + 메인 프로그램 대역 실행 중 (데스크톱 $DESK). Ctrl-C 로 끝."
wait
