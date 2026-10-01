#!/usr/bin/env bash
# 돌고 있는 배치·검증 체인을 모두 멈춘다.
#
# 반드시 **별도 파일**로 둔다. 같은 내용을 `bash -c` 한 줄로 실행하면 명령줄 자체에 패턴이
# 들어가 `pkill -f` 가 자기 셸을 죽이고 exit 144 로 끝난다 (실제로 두 번 겪었다).
#
# 사용:  bash scripts/archive/stop_chains.sh          # 무엇이 돌고 있는지 보여주고 멈춘다
#        bash scripts/archive/stop_chains.sh --list   # 보기만 한다
set -u
PATTERNS='rerun_baseline\.sh|batch_5090\.sh|demo_batch_5090\.sh|after_5090_control\.sh|verify_evade|after_batch_chain|after_chain_night|after_night_more|after_all_organize|demo_batch\.sh|demo_auto\.sh'

echo "=== 돌고 있는 체인 ==="
ps -eo pid,lstart,args --no-headers | grep -E "$PATTERNS" | grep -v 'stop_chains' | grep -vE '(^| )grep ' | cut -c1-110
echo

[ "${1:-}" = "--list" ] && exit 0

echo "=== 멈추는 중 ==="
for sig in TERM TERM KILL; do
  mapfile -t pids < <(ps -eo pid,args --no-headers | grep -E "$PATTERNS" | grep -v 'stop_chains' | grep -vE '(^| )grep ' | awk '{print $1}')
  [ ${#pids[@]} -eq 0 ] && break
  for p in "${pids[@]}"; do kill -"$sig" "$p" 2>/dev/null; done
  sleep 2
done

echo "=== 원격(5090)의 데모 프로세스 ==="
ssh -o BatchMode=yes -o ConnectTimeout=8 ${RENDER_HOST:-user@render-host} \
  'for p in $(pgrep -f "carla_demo.p[y]"); do kill $p; done; echo "남은 데모: $(pgrep -f "carla_demo.p[y]" | wc -l)"' 2>/dev/null || echo "  (원격 접속 실패 — 건너뜀)"

echo
echo "=== 남은 것 ==="
ps -eo pid,args --no-headers | grep -E "$PATTERNS" | grep -v 'stop_chains' | grep -vE '(^| )grep ' | cut -c1-90 || echo "  없음"
echo
echo "CARLA 서버는 건드리지 않았다. 함께 내리려면 5090 에서 직접 종료할 것."
