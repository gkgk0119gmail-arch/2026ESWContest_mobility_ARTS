# STM32N6 NPU 배포 분석 (2026-09-18)

ST Edge AI Core 3.0.0을 설치해 RoadNet v2를 STM32N6570-DK의 Neural-ART NPU에 올릴 수
있는지 실측했다.

## 설치

```
~/icepredict/tools/stedgeai/3.0/Utilities/linux/stedgeai   # CLI
~/icepredict/tools/stedgeai/3.0/Utilities/linux/atonn      # Neural-ART NPU 컴파일러 (19MB)
```

- `stedgeai-lin.zip`는 **온라인 설치기**(Qt Installer Framework, x86-64)다. Pi는 aarch64라
  실행되지 않으니 데스크탑에서 설치해야 한다
- 저장소 접근에 **로그인이 필요 없다** (다운로드만 myST 로그인)
- 헤드리스 설치:
  `./stedgeai-linux-onlineinstaller -t <dir> --accept-licenses --accept-messages --confirm-command in stedgeai0300 stedgeai0300.stneuralart stedgeai0300.stm32mcu`
- **최신 4.0.1에는 `stneuralart` 모듈이 없다.** Neural-ART(=N6 NPU)가 있는 최신은 **3.0.0**
  (Neural-ART v11.0.0). N6 작업은 3.0.0을 써야 한다

## FP32 모델 분석 결과 — NPU 가속 사실상 0

`stedgeai analyze --model roadnet.onnx --type onnx --target stm32n6 --st-neural-art`

```
전체 143 epoch  →  순수 소프트웨어(CPU) 142개 / 순수 하드웨어(NPU) 1개 / 하이브리드 0개
MACC 59,094,820
weights     4.081 MB  (octoFlash, 112MB 중 3.64%)
activations 2.306 MB  (cpuRAM2 1MB + npuRAM3~5 각 448kB — 전부 100% 사용)
```

모든 레이어가 `SW`에 `(float)`로 찍힌다. Neural-ART NPU는 int8만 가속하므로 FP32 모델은
전량 CPU 폴백이다. **양자화 없이는 NPU 배포에 의미가 없다.**

activations가 npuRAM3~5를 100% 채우는 것도 주의할 점이다 — 양자화하면 절반 이하로
줄어들지만, 입력 해상도를 올릴 여유는 없다.

## 결정적 발견 두 가지

### 1. 백본 교체는 필요 없다
`stedgeai supported-ops --target stm32n6` 결과 318개 연산자 지원 목록에
**`HardSwish`, `HardSigmoid`, `Mul`, `Add`, `QLinearConv`, `QLinearMul` 등이 모두 포함**된다.
MobileNetV3-Small의 hard-swish·SE 블록이 지원되므로 아키텍처가 막는 것이 아니다.
"MobileNetV3가 양자화에 불리하니 MobileNetV2/ResNet으로 바꾼다"는 안은 **폐기**한다.

### 2. ST 툴체인은 양자화를 대신 해주지 않는다
`--quantize FILE`은 **텐서 포맷 설정 파일(JSON)**이고 PTQ를 수행하는 옵션이 아니다.
명령도 `analyze | generate | validate | supported-ops` 뿐이고 `quantize`가 없다.
ST Core는 **이미 양자화된 모델**(QDQ ONNX 또는 int8 TFLite)을 입력으로 받는다.

따라서 HANDOFF에 적힌 "벤더 툴체인 자체 양자화기로 INT8 재시도" 기대는 **성립하지 않는다.**
INT8 붕괴(acc 0.910 → 0.48)는 우리가 학습 단계에서 풀어야 한다.

## 다음 수: QAT (양자화 인지 학습)

근거: 원인이 이미 특정돼 있다 — `features.1`의 첫 depthwise Conv에서 최대오차 15.
PTQ로는 per-channel·보정법·conv-only 전부 무효였다. 아키텍처는 지원되고(발견 1),
벤더 양자화기도 없으므로(발견 2), 남은 것은 학습 중 양자화 오차를 모델이 흡수하게 하는
QAT다. 학습 코드와 데이터(RSCD 95.9만 + CARLA 1.2만)가 이미 있다.

절차:
1. PyTorch `torch.ao.quantization` FX 그래프 모드로 QAT 준비, RoadNet v2에서 이어 학습
2. 낮은 lr로 소수 에포크 — 목표는 RSCD test acc 0.88 유지 (FP32 v2 값)
3. QDQ ONNX로 export
4. `stedgeai analyze --target stm32n6 --st-neural-art`로 **HW epoch 수가 실제로 늘었는지** 확인
   (이 수치가 NPU 배포 성공의 유일한 객관적 지표다)
5. `stedgeai generate`로 C 코드 생성 → N6 펌웨어에 통합

## 기타

- 분석 중 `Do you allow statistics to improve the command line? (y)es / (n)o` 프롬프트가
  뜬다. 비대화식 실행에서는 응답이 없어 그대로 진행되지만, 다음부터는 텔레메트리를
  명시적으로 끄고 돌린다
- 리포트: `~/icepredict/models/n6_build/st_ai_output/network_analyze_report.txt`
