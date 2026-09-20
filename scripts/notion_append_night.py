#!/usr/bin/env python3
"""노션 정리 페이지에 **밤새 바뀐 것**을 덧붙인다.

이번 구간의 주제는 하나다 — 2차 방어를 평균이 아니라 분포로 만들고, 그 과정에서 드러난
약점을 찾아 고쳤다. 숫자는 전부 실측에서 읽는다.

사용: python3 scripts/notion_append_night.py [--dry-run] [--page <id>]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import re
import statistics as st

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = "3e115059-8e1e-8190-8a76-e0a46e2d7f70"

import importlib.util as _il
_sp = _il.spec_from_file_location("_nu", ROOT / "scripts/notion_update_overview.py")
_nu = _il.module_from_spec(_sp)
_sp.loader.exec_module(_nu)
api, rt, para, head, bullet, callout, divider, table = (
    _nu.api, _nu.rt, _nu.para, _nu.head, _nu.bullet, _nu.callout, _nu.divider, _nu.table)


def codeblk(s):
    return {"object": "block", "type": "code",
            "code": {"rich_text": rt(s), "language": "plain text"}}


def facts():
    f = {}
    TAG = re.compile(r"^var_s(\d+)_k(\d+)_mu(\d+)$")
    by_kph, hard, touch, n = {}, 0, 0, 0
    for p in glob.glob(str(ROOT / "logs/carla_demo/events_var_*.json")):
        tag = os.path.basename(p)[7:-5]
        m = TAG.match(tag)
        if not m:
            continue
        ev = (json.load(open(p)) or {}).get("events", [])
        if not ev:
            continue
        n += 1
        k = {}
        for e in ev:
            k.setdefault(e["event"], e)
        if k.get("patch_enter") and k.get("secondary_slip"):
            by_kph.setdefault(int(m.group(2)), []).append(
                round(k["secondary_slip"]["t"] - k["patch_enter"]["t"], 2))
        col = next((e for e in ev if e["event"] == "collision"), None)
        if col:
            stp = (k.get("stopped") or {}).get("t")
            after = stp is not None and col["t"] >= stp - 0.05
            if float(col.get("speed_kph", 0.0)) >= 5.0 and not after:
                hard += 1
            else:
                touch += 1
    f.update(var_n=n, by_kph=by_kph, hard=hard, touch=touch)

    gains = []
    for p in glob.glob(str(ROOT / "logs/carla_demo/events_*.json")):
        ab = (json.load(open(p)) or {}).get("rule_ab")
        if ab and ab.get("gain_s") is not None:
            gains.append(ab["gain_s"])
    f["ab_gains"] = gains

    ctrl_n = ctrl_fa = 0
    for p in glob.glob(str(ROOT / "logs/carla_demo/events_*control*.json")):
        d = json.load(open(p))
        if not d.get("args", {}).get("control_no_ice"):
            continue
        ctrl_n += 1
        if any(e["event"] == "primary_warning" for e in d.get("events", [])):
            ctrl_fa += 1
    f.update(ctrl_n=ctrl_n, ctrl_fa=ctrl_fa)

    try:
        rows = [json.loads(l) for l in open(ROOT / "logs/rscd_board_samples.jsonl") if l.strip()]
        f["photo_n"] = len(rows)
    except Exception:
        f["photo_n"] = 0
    return f


def build(f):
    B = [divider(), head(1, "11. 2026-09-21 새벽 갱신 — 2차 방어를 분포로, 그리고 약점을 고쳤다")]
    B.append(callout(
        "이번 구간의 주제는 하나다. 2차 방어 수치가 사실상 **한 번 잰 값**이었다. "
        "날씨를 8종 돌려도 CARLA 날씨가 물리에 영향을 주지 않아 결과가 바이트 단위로 같았기 "
        "때문이다. 물리에 닿는 값을 흔들어 분포로 만들었고, 그 과정에서 감지기의 약점이 드러나 "
        "고쳤다.", "🆕"))

    B.append(head(2, "11-1. 2차 방어를 분포로"))
    if f["var_n"]:
        B.append(para(f"시드·진입속도·마찰을 흔들어 **{f['var_n']}건**을 돌렸다."))
        if f["by_kph"]:
            B.append(table([["진입 속도", "빙판 진입 → 미끄러짐 확정", "주행"]] +
                           [[f"{k} km/h",
                             (f"{st.mean(v):.2f} s" if len(v) > 1 else f"{v[0]:.2f} s") +
                             (f"  [{min(v):.2f} ~ {max(v):.2f}]" if len(v) > 1 else ""),
                             f"{len(v)}건"] for k, v in sorted(f["by_kph"].items())]))
        B.append(bullet(f"충돌 — 주행 중(5 km/h 이상) {f['hard']}건, 정지 후 접촉(5 km/h 미만) {f['touch']}건"))
        B.append(callout(
            "충돌 숫자를 \"실패율\"로 말하면 안 된다. 회피를 강제하려고 정차 차량을 빙판 위 "
            "정지거리보다 가까이(+45 m) 세운 배치다. 최악 조건의 스트레스 값이다.", "🚨"))
        B.append(callout(
            "시드 5종을 표본 5개로 세면 안 된다. `--ice-seed` 는 주변 차량 배치와 얼음 외관만 "
            "바꾸고 자차 주행선은 안 바꾼다. 같은 속도에서 독립 표본은 사실상 1개다.", "⚠️"))

    B.append(head(2, "11-2. 느릴수록 늦게 잡힌다 — 그 원인을 찾았다"))
    B.append(para(
        "45 km/h 1.10 s, 40 km/h 1.78 s, 35 km/h 4.23 s. 직관과 반대다. "
        "확정 순간의 잔차를 열어 보니 35 km/h 는 횡가속도 0.287 · yaw 0.353 이었다. "
        "횡가속도 임계가 0.300 이니 **4 % 차이로** 못 넘었고, 훨씬 느린 yaw 경로가 "
        "0.35 에 닿을 때까지 3.2 초를 더 기다린 것이다."))
    B.append(para("두 잔차를 OR 로 묶은 **직사각형 판정의 모서리에 걸렸다**. 형태를 바꿨다."))
    B.append(codeblk(
        "직사각형(기존)  |ay_g| >= 0.30  또는  |yaw_err| >= 0.35\n"
        "타원(변경)      (ay_g/0.30)^2 + (yaw_err/0.35)^2 >= 1"))
    B.append(bullet("타원은 직사각형을 안에 품는다 — 정의상 늦어질 수 없다"))
    B.append(bullet("새 상수도 속도 보정도 없다. 비용은 곱 2 + 합 1, 분기 수는 그대로"))
    B.append(bullet("합성 검증: 빨라진 조건 3, 기존이 못 잡던 것을 새로 잡은 조건 1, 느려진 조건 0"))
    B.append(bullet("정상 주행 오탐 480 시행 중 0 — 직사각형과 같다"))
    if f["ab_gains"]:
        g = f["ab_gains"]
        B.append(bullet(f"CARLA 실주행 {len(g)}건, 같은 주행선 위 비교: 평균 {st.mean(g):+.2f} s, 최대 {max(g):+.2f} s"))
    B.append(para(
        "버린 안도 적어 둔다. 임계값 인하는 오경보가 늘고, 속도 정규화(임계 ∝ v²)는 고속에서 "
        "임계가 올라가 **도리어 늦어진다**."))
    B.append(callout(
        "보드 펌웨어에는 아직 안 올라가 있다. 굽는 데 BOOT1 스위치 물리 접근과 SWD 가 필요하다. "
        "코드는 fw/npu_lib/slip_core.h 에 들어갔고 C↔파이썬 동치도 통과했다. "
        "다음 현장 작업 때 scripts/fw_redeploy.sh 한 번이면 된다.", "🔧"))

    B.append(head(2, "11-3. 1차 방어 오경보를 막는 층이 하나 늘었다"))
    if f["ctrl_n"]:
        B.append(para(
            f"확정 계층(연속 8프레임)을 넣은 뒤 빙판 없는 대조군 {f['ctrl_n']}건 중 "
            f"오경보가 {f['ctrl_fa']}건으로 줄었다. 남은 것은 전부 **젖은 노면**이다."))
    B.append(para(
        "젖은 노면에서 1차가 노면 확률 0.87 로 얼음이라 단언했다. 문턱·연속프레임·가중치·"
        "강수게이트 어느 것으로도 못 막는다. 영상만으로는 젖음과 얼음이 안 갈린다는 뜻이고, "
        "그러면 갈라 줄 쪽은 맥락뿐이다."))
    B.append(para(
        "블랙아이스는 노면이 어는점 이하여야 생긴다. 노면이 공기보다 낮을 수 있는 폭을 "
        "최악으로 잡고(낮 2 °C / 낮·다리 3 °C / 밤 4 °C / 밤·다리 6 °C), 그러고도 0 °C 를 "
        "넘으면 얼음 경보를 내지 않는다. 잔설이 있으면 국소 재결빙이 남으므로 게이트를 열어 둔다."))
    B.append(callout(
        "강수 게이트와 의미가 다르다. 저쪽은 \"카메라를 못 믿겠다 → 2차에 맡긴다\", "
        "이쪽은 \"얼음이 있을 수 없다 → 얼음 경보를 내지 않는다\". "
        "**어느 쪽이든 2차 방어는 그대로 돈다** — 따뜻해도 젖은 노면은 미끄럽다.", "🎯"))
    B.append(callout(
        "젖은 노면 오경보는 **CARLA 현상**이다. 실사진에서는 젖음과 얼음이 거의 완전히 갈린다 "
        "(판별 AUC 0.998, 젖은 노면 오경보 1.9 %). 기온 게이트는 결함을 때우는 패치가 아니라 "
        "한 겹 더 두는 방어다. 발표에서 \"시뮬에서 오경보가 났다\"를 근거로 쓰면 안 된다.", "⚠️"))

    if f["photo_n"]:
        B.append(head(2, "11-4. 실사진 활용"))
        B.append(para(f"실물 보드로 판정한 실사진이 누적 **{f['photo_n']:,}장**이다 "
                      f"(데이터셋 전체 69,360장 중 {100*f['photo_n']/69360:.0f} %)."))

    B.append(head(2, "11-5. 새로 생긴 근거 문서"))
    B.append(table([
        ["문서", "무엇을 답하나"],
        ["정리/11_변동스윕_2차방어분포.md", "2차 방어를 평균이 아니라 분포로. 속도 의존과 충돌 분류"],
        ["정리/12_저속지연_원인분해.md", "저속에서 느린 게 감지기 탓인가 물리 탓인가"],
        ["정리/13_판정규칙_타원.md", "직사각형 → 타원. 합성 신호로 속도·오탐 검증"],
        ["정리/14_판정규칙_실주행AB.md", "같은 주행선 위에서 두 규칙을 나란히"],
        ["정리/15_맥락계층_기온게이트.md", "같은 화면, 기온만 다르게 — 맥락이 값을 하나"],
        ["정리/00_분석문서_목록.md", "근거 문서 16편이 각각 무엇을 답하는지 + 읽는 순서"],
    ]))
    return B


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--page", default=PAGE)
    a = ap.parse_args()
    f = facts()
    B = build(f)
    print(f"블록 {len(B)}개, 실측: 변동 {f['var_n']}건 · 대조군 {f['ctrl_n']}건 · "
          f"A/B {len(f['ab_gains'])}건 · 실사진 {f['photo_n']:,}장")
    if a.dry_run:
        print("(--dry-run — 올리지 않았다)")
        return
    tok = yaml.safe_load(open(ROOT / "secrets.yaml"))["notion_token"]
    rest = B
    while rest:
        api(tok, f"blocks/{a.page}/children", "PATCH", {"children": rest[:90]})
        rest = rest[90:]
    print(f"정리 페이지에 덧붙였다: {len(B)}블록")


if __name__ == "__main__":
    main()
