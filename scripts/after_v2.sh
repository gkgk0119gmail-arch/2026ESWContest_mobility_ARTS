#!/usr/bin/env bash
# v2 검증이 끝나면 변동 스윕으로 이어진다.
set -u
cd /mnt/ssd/icepredict
say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a /tmp/after_batch_status.txt; }
say "=== 후속: v2 검증 종료 대기 ==="
for i in $(seq 1 120); do
  pgrep -f "verify_v2.sh|demo_batch_5090.sh" > /dev/null 2>&1 || break
  sleep 30
done
pgrep -f "verify_v2.sh|demo_batch_5090.sh" > /dev/null 2>&1 && { say "v2 미종료 — 후속 중단"; exit 1; }
say "v2 종료 확인 → 변동 스윕 시작"
SEEDS="7 11 23 41 59" KPHS="35 40 45" MUS="0.08" bash scripts/variation_sweep.sh
