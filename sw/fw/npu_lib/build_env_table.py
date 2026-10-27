#!/usr/bin/env python3
"""마찰 인식 제동 시점 표(slip_env_table.h) 생성기 — 2차 방어 확장 'SLIP_ENVELOPE'.

입력: sw/docs/data/gain_sched/mu_table.json (2단계 CARLA 보정: friction 9종 × 일정 제동 0.3/0.5/0.7/1.0 의 평탄 직선 평균 감속)
출력: sw/fw/npu_lib/slip_env_table.h — 보드(안전 전용 RTOS)·파이 격리 코어가 그대로 포함하는 고정 크기 표
  · env_mu[]        실효 마찰 μ_eff (합가속/g 포화값)
  · env_dec_full[]  제동 1.0 평균 감속 (m/s²) — 정지 거리 계산에 쓴다
  · env_dec_probe[] 제동 0.3 평균 감속 (m/s²) — '노면 확인 탭'의 측정값을 μ 로 되돌릴 때 쓴다
감속은 엔진 브레이크를 포함한 실측이다. 표를 다시 만들려면 보정을 다시 돌리고 이 스크립트를 실행한다.
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[3]
src = ROOT / "sw/docs/data/gain_sched/mu_table.json"
t = json.loads(src.read_text())["table"]
rows = sorted(((v["mu_eff"], v["decel_by_brake"][3], v["decel_by_brake"][0], k) for k, v in t.items()), key=lambda r: r[0])
mu = [r[0] for r in rows]; full = [r[1] for r in rows]; probe = [r[2] for r in rows]
assert all(b > a for a, b in zip(mu, mu[1:])), "μ 가 단조 증가해야 보간할 수 있다"
assert all(b >= a for a, b in zip(full, full[1:])), "제동 1.0 감속이 μ 와 함께 늘어야 한다"
f = lambda xs: ", ".join(f"{x:.4f}f" for x in xs)
out = ROOT / "sw/fw/npu_lib/slip_env_table.h"
out.write_text(f"""/* 자동 생성: sw/fw/npu_lib/build_env_table.py — 손으로 고치지 말 것.
   근거: {src.relative_to(ROOT)} (CARLA 0.9.16 Tesla Model 3, 평탄 직선, friction {', '.join(r[3] for r in rows)}) */
#ifndef SLIP_ENV_TABLE_H
#define SLIP_ENV_TABLE_H
#define ENV_NMU {len(mu)}
static const float env_mu[ENV_NMU]        = {{ {f(mu)} }};
static const float env_dec_full[ENV_NMU]  = {{ {f(full)} }};   /* 제동 1.0, m/s² */
static const float env_dec_probe[ENV_NMU] = {{ {f(probe)} }};  /* 제동 0.3, m/s² */
#endif
""")
print(f"저장: {out.relative_to(ROOT)}  μ {len(mu)}칸 {mu[0]:.3f}~{mu[-1]:.3f}, 제동 1.0 감속 {full[0]:.2f}~{full[-1]:.2f} m/s²")
