# sw — 소프트웨어

```
sw/
  src/icepredict/   파이썬 패키지 (알고리즘 원본)
    common/         Pi ↔ N6 ↔ CARLA 메시지 규약
    pi/             기상 맥락, 위험도 융합, IMU 미끄러짐 감지
    sim/            CARLA 빙판 합성, 카메라 설정, 차로 추종
    train/          RoadNet 모델, 데이터 인덱스
  fw/npu_lib/       STM32N6 펌웨어 글루
  scripts/          수집 · 학습 · 양자화 · 배포 · 데모 · 분석  → scripts/README.md
  tests/            단위 테스트 19개
  pi/               ZMQ 연결 점검용 최소 예제
```

## 두 방어의 코드가 어디에 있나

| | 원본(파이썬) | 보드(C) |
|---|---|---|
| **1차 — 위험도 융합** | [`src/icepredict/pi/fusion.py`](src/icepredict/pi/fusion.py) | `fw/npu_lib/patch_app_netxduo.py` 가 ThreadX 융합 스레드에 주입 |
| **1차 — 반사도** | [`src/icepredict/pi/reflectance.py`](src/icepredict/pi/reflectance.py) | NPU 추론 출력의 두 번째 헤드 |
| **1차 — NPU 추론** | — | [`fw/npu_lib/npu_infer.c`](fw/npu_lib/npu_infer.c) |
| **2차 — 미끄러짐 감지** | [`src/icepredict/pi/imu_slip.py`](src/icepredict/pi/imu_slip.py) | [`fw/npu_lib/slip_core.h`](fw/npu_lib/slip_core.h) |
| **기상 맥락** | [`src/icepredict/pi/context.py`](src/icepredict/pi/context.py) | 호스트에서 생성해 보드로 보낸다 |

2차 방어의 C 코어는 **HAL·OS 비의존**이라 호스트에서도 그대로 컴파일된다. 그래서 파이썬 참조 구현과
같은 판정을 내리는지 보드 없이 검증할 수 있다.

```bash
python3 sw/fw/npu_lib/test_slip_core.py     # gcc 로 C 코어를 빌드해 동치 비교
```

## 재현

```bash
pip install -e .
python3 -m pytest -q                              # 단위 테스트 19개
python3 sw/scripts/analysis/summarize_runs.py     # 주행 집계표 재생성
```

CARLA 도 보드도 없이 저장소의 이벤트 데이터만으로 돈다.

## 스크립트

역할별로 다섯 묶음이다. 자주 쓰는 입구는 [`scripts/README.md`](scripts/README.md) 에 있다.

| 폴더 | 무엇이 들어 있나 |
|---|---|
| [`scripts/sim/`](scripts/sim) | CARLA 수집 · 데모 · 영상 정리 |
| [`scripts/model/`](scripts/model) | RoadNet 학습, int8 양자화, 반사도 헤드 |
| [`scripts/deploy/`](scripts/deploy) | STM32N6 적재 · 브리지 · 보드 평가 |
| [`scripts/analysis/`](scripts/analysis) | 집계 · 그림 · 근거 문서 생성 |
| [`scripts/archive/`](scripts/archive) | 일회성 배치 체인 (재현 이력 보존용) |
