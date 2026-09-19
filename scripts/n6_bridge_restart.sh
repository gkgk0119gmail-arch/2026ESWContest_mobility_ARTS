#!/usr/bin/env bash
# 브리지 재시작 (PID 파일 기반).
# 왜 별도 파일 + PID 파일인가: pkill/pgrep -f 는 호출한 셸의 **명령줄 전체**와도 매칭된다.
# 같은 명령줄에 브리지 실행 문자열(또는 그 문자열이 든 heredoc)이 있으면 자기 셸을 죽여
# 세션이 exit 144로 끊긴다. 실제로 두 번 겪었다.
set -u
GAP=${1:-150}
LOG=${2:-/tmp/n6_bridge.log}
PIDF=/tmp/n6_bridge.pid
if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
  kill "$(cat "$PIDF")" 2>/dev/null
  sleep 2
fi
cd /mnt/ssd/icepredict
nohup timeout 1200 python3 scripts/n6_bridge.py --gap-us "$GAP" > "$LOG" 2>&1 &
echo $! > "$PIDF"
sleep 12
cat "$LOG"
