# sw/scripts/

역할별로 다섯 묶음이다. 폴더만 보고 "본 파이프라인"과 "한 번 쓰고 남긴 실험 기록"을
구분할 수 있게 나눴다.

| 폴더 | 개수 | 무엇이 들어 있나 |
|---|---:|---|
| [`sim/`](sim) | 12 | CARLA 수집·데모·영상 정리 |
| [`model/`](model) | 17 | RoadNet 학습, int8 양자화, 반사도 헤드 |
| [`deploy/`](deploy) | 15 | STM32N6 적재·브리지·보드 평가 |
| [`analysis/`](analysis) | 28 | 집계·그림·근거 문서 생성 |
| [`archive/`](archive) | 53 | 일회성 배치 체인 (재현 이력 보존용) |

## 자주 쓰는 입구

```bash
# 분석만 재현한다 (CARLA·보드 없이, 저장소의 이벤트 데이터만으로)
python3 sw/scripts/analysis/summarize_runs.py     # sw/docs/evidence/00_집계.md 재생성
python3 sw/scripts/analysis/make_figures.py       # 결과 그림
bash    sw/scripts/analysis/sync_evidence.sh      # logs/ → sw/docs/ 복사
bash    sw/scripts/analysis/build_readme_media.sh # README 애니메이션 WebP 생성

# CARLA 데모 (렌더 머신 필요)
python3 sw/scripts/sim/carla_demo.py --help
bash    sw/scripts/sim/demo_batch_5090.sh         # 날씨 × 시나리오 배치
bash    sw/scripts/sim/rerun_baseline.sh          # 방어 없음 기준선 재촬영

# 모델
python3 sw/scripts/model/train_cls.py --smoke     # 노면 4클래스 분류기
python3 sw/scripts/model/ptq_int8_n6.py           # int8 PTQ (QDQ, opset 13)

# 보드
bash    sw/scripts/deploy/n6_fw_load.sh           # 펌웨어 적재 (SWD, BOOT1 스위치 필요)
python3 sw/scripts/deploy/n6_bridge.py --help     # Pi ↔ N6 ↔ CARLA 브리지
bash    sw/scripts/deploy/run_board_eval.sh       # 실사진 69,358장 보드 평가
```

## 호스트 설정

렌더 머신·데스크탑 주소는 저장소에 박아 두지 않고 환경 변수로 받는다.
기본값은 `user@render-host` 같은 자리표시자이므로 팀 환경에서는 아래를 내보내야 한다.

```bash
export RENDER_HOST=사용자@렌더머신      # CARLA 를 돌리는 기계
export RENDER_IP=렌더머신              # n6_bridge 가 접속할 주소
export DESK_HOST=사용자@데스크탑        # 보조 데스크탑
export DESK_IP=데스크탑
```

## archive/ 에 대한 주의

`batch_*`·`after_*`·`verify_*` 는 특정 날짜의 실험을 돌렸던 체인이다. 결과와 결론은
`sw/docs/evidence/` 에 문서로 남아 있고, 이 스크립트들은 "그 숫자가 어떤 명령에서 나왔는지"를
보이기 위해 남겼다. 지금 그대로 실행되는 것을 보장하지 않으며, 일부는 python heredoc 안에
당시의 절대 경로가 그대로 들어 있다.
