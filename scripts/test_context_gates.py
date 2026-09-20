#!/usr/bin/env python3
"""맥락 계층의 두 게이트가 의도대로 동작하는지 — CARLA 없이 도는 단위 시험.

게이트는 둘이고 의미가 다르다.
  강수 게이트 primary_trust   "카메라를 못 믿겠다" → 1차를 끄고 2차에 맡긴다
  기온 게이트 ice_possible    "얼음이 있을 수 없다" → 얼음 경보를 내지 않는다
어느 쪽이든 2차 방어는 그대로 돈다.

여기서 지키려는 것
- 겨울 조건에서 게이트가 **열려 있어야** 한다 (막으면 안 되는 걸 막으면 안 된다)
- 따뜻한 날 젖은 노면에서 기온 게이트가 닫혀야 한다
- 밤·다리는 같은 기온이라도 더 오래 열려 있어야 한다 (복사냉각)
- 눈이 쌓여 있으면 기온이 높아도 열려 있어야 한다 (국소 결빙)

사용: python3 scripts/test_context_gates.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from icepredict.pi.context import (LocationCtx, WeatherObs, build_context,  # noqa: E402
                                   ice_possible, primary_trust, road_temp_worst_c)

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"[{'OK ' if ok else 'FAIL'}] {name}: {got}" + ("" if ok else f"  (기대 {want})"))
    if not ok:
        FAIL.append(name)


def main():
    W = lambda **k: WeatherObs(**{"temp_c": -3.0, "humidity": 88.0, **k})

    print("=== 기온 게이트: 얼음이 있을 수 있나 ===")
    for temp, hour, feat, want in [
        (-3.0, 13, "bridge", True),    # 한겨울 — 당연히 가능
        (0.0, 13, "bridge", True),
        (3.0, 13, "bridge", True),     # 낮·다리 여유 3도 → 아직 가능
        (4.0, 13, "bridge", False),    # 여유를 빼도 어는점 위
        (3.0, 13, "none", False),      # 트인 곳은 여유 2도 → 불가능
        (5.0, 3, "bridge", True),      # 밤·다리 여유 6도 → 아직 가능
        (7.0, 3, "bridge", False),
        (5.0, 3, "none", False),       # 밤·트인 곳 여유 4도 → 최악 노면 +1도, 불가능
        (4.0, 3, "none", True),        # 여유를 빼면 0도 — 아직 가능하다고 본다
        (20.0, 13, "none", False),     # 한여름
    ]:
        p, _ = ice_possible(W(temp_c=temp), LocationCtx(feature=feat, hour=hour))
        check(f"기온 {temp:+.0f}도 {hour:2d}시 {feat}", p, want)

    print("\n눈이 쌓여 있으면 기온이 높아도 열려 있어야 한다 (국소 재결빙)")
    p, _ = ice_possible(W(temp_c=6.0, snow_on_ground=True), LocationCtx(feature="none", hour=13))
    check("기온 +6도 · 잔설 있음", p, True)

    print("\n=== 강수 게이트: 카메라를 믿을 수 있나 ===")
    for precip, want in [(0.0, True), (0.6, True), (4.9, True), (5.0, False), (21.8, False)]:
        t, _ = primary_trust(W(precip_mm=precip))
        check(f"강수 {precip:.1f} mm/h", t, want)

    print("\n=== 두 게이트는 서로 독립이어야 한다 ===")
    # 따뜻한 폭우: 얼음은 불가능하고, 카메라도 못 믿는다 — 둘 다 닫힌다
    c = build_context(W(temp_c=12.0, precip_mm=20.0), LocationCtx(feature="none", hour=13))
    check("따뜻한 폭우 — 얼음 가능", c.ice_possible, False)
    check("따뜻한 폭우 — 1차 신뢰", c.primary_trustworthy, False)
    # 추운 폭우: 얼음은 가능하지만 카메라는 못 믿는다
    c = build_context(W(temp_c=-2.0, precip_mm=20.0), LocationCtx(feature="none", hour=13))
    check("추운 폭우 — 얼음 가능", c.ice_possible, True)
    check("추운 폭우 — 1차 신뢰", c.primary_trustworthy, False)
    # 추운 맑음: 둘 다 열려 있다 — 1차가 정상 동작해야 하는 기본 조건
    c = build_context(W(temp_c=-3.0, precip_mm=0.0), LocationCtx(feature="bridge", hour=5))
    check("추운 맑음 — 얼음 가능", c.ice_possible, True)
    check("추운 맑음 — 1차 신뢰", c.primary_trustworthy, True)

    print("\n=== 최악 노면 온도 추정 ===")
    for hour, feat, want_gap in ((13, "none", 2.0), (13, "bridge", 3.0), (3, "none", 4.0), (3, "bridge", 6.0)):
        gap = 0.0 - road_temp_worst_c(W(temp_c=0.0), LocationCtx(feature=feat, hour=hour))
        check(f"{hour:2d}시 {feat:>7s} 여유", round(gap, 1), want_gap)

    print()
    if FAIL:
        print(f"!! 실패 {len(FAIL)}건: {', '.join(FAIL)}")
        return 1
    print("전부 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
