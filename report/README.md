# 개발완료보고서

대회 제출물: **`2026ESWContest_모빌리티_ARTS_개발완료보고서.pdf`** (16:9 · 20쪽 · PDF)

## 다시 만들기

```bash
python3 report/build_report.py    # slides.html 생성
bash    report/to_pdf.sh          # chromium 으로 PDF 인쇄
```

`build_report.py` 한 파일에 20쪽의 내용과 레이아웃이 모두 들어 있다.
사진은 `report/assets/`(주행 장면을 잘라 둔 것)와 저장소의 `docs/figures/`·`docs/images/` 를 그대로 쓴다.

## 왜 HTML 인가

python-pptx 로 .pptx 를 만들 수는 있지만, 이 기계에 pptx→PDF 변환기(libreoffice)가 없다.
HTML 은 사진 배치·여백·한글 타이포를 그대로 제어할 수 있고,
`@page` 를 13.333in × 7.5in 로 잡아 두면 `chromium --print-to-pdf` 가 한 쪽에 한 슬라이드씩 정확히 떨어뜨린다.

## 아직 비어 있는 것

**5쪽 하드웨어 구성에 STM32N6570-DK 실물 사진이 없다.** 저장소에 보드 사진이 한 장도 없어
HIL 주행 화면으로 대신해 두었다. 보드 사진을 찍어 `docs/images/hw_n6.jpg` 로 넣고
`build_report.py` 의 해당 `img(...)` 한 줄만 바꾸면 된다.
