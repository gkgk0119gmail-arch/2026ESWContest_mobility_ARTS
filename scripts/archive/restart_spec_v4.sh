#!/usr/bin/env bash
# 반사도 라벨 정의를 고치고 재수집→학습 체인을 다시 건다.
# 별도 파일인 이유: pkill/pgrep -f 는 호출한 셸의 명령줄과도 매칭돼 자기 자신을 죽인다 (여러 번 겪음).
set -u
LOGS=$HOME/icepredict/logs
CODE=$HOME/icepredict/code
P=$HOME/icepredict/venv/bin/python
N=${1:-8000}

echo "== 진행 중인 체인 정리 =="
for pat in "train_spec.p[y]" "spec_pipeline.s[h]" "spec_deploy_auto.s[h]" "carla_collect.p[y]"; do
  for pid in $(pgrep -f "$pat"); do kill "$pid" 2>/dev/null && echo "  kill $pid ($pat)"; done
done
sleep 3

echo "== 재수집 (목표 $N, 고친 라벨) =="
cd "$CODE" || exit 1
nohup $P -u scripts/sim/carla_collect.py --target "$N" --out "$HOME/icepredict/dataset/carla_v4" \
  > "$LOGS/collect_v4.log" 2>&1 < /dev/null &
echo "  collect_v4 시작 (pid $!)"

echo "== 수집 완료 대기 → 학습 → 배포 체인 =="
nohup bash -c "
  while pgrep -f 'carla_collect.p[y].*carla_v4' > /dev/null; do sleep 15; done
  grep -q '수집 완료' '$LOGS/collect_v4.log' || { echo '수집 실패'; exit 1; }
  grep -A 6 '반사도' '$LOGS/collect_v4.log' | tail -4
  cd '$CODE' && $P -u scripts/model/train_spec.py --carla \$HOME/icepredict/dataset/carla_v4 \
      --epochs 4 --steps-per-epoch 500 --rscd-test-per-class 300 > '$LOGS/train_spec.log' 2>&1
  bash '$CODE/scripts/deploy/spec_deploy_auto.sh'
" > "$LOGS/chain_v4.log" 2>&1 < /dev/null &
echo "  체인 시작 (pid $!)  로그: $LOGS/chain_v4.log"
