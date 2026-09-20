#!/usr/bin/env bash
# 실사진 대규모 보드 평가: test_50k 먼저, vali_20k 를 덧붙인다.
set -u
cd /mnt/ssd/icepredict
echo "[$(date +%H:%M:%S)] === test_50k 수집 ==="
python3 scripts/rscd_board_eval.py --n 4000 --split test_50k --seed 0 > /tmp/board_eval_1.log 2>&1
echo "[$(date +%H:%M:%S)] === vali_20k 덧붙이기 ==="
python3 scripts/rscd_board_eval.py --n 2500 --split vali_20k --seed 1 --append > /tmp/board_eval_2.log 2>&1
echo "[$(date +%H:%M:%S)] === 완료: $(wc -l < logs/rscd_board_samples.jsonl)장 ==="
