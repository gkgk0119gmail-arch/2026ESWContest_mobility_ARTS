# 개발완료보고서

| 파일 | 쓰임 |
|---|---|
| **`2026ESWContest_모빌리티_ARTS_개발완료보고서.pptx`** | 원본 · 발표용. 16:9 · 20쪽. 글·표·사진이 전부 PowerPoint 개체이고, 주행 영상 7편이 **임베드**되어 있다 |
| `2026ESWContest_모빌리티_ARTS_개발완료보고서.pdf` | 제출용. 위 pptx 에서 변환한 것. 영상 자리에는 포스터 프레임이 들어간다 |
| `build_pptx.py` | 20쪽의 내용과 좌표가 전부 들어 있는 생성기 |
| `pptx_kit.py` | 디자인 시스템(색·글꼴·표·사진·영상 자동 재생) |
| `make_media.sh` | `logs/carla_demo/정리/` 의 주행 영상을 PowerPoint 용 H.264 로 줄이고 포스터 프레임·정지 화면을 뽑는다 |
| `pdf_prep.py` | PDF 변환 전에 영상을 포스터 그림으로 바꾼 임시 사본을 만든다 (`to_pdf.sh` 가 부른다) |
| `assets/` | 주행 장면 사진, `assets/video/` 에 임베드용 영상(mp4)과 포스터(jpg) |
| `fonts/` | Pretendard 4종(OFL). 발표 PC 에 설치하면 PDF 와 똑같이 보인다 |

## 다시 만들기

```bash
python3 sw/report/build_pptx.py     # .pptx 생성 (assets/video 가 있어야 한다 — 저장소에 들어 있다)
bash    sw/report/to_pdf.sh         # .pptx → .pdf (libreoffice 필요)
bash    sw/report/make_media.sh     # 영상·포스터 다시 뽑기 (주행 원본이 있는 PC 에서만)
```

PDF 는 **반드시 pptx 에서 뽑는다.** 원본이 두 벌이면 한쪽만 고쳐져 조용히 어긋난다.
영상이 든 pptx 를 그대로 변환하면 LibreOffice 가 영상을 PDF 안에 묻어 27 MB 가 되므로,
`to_pdf.sh` 는 `pdf_prep.py` 로 영상을 포스터 그림으로 바꾼 사본을 변환한다 (5.6 MB).

```bash
sudo apt-get install -y --no-install-recommends libreoffice-impress   # 변환기
pip install python-pptx pillow                                        # 생성기
```

## 발표 모드에서 영상이 저절로 재생된다

영상이 있는 쪽(2 · 11 · 14 · 16 · 17 · 18 · 19)은 슬라이드가 열리면 바로 재생된다.
PowerPoint 의 "시작: 자동으로" 와 같은 `p:timing` XML 을 `pptx_kit.autoplay()` 가 써 넣는다.
영상은 파일에 들어 있어서(링크가 아니라 임베드) pptx 하나만 들고 가면 된다.

| 쪽 | 영상 | 길이 |
|---|---|---|
| 2 | 같은 조건 조감 비교 — 방어 없음 vs RTOS 2차 방어 | 16 s |
| 11 | 1차 방어 — 카메라가 보고 빙판 23.6 m 앞 정지 | 9 s |
| 14 | 2차 방어 — 카메라 미인식, 보드가 미끄러짐 확정 후 정지 | 13 s |
| 16 | 방어 없음 — 차선 이탈 뒤 방호벽 충돌 | 12 s |
| 17 | 같은 조건 4화면 비교 (전방 카메라 + 조감 × 2) | 16 s |
| 18 | 시맨틱 라이다 64채널 | 13 s |
| 19 | 60 km/h 한계 — 2차가 개입해도 앞차와 충돌 | 11 s |

H.264 High 4.1 · 30 fps · 무음. 윈도우·맥 PowerPoint 2016 이후와 Keynote 에서 그대로 재생된다.
LibreOffice Impress 는 재생은 되지만 자동 시작은 지원이 들쭉날쭉하다.

## 디자인

현대자동차 브랜드 스타일가이드(Hyundai European Website Styleguide)를 기준으로 삼았다.

| 요소 | 값 | 쓰임 |
|---|---|---|
| Hyundai Blue | `#002C5F` | 표지 · 핵심 숫자 띠 · 저장소 상자 · 제목 머리표 |
| Active Blue | `#00AAD2` | **1차 방어(예측 · AI)** 의 의미색 |
| Active Red | `#E63312` | **2차 방어(반응 · RTOS)** · 위험의 의미색 |
| Light Sand | `#E4DCD3` → 패널 `#F6F3F2` | 설명 상자 바탕 |
| 글꼴 | Pretendard (ExtraBold 제목 · Regular 본문) | Hyundai Sans 는 사내 전용이라 같은 계열의 공개 글꼴을 썼다 |

둥근 모서리 · 테두리 · 색 띠를 쓰지 않고, 사진은 모서리 그대로 격자에 붙이고 여백으로 구분한다.

## 글꼴

`Pretendard` 로 지정했다. 발표 PC 에 없으면 PowerPoint 가 다른 고딕으로 바꿔 보여 주는데
줄 길이가 조금 달라질 수 있다. `fonts/` 의 OTF 네 개를 더블클릭해 설치하면 PDF 와 똑같이 보인다.
PDF 는 글꼴이 박혀 있어 어디서 열어도 같다.

## 좌표

전부 **px(1280×720)** 로 쓴다. 1280×720 px = 13.333in × 7.5in (96 dpi) 이므로 `px/96` 이 인치다.
`page()` 가 머리글·바닥글을 그리고 본문에 쓸 수 있는 `(위, 아래) y` 를 돌려준다.

## 사진

실물 사진은 `hw/images/` 를 쓰고, 보고서 칸 비율에 맞게 `pptx_kit.crop_cover()` 가 만들 때 자른다.
주행 장면은 `assets/*.jpg` (640×480, `make_media.sh` 와 이전 판에서 뽑아 둔 것)이고
16:9 가 필요한 자리는 `still16()` 이 위아래를 잘라 쓴다.
