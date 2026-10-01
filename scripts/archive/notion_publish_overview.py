#!/usr/bin/env python3
"""팀 공유용 정리 페이지를 노션 경진대회 페이지 아래에 만든다.

무엇을 담나: 연구를 어떤 상황(시나리오)으로 나눴는지, 산출물 폴더가 어떻게 구성돼 있는지,
지금 어디까지 왔고 무엇이 확정됐고 무엇이 남았는지.

노션 API 는 하위 페이지를 부모 맨 뒤에 붙인다(앞에 넣는 API 가 없다). 그래서 페이지를 만든 뒤
부모 첫 블록 **바로 뒤**에 바로가기 콜아웃을 끼워 첫 화면에서 보이게 한다.

사용: python3 scripts/archive/notion_publish_overview.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import urllib.error
import urllib.request

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
PARENT = "35415059-8e1e-8071-98cc-fc2157b37da3"
TITLE = "📁 연구 정리 — 폴더 구성과 진행 현황"


def api(tok, path, method="GET", body=None):
    req = urllib.request.Request(
        f"https://api.notion.com/v1/{path}", method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {tok}", "Notion-Version": "2022-06-28",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"노션 API 실패 {e.code}: {e.read().decode()[:400]}")


# ---------- 블록 만들기 ----------
def rt(s: str, bold=False, code=False):
    return [{"type": "text", "text": {"content": s[:1900]},
             "annotations": {"bold": bold, "code": code}}]


def para(s=""):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rt(s) if s else []}}


def head(level, s):
    k = f"heading_{level}"
    return {"object": "block", "type": k, k: {"rich_text": rt(s)}}


def bullet(s):
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rt(s)}}


def todo(s, done=False):
    return {"object": "block", "type": "to_do", "to_do": {"rich_text": rt(s), "checked": done}}


def callout(s, emoji="💡"):
    return {"object": "block", "type": "callout",
            "callout": {"rich_text": rt(s), "icon": {"type": "emoji", "emoji": emoji}}}


def code(s, lang="plain text"):
    return {"object": "block", "type": "code",
            "code": {"rich_text": rt(s), "language": lang}}


def divider():
    return {"object": "block", "type": "divider", "divider": {}}


def table(rows: list[list[str]]):
    w = max(len(r) for r in rows)
    kids = []
    for r in rows:
        cells = [rt(c) for c in r] + [rt("")] * (w - len(r))
        kids.append({"object": "block", "type": "table_row", "table_row": {"cells": cells}})
    return {"object": "block", "type": "table",
            "table": {"table_width": w, "has_column_header": True,
                      "has_row_header": False, "children": kids}}


# ---------- 내용 ----------
def build() -> list[dict]:
    B: list[dict] = []

    B.append(callout(
        "IcePredict 연구·산출물 지도. 어떤 상황으로 나눠 실험했는지, 결과물이 어느 폴더에 "
        "어떻게 정리돼 있는지, 지금 무엇이 확정됐고 무엇이 남았는지를 한 곳에 모았다. "
        "기준 시각 2026-09-20 20:20. 원본은 팀 저장소 /mnt/ssd/icepredict 에 있다.", "🧭"))
    B.append(para())

    # ---- 1. 한눈에 ----
    B.append(head(1, "1. 한눈에 보는 진행 현황"))
    B.append(table([
        ["영역", "상태", "핵심 숫자"],
        ["보드 2차 방어 (IMU 미끄러짐 감지)", "✅ 검증 완료",
         "주행 25건·샘플 14,162개, 평균 11.8 µs, 관측 WCET 17.9 µs, 호스트 폴백 0건"],
        ["2차 방어 오탐률", "✅ 최초 실측", "빙판 없는 대조군 10건에서 0건 (0 %)"],
        ["비상 제어 — 회피 동작", "✅ 재현 성공 (9/20)", "앞차 간격 50 m 미만이면 evade_left, 70 m 면 차선유지·정지"],
        ["1차 방어 지각 성능 (실사진)", "✅ 측정 완료", "문턱 0.60 에서 얼음 97.7 %, 젖은노면 오경보 1.0 %"],
        ["1차 방어 오경보율 (시뮬)", "⚠️ 측정 완료 · 조치 필요", "대조군 10건 중 4건 = 40 %. 문턱 0.55↑ 면 12 %"],
        ["1차 방어 경보 거리", "⚠️ 한계 확인", "중앙값 11.7 m, 시간여유 1.00 s. 카메라가 7.9~42.2 m 만 본다"],
        ["폭우에서의 1차 방어", "❌ 미해결", "빙판 없는 도로에서 최대 위험도 0.963. 문턱·가중치로 못 막는다"],
        ["도메인 갭 (CARLA ↔ 실사진)", "🔄 원인 재정의", "질감·밝기·렌더품질 모두 기각. 진짜 제약은 카메라 기하"],
        ["Hailo 교차 검증", "⛔ 막힘", "컴파일러(DFC) 미확보 — 보드 수치의 독립 검증 수단 없음"],
        ["실물 IMU (D435i)", "⛔ 막힘", "미연결. 칼만 R 값이 여전히 시뮬 기준"],
    ]))
    B.append(para())

    # ---- 2. 시나리오 ----
    B.append(head(1, "2. 연구를 어떤 상황으로 나눴나"))
    B.append(para(
        "같은 빙판·같은 경로에서 '무엇을 켜고 무엇을 껐는지'만 바꿔 가며 돌린다. "
        "그래야 결과 차이가 방어 체계 때문이라고 말할 수 있다. 영상 파일명은 "
        "demo_{날씨}_{시나리오}_{시점}.mp4 형식이다."))
    B.append(table([
        ["시나리오", "무엇을 켜고 껐나", "주행 수", "무엇을 보여주나"],
        ["detect", "1차 켬 (보드 NPU 인지)", "11", "카메라가 빙판을 미리 보고 정지하는 정상 동작"],
        ["detect_traffic", "1차 켬 + 주변차량 8대", "3", "교통이 있어도 같은 동작"],
        ["detect_60kph", "1차 켬, 60 km/h", "3", "속도가 오르면 경고가 늦어 진입한다"],
        ["miss", "1차 끔, 2차를 호스트가 판정", "6", "초기 참조 구현 (지금은 보드 판정을 쓴다)"],
        ["miss_rtos", "1차 끔, 2차를 보드 ThreadX 가 판정", "6", "카메라가 놓쳤을 때 보드가 받는다"],
        ["miss_rtos_traffic", "위 + 주변차량 8대, 빙판 100 m, µ 0.08", "8", "간판 시나리오. 미끄러짐 → 비상 제어"],
        ["nodefense / _traffic", "1차·2차 모두 끔", "7", "기준선. 방어가 없으면 어떻게 되는지"],
        ["control ⭐", "빙판을 아예 안 만듦", "8", "여기 나오는 경보는 전부 오경보 → 오경보율 측정"],
        ["control_traffic ⭐", "빙판 없음 + 주변차량 8대", "2", "정상 교통 상황에서의 2차 오탐률"],
        ["evade_lead30 / 45 ⭐", "정차 차량을 앞으로 당김", "2", "회피(evade_left) 동작이 나오는 조건"],
        ["rscdtex_detect", "노면에 RSCD 실사진 질감", "2", "1차를 켜고도 못 본다 → 도메인 갭 증거"],
    ]))
    B.append(callout(
        "⭐ 표시는 2026-09-20 에 새로 만든 시나리오다. 특히 control 계열이 중요하다 — "
        "지금까지 모든 주행에 빙판이 있어서 '헛보는 비율'을 잴 방법이 아예 없었다.", "⭐"))
    B.append(para())

    # ---- 3. 폴더 ----
    B.append(head(1, "3. 산출물 폴더 구성"))
    B.append(para("저장소 루트는 /mnt/ssd/icepredict 이고, 데모 결과는 전부 logs/carla_demo 아래에 모인다."))
    B.append(code(
        "logs/carla_demo/\n"
        "├── demo_{날씨}_{시나리오}_{시점}.mp4   영상 202개 (원본, 지우지 않는다)\n"
        "├── events_{태그}.json                 주행별 사건 기록 (진입·미끄러짐·정지·충돌)\n"
        "├── trace_{태그}.json                  프레임별 속도·거리·위험도·클래스 확률\n"
        "├── photos/                            대표 장면 사진 714장\n"
        "├── figures/                           발표용 그림 16장\n"
        "└── 정리/                              ← 팀이 볼 곳. 상황별로 묶어 둔 보기용 트리\n"
        "    ├── 00_읽어보기.txt                폴더 안내\n"
        "    ├── 00_목록.txt                    주행 60건 한 줄 요약\n"
        "    ├── 00_집계.md ~ 04_질감의존.md    근거 문서 5편\n"
        "    └── A_ ~ H_                        상황별 그룹 폴더"))
    B.append(para())
    B.append(head(2, "정리/ 아래 그룹 폴더"))
    B.append(table([
        ["폴더", "무엇이 들어 있나", "주행", "영상"],
        ["A_1차방어_카메라인식_정지", "카메라가 빙판을 미리 보고 정지한 주행", "18", "68"],
        ["B_2차방어_카메라미인식_IMU개입", "1차를 끄고 2차가 받은 주행", "47", "169"],
        ["C_2차방어_카메라경고늦음_IMU개입", "봤지만 늦어 진입 → 2차 개입", "10", "31"],
        ["D_카메라오경보", "빙판을 볼 수 없는 거리에서 경고 — 1차의 한계", "9", "27"],
        ["E_기타", "분류가 애매한 주행", "6", "9"],
        ["F_방어없음_기준선", "1차·2차 모두 없을 때", "13", "52"],
        ["G_비교_방어없음_vs_RTOS", "좌우 나란히 붙인 비교 영상", "—", "8"],
        ["H_1차켜짐_그래도못봄_도메인갭 ⭐", "1차를 켜고 돌렸는데 실제로 못 본 주행", "2", "8"],
    ]))
    B.append(callout(
        "H 그룹은 2026-09-20 에 새로 나눴다. 그 전에는 '1차를 일부러 끈 주행(B)'과 "
        "'1차를 켜고도 못 본 주행'이 같은 이름을 받아 섞여 있었다. "
        "도메인 갭이라는 가장 중요한 음성 결과가 정리본에서 사라지고 있었다.", "⚠️"))
    B.append(callout(
        "정리/ 폴더는 원본에 하드링크를 건다. 같은 태그로 다시 촬영하면 원본은 덮이지만 "
        "옛 촬영본은 정리본 덕분에 살아남는다. 확인해 보니 정리본 안에만 남아 있는 영상이 "
        "98개다. 폴더 이름이 중복돼 보여도 절대 지우지 말 것.", "🚨"))
    B.append(para())

    # ---- 4. 근거 문서 ----
    B.append(head(1, "4. 근거 문서 5편 (정리/ 아래)"))
    B.append(table([
        ["파일", "무엇을 답하나", "핵심 결론"],
        ["00_집계.md", "주행별 결과를 한 표로", "충돌 3건·스핀 5건·차로이탈 9건 (전에는 충돌 열이 아예 없었다)"],
        ["01_탐지성능.md", "얼마나 멀리서 보고 얼마나 헛보나", "경보거리 중앙값 11.7 m / 대조군 오경보 40 % / 2차 오탐 0 %"],
        ["02_융합가중치.md", "β 와 문턱을 얼마로 할까", "문턱 0.441 → 0.60. 젖은노면 오경보 40 % → 1 %"],
        ["03_도메인갭.md", "CARLA 화면과 실사진이 얼마나 다른가", "국소 대비 8배 차이. 다만 모델은 그 차이에 강인하다"],
        ["04_질감의존.md", "그 차이가 원인인가", "아니다. 흐리게 해도 밝게 해도 모델 출력이 거의 안 변한다"],
    ]))
    B.append(para())

    # ---- 5. 확정된 숫자 ----
    B.append(head(1, "5. 서류·발표에서 그대로 쓸 수 있는 숫자"))
    B.append(para("숫자마다 근거 파일이 있다. 여기 없는 숫자는 아직 근거가 없다는 뜻이다."))
    B.append(table([
        ["주장", "수치", "근거"],
        ["보드 2차 판정 지연", "주행 25건·샘플 14,162개, 평균 11.8 µs, 관측 WCET 17.9 µs, 폴백 0", "logs/board_wcet.jsonl"],
        ["보드 NPU 추론 비용", "25.5 ms 고정, 24초 주행 1,200 프레임 무응답 0건", "대조군 주행 로그"],
        ["1차 지각 성능 (실사진)", "문턱 0.60 에서 얼음 97.7 %, 젖음 1.0 %, 마름 0.7 %", "02_융합가중치.md"],
        ["1차 경보 거리", "중앙값 11.7 m, 시간여유 1.00 s", "01_탐지성능.md"],
        ["그 거리의 이유", "ROI 가 보는 노면이 7.9~42.2 m, 30~60 m 는 15.2 px", "camera.py 기하 계산"],
        ["2차 방어 동작", "진입 → 1.78 s 미끄러짐 확정 → 비상 제어 → 정지. 8/8 개입, 충돌·스핀·이탈 0", "00_집계.md"],
        ["2차 방어의 상황 판단", "앞차 69.6 m → 차선유지·정지 / 49.6 m·36.9 m → 회피", "evade_lead30/45"],
        ["2차 방어 오탐", "빙판 없는 대조군 10건에서 0건 (0 %)", "control 주행"],
        ["1차 오경보 (시뮬)", "대조군 10건 중 4건 = 40 %. 문턱 0.55↑ 면 12 %", "control 주행"],
        ["방어가 없을 때", "차로이탈 12.02 s → 스핀 14.72 s → (주변차량 있으면) 충돌", "nodefense_traffic 4건"],
    ]))
    B.append(para())

    B.append(head(2, "같이 말해야 하는 한계 — 먼저 말하는 쪽이 낫다"))
    B.append(table([
        ["한계", "왜"],
        ["\"시뮬에서 몇 % 인식\"이라고 말하면 안 된다",
         "시뮬 정상 탐지 risk 중앙값 0.515 < 오경보 0.560. 두 분포가 겹친다. 지각 성능 근거는 실사진 쪽을 써야 한다"],
        ["날씨별 2차 방어 결과는 사실상 표본 1개다",
         "CARLA 날씨는 물리에 영향을 주지 않아 8개 날씨 결과가 바이트 단위로 같다. 배경 다양성으로는 가치가 있다"],
        ["기준선의 '충돌'은 재현 보장이 아니다",
         "제어 상실(이탈 12.02 s·스핀 14.72 s)은 매번 같지만 충돌 여부는 주변 차량 위치에 달렸다"],
        ["폭우에서 1차 방어는 신뢰할 수 없다",
         "빙판이 없는 대조군에서도 최대 위험도 0.963. 문턱·연속프레임·재가중 모두 못 막는다"],
        ["실물 IMU 노이즈는 측정된 적이 없다",
         "칼만 R 값이 CARLA IMU 기준. 2차 오탐률 주장 전체가 이 가정 위에 있다"],
    ]))
    B.append(para())

    # ---- 6. 이번에 밝혀낸 것 ----
    B.append(head(1, "6. 2026-09-20 에 밝혀낸 것"))
    B.append(head(3, "① 회피 시나리오가 네 번 실패한 진짜 이유"))
    B.append(para(
        "이전 세션들은 로그의 \"정차 차량이 428 m 밖에 놓였다\"를 보고 배치 버그를 고치려 했다. "
        "그런데 그 428 m 는 스폰 직후에 위치를 읽어서 생긴 착시였고, 차는 처음부터 제자리에 있었다. "
        "진짜 원인은 앞차 간격 계산이 나들목에서 램프 쪽을 골라 같은 차로 앞차를 통째로 탈락시킨 것이었다. "
        "차로 중심선을 점열로 만들어 선분 거리로 판정하게 바꾸자 간격이 999 → 69.6 m 로 바뀌었다."))
    B.append(table([
        ["정차 차량 위치", "미끄러짐 시점 앞차 간격", "보드 판단"],
        ["빙판 +70 m", "69.6 m", "차선 유지 → 정지"],
        ["빙판 +45 m", "49.6 → 43.5 m", "급제동 → 회피(evade_left) → 정지"],
        ["빙판 +30 m", "36.9 → 30.6 m", "급제동 → 회피(evade_left) → 정지"],
    ]))
    B.append(para(
        "전부 충돌 0·스핀 0. 전환점이 50~70 m 사이이고 µ 0.08 에서 38 km/h 제동거리가 약 71 m 이므로, "
        "제어기가 임의로 고르는 게 아니라 제동 가능 여부를 계산해 판단한다는 증거다. 이 표 자체가 논거가 된다."))

    B.append(head(3, "② 재수집·재학습 계획의 전제가 무너졌다"))
    B.append(para(
        "\"CARLA 화면이 실사진과 달라서 모델이 못 본다\"는 가설을 세 가지 실험으로 시험했고 모두 기각됐다."))
    B.append(table([
        ["시험", "결과"],
        ["실사진을 CARLA 수준으로 흐리게 (국소대비 52.7 → 8.5)", "얼음 확률 0.898 → 0.881"],
        ["실사진을 CARLA 수준으로 밝게 (L* +36)", "얼음 확률 0.902 → 0.885"],
        ["렌더 품질 Low → Epic", "경보 거리 10.3 m → 9.7 m"],
    ]))
    B.append(para(
        "모델은 질감·밝기 양쪽에 충분히 강인하다. 진짜 제약은 카메라 기하다 — ROI 가 전방 7.9~42.2 m 만 보고, "
        "30~60 m 구간은 15.2 px 에 눌려 있다. 확률 곡선도 이와 맞는다(43 m 에서 0.006, 31.6 m 0.219, 27.4 m 0.943). "
        "외관을 못 알아보는 게 아니라 픽셀이 없다가 생기는 것이다. 따라서 CARLA 재수집·센서노이즈 추가는 근거가 없다."))

    B.append(head(3, "③ 오경보율을 처음으로 실측했다"))
    B.append(para(
        "빙판을 아예 만들지 않는 대조군(--control-no-ice)을 새로 만들었다. 마찰·합성·타일을 전부 정상 노면과 "
        "같게 두므로 여기서 나오는 경보는 정의상 전부 오경보다. 8개 날씨 + 주변차량 2종을 돌렸다."))
    B.append(table([
        ["문턱", "오경보 주행", "비율"],
        ["0.441 (현행)", "4 / 10", "40 %"],
        ["0.55 이상", "1 / 10", "12 %"],
    ]))
    B.append(para(
        "0.55 에서 계단이 한 번 떨어지고 그 아래로는 안 내려간다. 바닥에 남는 폭우 주행은 최대 위험도 0.963 이다. "
        "실사진 900장 스윕·기존 주행 궤적 분석·이 대조군, 세 가지 독립된 방법이 같은 답을 냈다."))
    B.append(para())

    # ---- 7. 남은 일 ----
    B.append(head(1, "7. 남은 일"))
    B.append(todo("융합 문턱을 0.441 → 0.55~0.60 으로 올린다 (ctx 메시지로 내려가므로 펌웨어 재빌드 불필요)"))
    B.append(todo("폭우에서 1차를 '신뢰 불가'로 선언하고 2차에 넘기는 로직 (perception_degraded 와 같은 결)"))
    B.append(todo("데모가 CARLA 날씨를 실제로 컨텍스트에 반영하게 하기 — 지금은 강수량 0 으로 고정해 넘긴다"))
    B.append(todo("망원 시야(25°) 검토 — 원거리 해상도 2.5배. 경보 거리를 늘리려면 이 길뿐"))
    B.append(todo("통계적 변동 만들기 — 진입속도·진입각·µ·시드를 흔들어야 표본 1을 벗어난다"))
    B.append(todo("Hailo 컴파일러(DFC) 확보 — 보드 수치 독립 교차 검증"))
    B.append(todo("RealSense D435i 연결 → 실물 IMU 노이즈 측정 → 칼만 R 갱신"))
    B.append(para())

    # ---- 8. 도구 ----
    B.append(head(1, "8. 분석·검증 도구 (scripts/)"))
    B.append(table([
        ["파일", "하는 일"],
        ["analyze_detection.py", "주행별 경보 거리·시간여유·오경보 분류·대조군 집계 → 01_탐지성능.md"],
        ["tune_fusion.py", "RSCD 실사진으로 β·문턱 스윕 → 02_융합가중치.md"],
        ["domain_gap_stats.py", "CARLA vs 실사진 ROI 영상 통계 → 03_도메인갭.md"],
        ["texture_sensitivity.py", "모델이 질감에 얼마나 기대는지 시험 → 04_질감의존.md"],
        ["summarize_runs.py", "주행 집계 (충돌·스핀·이탈 포함) → 00_집계.md"],
        ["organize_media.py", "상황별 폴더로 하드링크 배치 → 정리/"],
        ["make_figures.py / extract_photos.py / compare_videos.py", "발표용 그림·사진·좌우 비교 영상"],
        ["merge_wcet.py", "보드 지연 기록을 데스크탑에서 가져와 합친다 (배치 뒤 반드시 실행)"],
        ["test_follow_lane.py", "차로 추종·선분 거리 단위 시험 (CARLA 없이 돌아간다)"],
        ["stop_chains.sh", "돌고 있는 배치 체인을 안전하게 멈춘다 (--list 로 먼저 확인)"],
    ]))
    B.append(para())

    B.append(head(1, "9. 실행 환경"))
    B.append(table([
        ["역할", "기계", "비고"],
        ["오케스트레이션·보드 브리지", "Raspberry Pi 5 (sdv-pi)", "보드와 유선 연결 192.168.50.158"],
        ["CARLA 서버·데모 실행", "RTX 5090 (dlab27)", "2026-09-20 에 3090 에서 이전. 렌더 품질 Epic"],
        ["1차·2차 방어 실행", "STM32N6570-DK", "NPU + ThreadX. 한 장이 둘 다 담당"],
    ]))
    B.append(callout(
        "긴 배치는 setsid 로 띄워야 한다. 도구 백그라운드로만 띄우면 프로세스 트리가 회수돼 죽는다. "
        "그리고 작업 세션을 둘 이상 동시에 돌리지 말 것 — 같은 CARLA·같은 보드·같은 출력 폴더를 "
        "두 체인이 쓰면 서버가 죽고 영상이 덮인다.", "⚙️"))

    return B


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    tok = yaml.safe_load(open(ROOT / "secrets.yaml"))["notion_token"]
    blocks = build()
    print(f"블록 {len(blocks)}개 준비")
    if a.dry_run:
        print("(--dry-run — 만들지 않았다)")
        return

    page = api(tok, "pages", "POST", {
        "parent": {"page_id": PARENT},
        "icon": {"type": "emoji", "emoji": "📁"},
        "properties": {"title": {"title": rt(TITLE)}},
        "children": blocks[:95],
    })
    pid, url = page["id"], page["url"]
    print("페이지 생성:", url)

    rest = blocks[95:]
    while rest:
        api(tok, f"blocks/{pid}/children", "PATCH", {"children": rest[:95]})
        rest = rest[95:]
    print("본문 전송 완료")

    # 부모 첫 블록 바로 뒤에 바로가기를 끼운다 (노션 API 에 '맨 앞' 이 없다)
    ch = api(tok, f"blocks/{PARENT}/children?page_size=3")
    first_id = ch["results"][0]["id"]
    api(tok, f"blocks/{PARENT}/children", "PATCH", {
        "after": first_id,
        "children": [
            {"object": "block", "type": "callout",
             "callout": {"icon": {"type": "emoji", "emoji": "📁"},
                         "rich_text": [
                             {"type": "text", "text": {"content": "연구 정리 — 폴더 구성과 진행 현황 "}},
                             {"type": "text",
                              "text": {"content": "(여기를 열어 보세요)", "link": {"url": url}},
                              "annotations": {"bold": True}},
                         ]}},
        ]})
    print("부모 페이지 상단에 바로가기 추가")
    print("\n주소:", url)


if __name__ == "__main__":
    main()
