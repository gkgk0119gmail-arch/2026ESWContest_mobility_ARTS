#!/usr/bin/env bash
# 마지막: 모든 주행·평가가 끝나면 문서와 그림을 실측에 다시 맞추고 노션에 올린다.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/../../.." || exit 1
say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a /tmp/after_batch_status.txt; }
say "=== 후속8: after_yawlag 종료 대기 ==="
for i in $(seq 1 90); do pgrep -f "after_yawla[g].sh" > /dev/null 2>&1 && break; sleep 10; done
for i in $(seq 1 500); do pgrep -f "after_yawla[g].sh" > /dev/null 2>&1 || break; sleep 30; done
say "=== 문서·그림 실측 재정렬 ==="
python3 sw/scripts/analysis/finalize_docs.py 2>&1 | tail -40 | tee -a /tmp/after_batch_status.txt
say "=== 단위 시험 ==="
python3 sw/scripts/analysis/test_context_gates.py 2>&1 | tail -1 | tee -a /tmp/after_batch_status.txt
python3 sw/fw/npu_lib/test_slip_core.py 2>&1 | tail -1 | tee -a /tmp/after_batch_status.txt
python3 sw/scripts/sim/test_follow_lane.py  2>&1 | tail -1 | tee -a /tmp/after_batch_status.txt
say "=== 후속8 완료 — 노션 게시는 사람이 확인 후 ==="
