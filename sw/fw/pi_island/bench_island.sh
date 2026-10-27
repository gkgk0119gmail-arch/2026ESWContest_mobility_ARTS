#!/usr/bin/env bash
# 안전 전용 RTOS 지연 측정: 파이 전체에 부하를 건 채 세 구성을 잰다. 결과: logs/pi_island/bench_*.json
# 3번 구성은 systemd 런타임 속성으로 다른 모든 작업을 코어 0~2 로 몰아낸다 (재부팅하면 사라진다. 끝나면 되돌린다).
set -u
SEC=${SEC:-120}; D=$(cd "$(dirname "$0")" && pwd); OUT=${OUT:-$D/../../../logs/pi_island}; mkdir -p "$OUT"
load_on() {
  for i in 1 2 3 4; do (while :; do :; done) & done                                   # 코어 4개 계산
  python3 -c "
import numpy as np
a=np.ones(64*1024*1024//8); b=np.empty_like(a)
while True: np.copyto(b,a); np.copyto(a,b)" & python3 -c "
import numpy as np
a=np.ones(64*1024*1024//8); b=np.empty_like(a)
while True: np.copyto(b,a); np.copyto(a,b)" &                                          # 메모리 대역 (캐시 밀어내기)
  (while :; do dd if=/dev/zero of=/tmp/island_dd bs=1M count=256 conv=fsync status=none; done) &   # 디스크 쓰기
}
load_off() { kill $(jobs -p) 2>/dev/null; wait 2>/dev/null; rm -f /tmp/island_dd; }
isolate() { for s in system.slice user.slice init.scope; do sudo systemctl set-property --runtime $s AllowedCPUs=$1 >/dev/null; done; }
trap 'load_off; isolate 0-3' EXIT
load_on; sleep 3
echo "== 1 일반 프로그램";            "$D/safety_island" --selftest $SEC --prio 0 --cpu -1 --json "$OUT/bench_1_default.json" 2>/dev/null
echo "== 2 실시간 우선순위 + 코어 3"; sudo "$D/safety_island" --selftest $SEC --prio 80 --cpu 3 --json "$OUT/bench_2_fifo.json" 2>/dev/null
echo "== 3 + 다른 작업 코어 0~2 로";  isolate 0-2; load_off; load_on; sleep 3
sudo systemd-run --scope -q --slice=island -p AllowedCPUs=3 "$D/safety_island" --selftest $SEC --prio 80 --cpu 3 --json "$OUT/bench_3_isolated.json" 2>/dev/null
