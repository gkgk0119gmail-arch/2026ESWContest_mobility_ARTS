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
  --st-neural-art "n6-allmems-O3@<STEDGEAI>/sw/scripts/N6_sw/scripts/user_neuralart.json"
  → 99 epoch 중 HW 90 / SW 9
```

프로파일은 `sw/scripts/N6_sw/scripts/user_neuralart.json`에 정의돼 있다 (n6-extram, n6-extflash,
n6-noextmem, n6-nointmem, n6-allmems-O1/O2/O3/Oauto). 각각 메모리 풀(.mpool)과 atonn
옵션(`--optimization 3 --Oauto-sched --Ocache-opt` 등)을 지정한다.

**대조 실험**: ST 자체 샘플 `sw/scripts/N6_sw/scripts/models/mnist_int8_io_i8.tflite`는 5 epoch 중
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
- `sw/scripts/model/ptq_int8_n6.py` — ORT PTQ. `--activation int8|uint8 --act-symmetric --op-types
  --calib-method`로 조합 스윕
- `sw/scripts/model/quant_sensitivity.py` — 중간 텐서 SQNR로 민감 레이어 순위 + 제외 개수 스윕
- `sw/scripts/model/qat_n6.py` — FX 그래프 모드 QAT + QDQ ONNX export

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
sw/scripts/model/qat_n6.py            # 스템 FP32 + quint8 QAT  → best.pt
sw/scripts/model/qat_to_plain.py      # FX 키를 평범한 RoadNet으로 되돌려 v2 형태 FP32 ONNX
sw/scripts/model/ptq_int8_n6.py --activation uint8 --exclude-until-depthwise   # ORT PTQ, 깨끗한 그래프
sw/scripts/model/qdq_u8_to_i8.py      # uint8→int8 정확 변환 (영점 −128, 오차 0.00) — ST는 signed만 받음
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

---

## 2026-09-19 실보드 실측 (STM32N6570-DK, NPU_Validation 펌웨어, `validate --mode target`)

| 변형 | 배치 | 추론/샘플 | 스템 SW(epoch 2+5) | NPU 시간 비중 | ONNX 대비 cos / nse |
|---|---|---|---|---|---|
| D | allmems (cpuRAM2) | 186.8 ms | — | 7.6% | 0.971 / 0.946 |
| hyb | allmems (cpuRAM2) | 199.8 ms | 152 + 38 | 7.6% | 0.9954 / 0.9915 |
| hybfsbl | cpuRAM2 512K + **hyperRAM 784K** | 233.3 ms | 152 + 38 | 10.3% | 0.9954 / 0.9915 |
| **hybfsbl2** | **AXISRAM1 1M + cpuRAM2 512K, hyperRAM 0** | **199.1 ms** | 135 + 29 | 7.7% | 0.9954 / 0.9915 |

결론
- **지연의 82%(164 ms)가 FP32로 남긴 스템**(features.0 Conv 3×3@224² + 첫 depthwise)이 CM55 float로 도는 비용이다.
  NPU 94 epoch는 전부 합쳐 ~20 ms. 스템을 int8로 올리면 ~35–40 ms가 된다. **혼합 정밀도는 정확도는 살렸지만
  지연 예산(30 ms)에서는 실패** — 스템 int8화가 필수다.
- hyperRAM 페널티 33 ms는 풀 v2로 제거됐다. 풀 v2 = `icepredict_fsbl2.mpool`: flexMEM 0x34000000 400K +
  cpuRAM1 0x34064000 624K(AXISRAM1 전체) + cpuRAM2 512K, hyperRAM 0. 우리 FSBL(0x34180400~)과 겹치지 않는다.
- 보드 기준 ONNX 대비 cos 0.9954 — 호스트 int8 시뮬과 보드 NPU 실행이 일치한다 (D는 0.971로 더 나쁨: 모델 자체 차이).

측정 펌웨어 변경 (ST 설치본 `Projects/STM32N6570-DK/Applications/NPU_Validation`, 원본은 `.orig`로 보관)
- 링커 `AXISRAM1_S`: 0x34000000/1024K → **0x34180000/512K** (우리 FSBL과 같은 자리). 풀 v2가 0x34000000~을 활성으로
  쓰므로 원래 자리에 두면 모델이 펌웨어를 덮어써 죽는다 (실제로 `read timeout`, `Lost target connection`).
- `misc_toolbox.c` `SCB->VTOR = 0x34000000` 하드코딩 → `(uint32_t)g_pfnVectors`. 재링크 후 이게 없으면 첫 인터럽트에서 죽는다.
- 개발 모드 적재는 `ST-LINK_gdbserver -m 1 -k --halt` (AP1 = Cortex-M55; AP0은 halt 실패). `pkill -x`는 15자 comm에
  안 맞으니 포트(`fuser -k 61234/tcp`)로 정리. 러너: `sw/scripts/deploy/n6_npu_validate.sh D|hyb|hybfsbl|hybfsbl2` (`APID=1 RESET=1`).

다음: 스템 int8화. 절벽 텐서(스템 hardswish 출력)의 양자화 범위만 백분위 클리핑(`sw/scripts/model/stem_clip_quant.py`,
ORT `TensorQuantOverrides`) → 안 되면 스템을 클리핑된 고정 범위 fake-quant로 QAT.

---

## 2026-09-19 해결: 스템 int8화 — 실보드 **38.2 ms** (199 ms → 5.2배), cos 0.9982

### 진짜 원인 (진단 2회 수정 끝에)
features.0 **hardswish 출력의 채널 간 범위 편차 565×** — 채널 11이 희소 스파이크(중앙 −0.05, p99 38, max 175),
하위 채널은 0.3. per-tensor 스케일이 채널 11에 맞춰지면 나머지 15개 채널이 2~3단계로 뭉개지고, depthwise는
채널을 섞지 않아 그 손상이 그대로 출력 붕괴가 된다. depthwise **출력**의 편차는 2.5×로 정상이었다(→ 그 출력을
겨냥한 클리핑·CLE는 무효). 가중치 편차 690×는 그 거울상(활성 큰 채널 ↔ 가중치 작음).

### 실패한 시도와 이유
- 스템 텐서 클리핑 p99.9/99.99/99.999: 0.36/0.31/— — 몸통 간 100× 차이는 꼬리를 잘라도 남는다
- hardswish **뒤** 1×1 depthwise 균등화 Conv(v1): 0.363 — 그 Conv도 양자화 대상이라 ORT가 그 **입력**(불균형
  텐서)에 per-tensor Q를 먼저 박는다. 문제를 한 노드 뒤로 옮겼을 뿐
- hardswish **앞** CLE는 hardswish가 양의 동차함수가 아니라 부정확(항등 구간 15%)

### 성공: 균등화를 Conv0에 접어 넣고 게이트만 float (`sw/scripts/model/stem_equalize.py`, 수학적 동치 1.9e-06)
```
Conv0'(채널 c 가중치·편향 ÷ s_c) → x' = x/s_c          [int8, 균형]
Mul_s(x', s_c) → x   (게이트 계산 전용)                   [FLOAT 제외 — 여기 Q하면 절벽 재현]
HardSigmoid(x) → h ∈ [0,1]                              [FLOAT 제외]
Mul_hs(x', h) → hardswish(x)/s_c                        [int8, |y'| ≤ |x'|]
dw3x3'(채널 c 가중치 × s_c)  = dw3x3(hardswish(x))       [정확히 동치]
```
s_c = 채널 p99.99 범위 / 중앙값 (하한 0.05). float으로 남는 건 112²×16 원소 Mul·HardSigmoid 둘뿐.

| 모델 | RSCD acc | RSCD ice | CARLA ice | NPU HW/SW | **실보드** | cos |
|---|---|---|---|---|---|---|
| 스템 FP32 하이브리드 (hybfsbl2) | 0.8885 | 0.964 | 0.788 | 94/24 | 199.1 ms | 0.9954 |
| **균등화 전량 int8 (eq)** | **0.8842** | **0.970** | 0.632 | **96/21** | **38.2 ms** | **0.9982** |
| FP32 v2 | 0.9008 | 0.983 | 0.677 | — | — | — |

파이프라인: `qat_to_plain` → `stem_equalize` → `ptq_int8_n6 --activation uint8 --exclude-nodes <Mul_s,HardSigmoid>`
→ `qdq_u8_to_i8` → `stedgeai generate icepredict-fsbl2` → `n6_npu_validate.sh eq`.
남은 SW 52%는 Q/DQ·게이트·SW ctrl 등 작은 조각들. CARLA ice 0.632는 스템 양자화 비용 — 균등화 그래프 위에서
짧은 QAT를 돌리면 회복 여지가 있다(아직 안 함).

### 최종 (eq3): 게이트 경로까지 int8 — 실보드 **21.7 ms**, cos 0.9992
게이트 입력 x(Mul_s 출력)는 HardSigmoid가 [−3,3] 밖에서 포화하므로 **그 범위로 잘라 양자화해도 정확히 동치**
(오차 ≤ 6/255/6 ≈ 0.004). `--override-tensor ".../Conv_output_0_unscaled:-3:3"` 하나로 float 노드가 0개가 된다.

| 모델 | RSCD acc / ice | CARLA ice | NPU HW/SW | 실보드 | HW% | cos |
|---|---|---|---|---|---|---|
| 스템 FP32 하이브리드 | 0.8885 / 0.964 | 0.788 | 94/24 | 199.1 ms | 8% | 0.9954 |
| eq (게이트 float) | 0.8842 / 0.970 | 0.632 | 96/21 | 38.2 ms | 41% | 0.9982 |
| **eq3 (전량 int8)** | **0.8800 / 0.977** | 0.604 | **97/16** | **21.7 ms** | **73%** | **0.9992** |

실보드 분해: HW 97 epoch 18.4 ms, SW 16 epoch 3.3 ms(최대 0.53 ms), SW ctrl 2.5 ms. 남은 것은 후반 SE 블록의 HardSigmoid
float 조각들과 입력 QuantizeLinear뿐이다. 입력을 uint8 이미지로 받고 정규화를 Conv0에 접으면 입력 변환(~1.8 ms)과
호스트 전송량(602KB→150KB/프레임)이 함께 준다 — 통합 단계에서 할 것.

**배포 산출물** (데스크탑)
- 모델: `~/icepredict/models/roadnet_v2_eq/ptq3/roadnet_int8_signed.onnx` (QDQ, signed int8, 입력 f32 [1,3,224,224], 출력 int8 [1,4])
- NPU 코드: `~/icepredict/models/n6_gen_eq3/out/{network.c,network.h,stai_network.c,stai_network.h,network_atonbuf.xSPI2.raw}`
  (가중치 블롭 1.19MB → 외부 플래시 0x71000000, 프로파일 `icepredict-fsbl2`, 활성 AXISRAM1 1M + cpuRAM2 512K + npuRAM)
- 재현: `stem_equalize.py` → `ptq_int8_n6.py --activation uint8 --override-tensor <Mul_s출력>:-3:3` → `qdq_u8_to_i8.py`
  → `stedgeai generate --st-neural-art icepredict-fsbl2@neuralart_icepredict.json`

남은 과제: CARLA ice recall 0.677 → 0.604 (스템 양자화 비용). 균등화 그래프를 PyTorch에 이식해 짧은 QAT를 돌리면 회복
여지. 그리고 **우리 NetXDuo FSBL 펌웨어에 통합** — ll_aton 런타임 + network.c + NPU 초기화(캐시·클럭·RIF)를 넣고
`infer` 패킷 대신 이미지를 받아 보드가 직접 추론.
