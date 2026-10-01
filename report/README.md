# 개발완료보고서

| 파일 | 쓰임 |
|---|---|
| **`2026ESWContest_모빌리티_ARTS_개발완료보고서.pptx`** | 원본. 16:9 · 20쪽. 글·표·사진이 전부 PowerPoint 개체라 그대로 고칠 수 있다 |
| `2026ESWContest_모빌리티_ARTS_개발완료보고서.pdf` | 제출용. 위 pptx 에서 변환한 것 |
| `build_pptx.py` | 20쪽의 내용과 좌표가 전부 들어 있는 생성기 |
| `pptx_kit.py` | 상자·표·사진·반투명 덮개 같은 레이아웃 도구 |
| `assets/` | 주행 장면을 잘라 둔 사진 (나머지는 `docs/figures`·`docs/images` 를 그대로 쓴다) |

## 다시 만들기

```bash
python3 report/build_pptx.py     # .pptx 생성
bash    report/to_pdf.sh         # .pptx → .pdf (libreoffice 필요)
```

PDF 는 **반드시 pptx 에서 뽑는다.** 예전에는 HTML 을 따로 두고 chromium 으로 인쇄했는데,
그러면 원본이 두 벌이 되어 한쪽만 고치면 조용히 어긋난다. 그 경로는 없앴다.

```bash
sudo apt-get install -y --no-install-recommends libreoffice-impress   # 변환기
```

## 글꼴

`맑은 고딕`으로 지정했다. 윈도우·맥 PowerPoint 가 둘 다 가진 글꼴이라 어디서 열어도 깨지지 않는다.
다른 글꼴을 쓰려면 `pptx_kit.py` 의 `FONT` 한 줄만 바꾸면 된다.

## 좌표

전부 **px(1280×720)** 로 쓴다. 1280×720 px = 13.333in × 7.5in (96 dpi) 이므로 `px/96` 이 인치다.
`page()` 가 머리글·바닥글을 그리고 본문에 쓸 수 있는 `(위, 아래) y` 를 돌려준다.

## 아직 비어 있는 것

**5쪽 하드웨어 구성에 STM32N6570-DK 실물 사진이 없다.** 저장소에 보드 사진이 한 장도 없어
HIL 주행 화면으로 대신해 두었다. 보드 사진을 찍어 `docs/images/hw_n6.jpg` 로 넣고
`build_pptx.py` 의 `shots` 목록에서 해당 줄만 바꾸면 된다.
