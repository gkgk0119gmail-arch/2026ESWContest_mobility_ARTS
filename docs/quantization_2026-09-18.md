# int8 양자화 조사 (2026-09-18)

STM32N6 NPU 배포를 막는 두 문제를 분리해 조사했다. **하나는 해결, 하나는 미해결**이다.

## 해결: NPU 매핑

`--st-neural-art`에 **프로파일을 지정해야 한다.** 지정하지 않으면 atonn이 기본값으로 돌아
거의 전부 CPU 폴백으로 보인다.

```
# 잘못된 호출 (프로파일 없음)
stedgeai analyze --model m.onnx --target stm32n6 --st-neural-art
  → 211 epoch 중 HW 1 / SW 210

# 올바른 호출
stedgeai analyze --model m.onnx --target stm32n6 \
  --st-neural-art "n6-allmems-O3@<STEDGEAI>/scripts/N6_scripts/user_neuralart.json"
  → 99 epoch 중 HW 90 / SW 9
```

프로파일은 `scripts/N6_scripts/user_neuralart.json`에 정의돼 있다 (n6-extram, n6-extflash,
n6-noextmem, n6-nointmem, n6-allmems-O1/O2/O3/Oauto). 각각 메모리 풀(.mpool)과 atonn
옵션(`--optimization 3 --Oauto-sched --Ocache-opt` 등)을 지정한다.

**대조 실험**: ST 자체 샘플 `scripts/N6_scripts/models/mnist_int8_io_i8.tflite`는 5 epoch 중
HW 4 / SW 1로 매핑된다 → 툴체인은 정상이다.

이 오류 때문에 앞서 "FP32는 원래 NPU 가속이 안 된다"고 적은 진단(docs/n6_npu_analysis)도
근거가 무효다. FP32도 프로파일을 주고 다시 재봐야 한다.

## 미해결: int8 정확도 붕괴

RoadNet v2(FP32 RSCD acc 0.905)를 전 레이어 int8로 양자화하면 **0.29~0.48로 붕괴**한다.
오늘 시도한 조합 전부에서 같다.

| 방법 | 활성 | 대칭 | 보정 | 양자화 범위 | RSCD acc |
|---|---|---|---|---|---|
| (기준) FP32 v2 | — | — | — | — | **0.9050** |
| ORT PTQ | int8 | 대칭 | minmax | 전체 | 0.3812 |
| ORT PTQ | int8 | 대칭 | entropy | 전체 | 0.3812 |
| ORT PTQ | int8 | 대칭 | percentile | 전체 | 0.2863 |
| ORT PTQ | int8 | 비대칭 | minmax | 전체 | 0.3063 |
| ORT PTQ | int8 | 대칭 | minmax | **Conv만** | 0.4283 |
| ORT PTQ | int8 | 대칭 | minmax | **Conv+Gemm** | 0.4283 |
| ORT PTQ, 민감 Conv 1/2/4개 제외 | int8 | 대칭 | minmax | 전체−K | 0.379 / 0.381 / 0.367 |
| PyTorch QAT 1,200스텝 | uint8 | — | — | 거의 전체 (QDQ 164쌍) | 0.4800 |
| PyTorch QAT 0스텝 | int8 | — | — | **부분** (QDQ 54쌍) | **0.9050** |

### 측정된 붕괴의 성격
- 로짓 SQNR **−1.56 dB**, FP32와 argmax 일치 **3/10** → 출력이 사실상 무상관
- 중간 텐서 141개의 SQNR이 **전부 음수** (최악 −5.90 dB, 20위 −1.85 dB) →
  특정 레이어가 아니라 **균일한 붕괴**. 그래서 민감 레이어 제외가 듣지 않는다
- SE 블록(GlobalAveragePool/Mul/Add)이 민감도 상위를 차지하지만, 그것들을 FP32로 남기고
  Conv만 양자화해도 0.4283 → **Conv 양자화 자체가 파괴적이다**

### 오늘 반증된 가설
1. ~~"부호 없는 활성이 원인"~~ — 부호 있는 int8도 같이 붕괴한다. 다만 signed는 ST 배포의
   **필수 조건**이다 (`NOT IMPLEMENTED: Onnx exporting model with quantized unsigned integer
   format is not supported`)
2. ~~"MobileNetV3가 양자화에 불리해 백본을 바꿔야 한다"~~ — HardSwish/HardSigmoid/QLinearConv
   가 NPU 지원 목록(318개)에 있다. 아키텍처가 막는 것은 아니다
3. ~~"features.1 첫 depthwise Conv 하나가 원인"~~ — 붕괴가 전 구간 균일하다. 그 레이어를
   제외해도 효과가 없다
4. ~~"보정법(entropy/percentile)이 해결책"~~ — 무효 또는 악화
5. ~~"ST 벤더 양자화기가 대신 풀어준다"~~ — `--quantize`는 텐서 포맷 설정 파일이고 PTQ가
   아니다. ST Core는 이미 양자화된 모델을 받는다

### 유일하게 잘 나온 조건이 주는 단서
PyTorch가 dtype 제약으로 **일부 레이어 양자화를 건너뛴** 경우(QDQ 54쌍)에만 0.9050이
나왔다. ORT로 같은 효과를 재현하려 Conv를 1~4개씩 제외해봤지만 실패했다 — 즉 "소수만
빼면 된다"가 아니라 **양자화된 Conv의 비율 자체**가 문제일 가능성이 있다.

## 다음에 시도할 것 (우선순위와 근거)

1. **입력 양자화 구조 수정.** 우리 모델은 ImageNet 정규화된 float(범위 약 −2.1~2.6)를
   입력으로 받는다. ST 샘플은 `mnist_int8_io_i8` — **int8 입출력**이다. 정규화를 첫 Conv에
   접어넣고 입력을 uint8 이미지(0~255)로 바꾸는 것이 배포 모델의 표준 형태다. 첫
   QuantizeLinear의 스케일이 어긋나면 오차가 망 전체로 증폭되므로, 균일한 붕괴와 부합한다
2. **TFLite int8 경로.** ST가 실제로 검증한 형식이다 (샘플이 TFLite, 모델 주는 TFLite 기반).
   TFLite 변환기는 대표 데이터셋으로 full-integer 양자화를 하며 스케일 처리 방식이 ORT와
   다르다. PyTorch → ONNX → TF → TFLite int8
3. **제대로 된 QAT 한 번.** 오늘 QAT는 1,200스텝이었고, uint8이거나(전체 양자화) int8인데
   레이어를 건너뛰는(부분 양자화) 상태여서 **깨끗한 조건으로 돌린 적이 없다.** 전 레이어
   int8 활성을 강제하려면 torchao의 pt2e 경로가 필요하다 (현재 venv에는 없음)

## 재현 스크립트
- `scripts/ptq_int8_n6.py` — ORT PTQ. `--activation int8|uint8 --act-symmetric --op-types
  --calib-method`로 조합 스윕
- `scripts/quant_sensitivity.py` — 중간 텐서 SQNR로 민감 레이어 순위 + 제외 개수 스윕
- `scripts/qat_n6.py` — FX 그래프 모드 QAT + QDQ ONNX export

---

## 추가 (같은 날 저녁): 해결됨 — 원인은 스템 절벽, 해법은 "QAT 가중치 + ORT 그래프"

### 원인
그래프 순서로 SQNR을 보면 **첫 depthwise Conv(features.1 block.0)에서 31dB → −2dB로 무너지고**
그 뒤는 전부 하류다. 절대 SQNR 순위로는 하류가 더 나빠 보여 원인이 7위로 밀렸다 — 연쇄 오차는
"가장 나쁜 곳"이 아니라 "처음 무너지는 곳"을 봐야 한다. 앞서 "features.1은 증상일 뿐"이라고 쓴
판단은 틀렸고 핸드오프의 원래 진단이 맞았다.
정확히는 depthwise의 *가중치*가 아니라 **스템 hardswish 출력의 활성 양자화**다: 그 Conv만
FP32로 빼면 효과가 없고(0.29), 입력 활성까지 FP32로 남겨야 회복된다(0.82).

### 실험 요약
| 설정 | RSCD acc |
|---|---|
| 스템 4노드(features.0 + 첫 depthwise) FP32, 나머지 ORT PTQ int8 대칭 = **D** | 0.8067 |
| + 후반 SE 블록 제외 / 보정 500장 / 비대칭 | 0.80 / 0.80 / 0.56 (무효) |
| 스템 FP32 + PyTorch QAT (quint8) fake-quant | **0.9075** (부분집합), 0.8975 (2,000장) |
| QAT 가중치 → ORT PTQ int8 **대칭** | 0.6925 (관습 불일치로 공적응 붕괴) |
| QAT 가중치 → ORT PTQ **uint8 비대칭**(같은 계열), FX 그래프 | 0.8230 |
| **QAT 가중치 → 평범한 RoadNet → v2 export → ORT PTQ uint8 → signed 변환** | **0.8885** ← 채택 |

### 채택 모델 (하이브리드-plain)
- RSCD 2,000장: acc **0.8885** / ice **0.964** (FP32 0.8945 / 0.974) · CARLA: acc 0.7769 / ice **0.788** (FP32 0.677)
- NPU: 118 epoch 중 **HW 94 / SW 24**, MACC 69.7M, 가중치 블롭 1.19MB (octoFlash 0x71000000)
- 입력 STAI_FORMAT_FLOAT [1,3,224,224] (스템이 FP32라 첫 양자화가 그래프 안), 출력 S8 [1,4] scale 0.0342

### 파이프라인 (재현)
```
scripts/qat_n6.py            # 스템 FP32 + quint8 QAT  → best.pt
scripts/qat_to_plain.py      # FX 키를 평범한 RoadNet으로 되돌려 v2 형태 FP32 ONNX
scripts/ptq_int8_n6.py --activation uint8 --exclude-until-depthwise   # ORT PTQ, 깨끗한 그래프
scripts/qdq_u8_to_i8.py      # uint8→int8 정확 변환 (영점 −128, 오차 0.00) — ST는 signed만 받음
stedgeai generate --st-neural-art "n6-allmems-O3@user_neuralart.json"  # network.c + xSPI2.raw
```
FX 키 매핑: qconfig=None인 스템은 융합 대신 한 단계 더 감싸여 `features.0.0.0.*`(conv)/`features.0.0.1.*`(bn),
융합된 블록은 `X.i.bn.*` → `X.(i+1).*`. strict 로드 0/0으로 확인.

### 왜 이 조합인가
QAT의 강건성은 **스케일 관습에 종속**된다 — 같은 계열(uint8 비대칭)로 재양자화해야 이어진다.
PyTorch export 그래프는 atonn이 덜 흡수한다(HW 54/SW 239) — 그래프는 ORT가 만든 것을 써야 한다.
그래서 "가중치는 QAT, 그래프는 ORT, 부호는 정확 변환"이다.

### 실보드 검증 준비물 (`~/icepredict/fw/`)
`npuval_{D,hyb}.elf`(NPU_Validation, 0x34000000 RAM 이미지, 개발 모드 GDB 적재), `npuval_*_weights.raw`,
`n6_npu_validate.sh D|hyb` — 블롭 굽기 → GDB 서버 → 적재·실행 → `validate --mode target --desc serial:/dev/ttyACM0:921600`.
relocatable 생성도 동작한다 (`network_rel.bin`, PATH에 arm-gcc 필요).

### NPU 통합 메모리 설계 (FSBL 펌웨어와의 겹침 해결)
기본 프로파일(`n6-allmems-O3`)은 활성을 `cpuRAM2`(0x34100000~0x34200000) 1MB에 100% 배치한다.
우리 NetXDuo FSBL 펌웨어는 ROM 0x34180400(255K) + RAM 0x341C0000(256K) — **그 안에 산다.** 그대로 통합하면 충돌.

해결: 커스텀 풀 `~/icepredict/fw/icepredict_fsbl.mpool` (`stm32n6.mpool` 복사본, `cpuRAM2` 1024→**512KB**)
+ 프로파일 `icepredict-fsbl@~/icepredict/fw/neuralart_icepredict.json`. 결과:
- `cpuRAM2` 사용 0%, 활성 = npuRAM3~5 100% + npuRAM6 50% + **hyperRAM 784KB**(외부 PSRAM, 느림)
- HW 94 / SW 24 유지, 가중치 1.13MB는 octoFlash 0x71000000 (우리 펌웨어 0x70000000과 무관)
- hyperRAM 784KB의 지연 비용은 실측 대상 (`npuval_hybfsbl`)
`stm32n6 __bootFromFlash.mpool`(ST 제공)은 이 atonn에서 파싱 실패이고, 내용도 cpuRAM2 1024KB를 그대로 써서 해결책이 아니다.

### 실보드 검증 변형 (`~/icepredict/fw/npuval_*.elf`, `n6_npu_validate.sh D|hyb|hybfsbl`)
| 변형 | 모델 | 풀 | 용도 |
|---|---|---|---|
| D | v2 PTQ 스템 FP32 (0.8067) | allmems | 절차 검증용 기준 |
| hyb | QAT+ORT 하이브리드 (0.8885) | allmems | 최고 정확도, cpuRAM2 사용(통합 불가 배치) |
| hybfsbl | 동일 모델 | icepredict-fsbl | **통합 대상 배치** — hyperRAM 비용 실측 |
`validate --mode host`는 Neural-ART에서 미지원 → 실보드가 유일한 검증 경로. 외부 플래시 소거·GDB halt는
개발/프로그래밍 모드(BOOT1 오른쪽)에서만 된다 (실행 모드: 'failed to erase memory', 'No device found').
