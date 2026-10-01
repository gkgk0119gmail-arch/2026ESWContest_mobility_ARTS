#!/usr/bin/env python3
"""개발완료보고서(PPT, 20쪽) HTML 을 만든다 → chromium 으로 PDF 인쇄.

왜 HTML 인가: python-pptx 로 만들면 레이아웃을 PDF 로 바꿀 변환기(libreoffice)가 이 기계에 없다.
HTML 은 사진 배치·여백·한글 타이포를 그대로 제어할 수 있고 chromium --print-to-pdf 로
16:9 한 쪽당 한 슬라이드가 정확히 떨어진다.

사용: python3 report/build_report.py && bash report/to_pdf.sh
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "report" / "slides.html"

TITLE = "IcePredict — AI 예측과 RTOS 반응의 이중 안전망 블랙아이스 대응 시스템"
TEAM = "제24회 임베디드SW경진대회 · 자동차/모빌리티(현대자동차) · 팀 ARTS · 이지성 · 남윤상 · 김진찬"

slides = []


def S(section, title, body, lead=None, foot=None):
    slides.append(dict(section=section, title=title, body=body, lead=lead, foot=foot))


def img(src, cap=None, cls=""):
    c = f'<figcaption>{cap}</figcaption>' if cap else ""
    return f'<figure class="{cls}"><img src="{src}">{c}</figure>'


def table(head, rows, cls=""):
    th = "".join(f"<th>{h}</th>" for h in head)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>'


# ── 1. 표지 ──────────────────────────────────────────────────────────────────
slides.append(dict(cover=True))

# ── 2. 한 장 요약 ────────────────────────────────────────────────────────────
S("요약", "한 장 요약",
  f"""
  <div class="three">
    <div class="card blue"><h4>문제</h4>
      <p>블랙아이스는 <b>공개 데이터에 라벨이 없다.</b> 투명한 얼음은 아스팔트와 시각적으로 구분되지 않는다.
      카메라 한 겹으로는 반드시 놓치는 경우가 남는다.</p></div>
    <div class="card red"><h4>해법</h4>
      <p>예측과 반응을 <b>한 보드에 두 겹</b>으로 올렸다. 1차는 NPU 비전이 빙판에 닿기 전에 세우고,
      1차가 놓치면 2차가 IMU 거동으로 미끄러짐을 확정해 받는다.</p></div>
    <div class="card gray"><h4>증명</h4>
      <p>실물 STM32N6 보드가 실제 펌웨어를 돌리고 CARLA 가 센서·물리를 제공하는 <b>HIL</b> 로
      주행 108회, 실사진 69,358장을 보드에 직접 넣어 측정했다.</p></div>
  </div>
  <div class="kpis">
    <div class="kpi"><span class="n">96.1<small>%</small></span><span class="l">실사진 블랙아이스 6,340장 정답률<br>경보율 96.6 %</span></div>
    <div class="kpi"><span class="n">0.3<small>%</small></span><span class="l">마른 노면 19,018장 오경보율<br>젖은 노면 0.8 %</span></div>
    <div class="kpi red"><span class="n">22.6<small>µs</small></span><span class="l">2차 방어 최악 응답<br>리눅스는 5,790 µs</span></div>
    <div class="kpi red"><span class="n">9/9</span><span class="l">방어를 모두 끈 기준선<br>전부 제어 상실</span></div>
  </div>
  {img("assets/cmp_15.0.jpg", "<b>같은 빙판 · 같은 속도 · 같은 주변 차량.</b> 왼쪽은 방어 없음 — 제어를 잃고 25.4 km/h 로 차로를 가로질러 미끄러진다. 오른쪽은 STM32N6 RTOS 2차 방어 — 차선 안에서 정지했다", "grow")}
  """,
  lead="AI가 노면을 미리 보고, 못 보더라도 RTOS가 차량 거동으로 확실히 잡는다.")

# ── 3. 문제 정의 ────────────────────────────────────────────────────────────
S("문제", "왜 블랙아이스는 비전만으로 풀리지 않는가",
  f"""
  {img("../docs/figures/real_failure_miss.jpg", "<b>우리 보드가 실제로 놓친 실사진</b> — 전부 정답이 블랙아이스인데 '정상'으로 판정했다. 위험도 0.01~0.04 라 운영 문턱(0.603)은 물론 어떤 문턱으로도 잡히지 않는다", "wide")}
  <div class="three">
    <div class="box"><h4>라벨이 없다</h4>
    <p>공개 데이터셋에 <b>'블랙아이스' 라벨이 없다.</b> RSCD 의 <code>ice</code> 57,262장도
    다져진 눈·서리에 가까운 '얼음 노면'일 뿐, 투명한 블랙아이스를 따로 구분하지 않는다.</p></div>
    <div class="box"><h4>원래 어려운 문제다</h4>
    <p>투명·검은 얼음이 아스팔트와 시각적으로 구분되지 않는다는 것은 업계 통설이다.
    우리 모델도 마찬가지로, 실사진 얼음 6,340장 중 <b>215장(3.4 %)</b>이 문턱 아래에 남는다.</p></div>
    <div class="box red"><h4>그래서 내린 결론</h4>
    <p>1차는 블랙아이스를 <b>직접 맞히려 하지 않는다.</b> '결빙 위험 노면 확률 + 반사도 이상 + 기상 맥락'으로
    위험도를 올린다. 그리고 <b>그 전략이 실패할 때를 위해 2차가 있다.</b></p></div>
  </div>
  """,
  foot="약점을 숨기지 않고, 이중 안전망 구조의 '존재 이유'를 데이터로 뒷받침한다 — 개발계획서에 적었던 방침을 그대로 지켰다.")

# ── 4. 구조 ─────────────────────────────────────────────────────────────────
S("구조", "이중 안전망 — 예측(AI)과 반응(RTOS)",
  f"""
  {img("../docs/figures/architecture.png", None, "wide")}
  <div style="flex:0 0 auto">
    {table(["", "1차 방어 · 예측", "2차 방어 · 반응"], [
      ["센서", "전방 카메라", "IMU (횡가속 · yaw rate · 종가속)"],
      ["판단", "노면 4분류 + 반사도 + 기상 맥락 → 위험도", "칼만 필터 + 자전거 모델 잔차 → 미끄러짐 확정"],
      ["시점", "빙판에 닿기 <b>전</b>", "미끄러지기 <b>시작한 뒤</b>"],
      ["성격", "똑똑하지만 틀릴 수 있다", "단순하지만 제때 반드시 실행된다"],
      ["실행", "STM32N6 NPU · 25 ms", "STM32N6 + ThreadX · 최악 22.6 µs"],
    ], "cmp")}
  </div>
  """,
  foot="ThreadX 우선순위는 IMU 융합 스레드가 3, NPU 프레임 스레드가 4다. 25 ms짜리 추론이 돌고 있어도 IMU 판정이 선점한다 — 혼합 임계도(mixed-criticality) AI ECU 구조.")

# ── 5. 하드웨어 ──────────────────────────────────────────────────────────────
S("구현", "하드웨어 구성",
  f"""
  <div class="four grow">
    {img("../docs/images/hw_pi_stack.jpg", "Pi 5 16 GB + AI HAT+ 2 (Hailo-10H)<br>KKSB 케이스 · 액티브 쿨러")}
    {img("../docs/images/hw_d435i_nvme.jpg", "RealSense D435i (RGB·뎁스·IMU)<br>NVMe 1 TB 외장")}
    {img("../docs/images/hw_boot.jpg", "Raspberry Pi OS 부팅 · 브링업 확인")}
    {img("assets/p2_slip_c.jpg", "HIL 주행 — 보드가 판정하고 CARLA 가 물리를 돌린다")}
  </div>
  {table(["구성", "역할"], [
    ["<b>STM32N6570-DK</b>", "1차 NPU 추론(Neural-ART @ 1 GHz) + 2차 ThreadX 실시간 판정·제어. <b>두 임계도가 한 보드에 공존한다</b>"],
    ["<b>Raspberry Pi 5 16 GB + AI HAT+ 2</b>", "호스트. 기상 맥락 생성, ZMQ 통신 브리지, 교차 검증용 NPU (대회 필수 보드)"],
    ["<b>RealSense D435i</b>", "RGB · 뎁스 · IMU. 실측 노이즈 특성 확보용"],
    ["<b>RTX 5090 워크스테이션</b>", "CARLA 0.9.16 — HIL 환경의 물리·센서 제공"],
  ])}
  """)

# ── 6. 계획 대비 변경 ────────────────────────────────────────────────────────
S("구현", "개발계획서 대비 변경점과 그 이유",
  f"""
  {table(["항목", "개발계획서(6월)", "실제 구현", "바꾼 이유"], [
    ["2차 방어 실행 환경", "Pi 5 + 리눅스", "<b>STM32N6 + ThreadX RTOS</b>",
     "리눅스 최악 깨어남 지연이 <b>5,790 µs</b>로 측정됐다. 제어 주기 20 ms의 29 %를 한 번의 지터가 먹는다. 평균이 아니라 <b>꼬리</b>가 안전 기능의 기준이다"],
    ["1차 NPU", "Hailo-10H 단독", "<b>Neural-ART(N6) 주 · Hailo 교차검증</b>",
     "Hailo Dataflow Compiler 를 확보하지 못해 int8 변환 경로를 열지 못했다. 1·2차를 한 보드에 올리는 편이 선점 구조를 증명하기에도 맞다"],
    ["검증 방법", "1/5 차량 저마찰 노면 실측", "<b>HIL (실물 보드 + CARLA) + 실사진 69,358장</b>",
     "실물 빙판을 재현 가능하게 만들기 어렵다. 대신 <b>펌웨어는 실물 보드에서 그대로 돌리고</b>, 비전 성능은 시뮬 화면이 아닌 실제 도로 사진으로 측정했다"],
    ["노면 클래스", "정상 / 젖음 / 결빙 위험 / 포트홀", "동일 (유지)", "RSCD 27클래스 → 4클래스 매핑을 계획대로 적용"],
  ], "small")}
  <div class="split-5-5" style="flex:0 0 236px;align-items:stretch">
    {img("../docs/figures/rtos_latency.jpg", "<b>1번 변경의 근거</b> — 같은 연산의 지연 분포. 리눅스는 꼬리가 길다", "fit")}
    {img("../docs/figures/schedule_inverted.jpg", "<b>2번 변경의 근거</b> — 우선순위를 뒤집으면 NPU 추론(25 ms)이 IMU 주기(20 ms)를 막아 스케줄이 성립하지 않는다", "fit")}
  </div>
  <div class="box"><h4>바뀌지 않은 것</h4>
  <p>이중 안전망이라는 <b>구조</b>와, '블랙아이스를 직접 맞히지 않고 위험도를 올린다'는 <b>1차 전략</b>,
  '노면 라벨과 무관하게 차량 거동으로 확정한다'는 <b>2차 전략</b>은 계획서 그대로다.
  바뀐 것은 전부 <b>그 구조를 더 확실히 증명하기 위한 수단</b>이다.</p></div>
  """)

# ── 7. 데이터 전략 + 주 학습 데이터 ──────────────────────────────────────────
S("데이터", "데이터 전략 — 모델 헤드별 정답을 먼저 정의했다",
  f"""
  <p class="lead-in">데이터셋을 먼저 고르지 않았다. <b>각 헤드가 무엇을 정답으로 배워야 하는지</b>를 정의한 뒤 거기에 맞는 공개 데이터셋을 매핑했다.</p>
  <div class="split-5-5">
    {table(["방어", "필요한 정답(label)", "데이터셋", "활용"], [
      ["🔵 1차", "건조 / 젖음 / 눈 / 결빙 클래스", "<b>RSCD</b>", "주 학습 (사전학습)"],
      ["🔵 1차", "실측 수막 두께 (센서 GT)", "<b>RoadSaW</b>", "반사도 헤드 근거"],
      ["🔵 1차", "눈 · 비 · 안개 + 조도", "<b>AI Hub</b>", "국내 도로 도메인 검증"],
      ["🟠 2차", "실측 6축 IMU + 노면 라벨", "<b>PVS</b>", "칼만 공분산 설정"],
      ["🟠 2차", "RGB-IMU 동기화 · 악조건", "<b>ROAD</b>", "비교 기준선"],
      ["⚪ 보조", "실제 폭설 주행 씬", "<b>CADC · WADS</b>", "야간 · 악천후 점검"],
      ["⚪ 보조", "자동 라벨 (차선 가시성)", "<b>CARLA</b>", "합성 데이터 생성"],
    ], "small")}
    <div class="box blue"><h4>RSCD 27클래스 → 우리 4클래스</h4>
      <ul class="map">
        <li><b class="g">정상</b> ← 건조 (dry)</li>
        <li><b class="b">젖음</b> ← 젖음 · 물 고임 (wet · water)</li>
        <li><b class="r">결빙 위험</b> ← 결빙 · 녹은 눈 (ice · melted snow)</li>
        <li><b class="k">포트홀</b> ← 요철 '심함' 라벨</li>
      </ul>
      <p style="margin-top:8px">약 100만 장(공개 서브셋 37만 장) · 27클래스 = 마찰 6 × 재질 4 × 요철 3 ·
      베이징 약 700 km 실도로 주행(2022) · <b>240×360 패치</b>라 NPU 입력 크기에 맞는다 ·
      눈·얼음은 결빙 57,262 · 녹은 눈 64,263 · 신설 76,730장</p>
    </div>
  </div>
  <div class="three grow">
    {img("../docs/images/ds_rscd_classes.jpg", "RSCD 클래스별 샘플")}
    {img("../docs/images/ds_rscd_patch.jpg", "주행 영상에서 노면 영역만 잘라 패치로 쓴다")}
    {img("../docs/images/ds_rscd_camera.jpg", "차량 전방 카메라 — 20~80 km/h 주행 촬영")}
  </div>
  """,
  foot="데이터셋 그림은 각 공개 데이터셋의 논문·공식 사이트에서 가져온 설명 목적의 인용이며, 라이선스는 저장소 NOTICE 에 명시했다.")

# ── 9. 보조 데이터셋 ─────────────────────────────────────────────────────────
S("데이터", "반사도 · 국내 도로 · 실측 IMU · 폭설 — 보조 데이터셋 6종",
  f"""
  <div class="six">
    {img("../docs/images/ds_roadsaw.jpg", "<b>RoadSaW</b> 12클래스 = 노면 3종 × 젖음 4단계. MARWIS 로 수막 두께 실측 → 반사도 회귀의 근거. 패치 약 72만 장")}
    {img("../docs/images/ds_aihub.jpg", "<b>AI Hub</b> 승용 자율주행차 악천후(71626). 카메라·라이다·레이더 + 2D 분할 라벨 + 3D 박스 — 우리 라벨 내보내기 형식의 본보기")}
    {img("../docs/images/ds_pvs.jpg", "<b>PVS</b> MPU-9250 IMU 100 Hz 를 대시보드·서스펜션 3곳에. 9세트 = 차량 3 × 운전자 3 × 경로 3. 실측 노이즈로 칼만 공분산을 잡는다")}
    {img("../docs/images/ds_road.jpg", "<b>ROAD</b> 카메라 30 fps + IMU 5개 400 Hz 동기, 약 115만 프레임. 야간·폭우·먼지 — 우리 이중 구조와 같은 논리")}
    {img("../docs/images/ds_cadc.jpg", "<b>CADC</b> 눈길 실주행 5.6만 장 · 라이다 7천 스윕 · 75개 장면 (캐나다 워털루)")}
    {img("../docs/images/ds_wads.jpg", "<b>WADS</b> 미시간 폭설 라이다. '내리는 눈 / 쌓인 눈'을 포인트별 라벨(36억 점) — 시맨틱 라이다 표현의 참고")}
  </div>
  """,
  foot="⚠️ RoadSaW 에는 눈·얼음이 없고, ROAD 의 클래스는 노면 종류(아스팔트/블록/비포장)라 결빙이 없다. 한계를 알고 역할을 나눠 썼다.")

# ── 10. 모델 ────────────────────────────────────────────────────────────────
S("1차 방어", "모델과 int8 배포 — RoadNet",
  f"""
  <div class="split-5-5 fill">
    {table(["항목", "값"], [
      ["백본", "MobileNetV3-Small (ImageNet 사전학습)"],
      ["입력", "1×3×224×224 NCHW, int8"],
      ["출력", "4클래스 로짓 + <b>반사도 헤드</b> (576→64→1)"],
      ["내보내기", "ONNX opset 13, 고정 배치 1"],
      ["양자화", "int8 PTQ (QDQ), 가중치 채널별 · 활성 대칭"],
      ["입력 양자화", "scale 0.018658448, zero-point −14"],
      ["출력 양자화", "scale 0.029451849"],
      ["프레임당 전송", "150,528 B"],
      ["NPU", "Neural-ART @ 1 GHz · <b>추론 25 ms</b>"],
      ["카메라 ROI", "차량 전방 <b>7.9 ~ 42.2 m</b> (FOV 60°, 피치 −12°)"],
    ], "small")}
    <div>
      <div class="box blue"><h4>반사도 헤드를 따로 둔 이유</h4>
      <p>블랙아이스의 단서는 '무슨 노면인가'보다 <b>'빛을 어떻게 되돌리는가'</b>에 가깝다.
      분류 로짓 하나로는 젖음과 결빙이 섞인다. RoadSaW 의 실측 수막 두께를 정답으로
      반사도를 <b>회귀</b>로 따로 배우게 하고, 융합 단계에서 분류 확률과 더한다.</p></div>
      <div class="box"><h4>양자화에서 겪은 것</h4>
      <p>표준 PTQ 로는 stem 층의 활성 분포가 넓어 int8 에서 정확도가 떨어졌다.
      <b>stem 등화(equalization)와 클리핑</b>을 적용해 회복했고, QDQ 그래프를 u8→i8 로
      다시 쓰는 변환을 거쳐 Neural-ART 가 받는 형태로 맞췄다.</p></div>
    </div>
  </div>
  """)

# ── 11. 1차 성능 ────────────────────────────────────────────────────────────
S("1차 방어", "성능 — 실제 도로 사진 69,358장을 보드에 직접 넣었다",
  f"""
  <p class="lead-in">시뮬레이션 화면이 아니다. RSCD 실사진을 STM32N6 에 그대로 넣고 <b>추론·융합을 전부 보드에서</b> 돌려 받은 판정이다.</p>
  <div class="split-4-6">
    {table(["실제 노면", "장수", "정답률", "경보율 (문턱 0.603)"], [
      ["<b>블랙아이스</b>", "6,340", "<b class='r'>96.1 %</b>", "<b class='r'>96.6 %</b>"],
      ["마른 노면", "19,018", "80.7 %", "<b class='g'>0.3 %</b>"],
      ["젖은 노면", "34,440", "84.6 %", "<b class='g'>0.8 %</b>"],
      ["포트홀", "9,560", "90.2 %", "<b class='g'>0.3 %</b>"],
    ])}
    <div class="two tight grow">
      {img("../docs/figures/real_threshold.jpg", "운영 문턱 결정 — 69,358장 스윕", "fit")}
      {img("../docs/figures/real_confusion.jpg", "혼동 행렬", "fit")}
    </div>
  </div>
  """,
  foot="경보율은 보드의 alarm 플래그가 아니라 risk ≥ 문턱으로 계산했다. 보드 융합기의 히스테리시스(켜짐 0.441 / 꺼짐 0.341)는 연속 영상의 깜빡임을 막지만, 서로 무관한 낱장 사진을 이어 넣으면 앞 사진 상태가 넘어와 경보율이 부풀려지기 때문이다.")

# ── 12. 1차 동작 ────────────────────────────────────────────────────────────
S("1차 방어", "동작 — 카메라가 보고 빙판 앞에서 선다",
  f"""
  <div class="seq">
    <div class="step">{img("assets/p1_approach_c.jpg", None, "main")}{img("assets/p1_approach_b.jpg", None, "band")}<div class="cap"><b>① 접근</b><br>40 km/h 주행. 위는 전방 카메라(모델 입력), 아래는 조감.<br>위험도 0.00 · DRIVE</div></div>
    <div class="step">{img("assets/p1_warn_c.jpg", None, "main")}{img("assets/p1_warn_b.jpg", None, "band")}<div class="cap"><b>② 1차 경보 <span class="t">6.10 s</span></b><br>위험도 <b>0.443</b> → 문턱 돌파. 빙판 가장자리까지 11.4 m.<br>PRIMARY WARNING · BRAKE</div></div>
    <div class="step">{img("assets/p1_stop_c.jpg", None, "main")}{img("assets/p1_stop_b.jpg", None, "band")}<div class="cap"><b>③ 정지 <span class="t">7.14 s</span></b><br>빙판 <b>23.6 m 앞</b>에서 멈췄다.<br>빙판에 닿지 않았다 — 예방에 성공</div></div>
  </div>
  """,
  lead="CARLA Town04 · 맑은 낮 · 40 km/h · 마찰 0.02 · 1차 방어 ON (보드 NPU 추론 + 융합)",
  foot="같은 주행선에서 날씨 11종(맑음·흐림·젖은 노면·보슬비·폭우·해질녘·밤·비 오는 밤·눈 등)을 모두 돌렸다. 인식·정지 14회 · 경고 늦음 7회 · 미인식 10회 · 오경보 1회.")

# ── 13. 융합 ────────────────────────────────────────────────────────────────
S("1차 방어", "위험도 융합 — 기상·위치 맥락으로 가중치를 바꾼다",
  f"""
  <div class="formula">risk = α · p<sub>ice</sub> &nbsp;+&nbsp; β · 반사도 &nbsp;+&nbsp; γ · (1 − 차선 가시성)</div>
  <div class="split-5-5 fill">
    {table(["기상 · 위치 맥락", "α (분류)", "β (반사도)", "γ (차선)"], [
      ["교량 · 새벽 등 결빙 위험 높음", "0.35", "<b>0.45</b>", "0.20"],
      ["일반", "<b>0.50</b>", "0.30", "0.20"],
      ["저위험", "<b>0.55</b>", "0.15", "<b>0.30</b>"],
    ])}
    <div class="box red"><h4>설계에서 고친 두 가지</h4>
    <p><b>① 재정규화.</b> 쓸 수 없는 신호에 0을 넣으면 안 된다. 0은 중립값이 아니라 <b>최솟값</b>이라 위험도 상한이 잘린다.
    그래서 쓸 수 없는 신호는 <b>분모에서 빼고</b> 남은 신호로 다시 정규화한다.</p>
    <p><b>② 문턱 재교정.</b> 초기 0.441 에서는 젖은 노면 오경보가 40 %였다. 실사진 69,358장 스윕으로
    절벽 구간을 찾아 <b>0.603</b> 으로 올렸고 오경보가 0.8 %로 내려갔다. 폭우는 강수 게이트로 따로 분리했다.</p></div>
  </div>
  """,
  foot="운영 문턱 0.603 — 손으로 고른 값이 아니라 실사진 전량 스윕에서 결정했고, logs/rscd_board_samples.jsonl 로 문턱을 바꿔 재계산할 수 있다.")

# ── 14. 2차 원리 ────────────────────────────────────────────────────────────
S("2차 방어", "원리 — 칼만 필터 + 자전거 모델 잔차",
  f"""
  <div class="split-6-4">
    <div>
      <div class="box red"><h4>판단 절차</h4>
      <ol>
        <li>IMU 50 Hz 에서 횡가속 a<sub>y</sub> · yaw rate · 종가속을 받는다</li>
        <li><b>2상태 칼만 필터</b>(값 · 변화율)로 잡음을 거른다 — Q = 2000/3000, R = 0.16/0.0005</li>
        <li><b>자전거 모델</b>로 '이 속도·이 조향이면 나와야 할 yaw rate'를 계산한다<br>
            고속 언더스티어 보정 <code>1/(1+(v/v<sub>ch</sub>)²)</code>, v<sub>ch</sub> = 17 m/s</li>
        <li>실측과 모델의 <b>잔차</b>가 타원 밖이면 미끄러짐 — 3샘플(60 ms) 연속이면 확정</li>
      </ol></div>
      {table(["항목", "값", "왜 이 값인가"], [
        ["축거 L", "2.7 m", "자전거 모델"],
        ["조향 지연 τ", "0.06 s", "급조향 시 '모델이 즉답한다'는 가정이 <b>오탐</b>을 만들었다"],
        ["횡가속 임계", "0.30 g", ""],
        ["yaw 오차 임계", "0.35 rad/s", ""],
        ["판정 규칙", "<b>타원</b>", "직사각형은 모서리에서 저속 지연이 생겼다"],
        ["저마찰 트리거", "제동 ≥ 0.3 이 0.3 s 지속 + 감속 &lt; 1.2 m/s²", "필터 지연 구간의 오탐 방지"],
      ], "small")}
    </div>
    {img("../docs/figures/rule_boundary.jpg", "판정 경계 — 직사각형(점선) 대 타원(실선). 모서리에 걸리던 저속 구간이 타원에서 사라진다", "fit grow")}
  </div>
  """,
  foot="C 코어(fw/npu_lib/slip_core.h)는 HAL·OS 비의존이라 호스트에서도 컴파일된다. 파이썬 참조 구현과 같은 판정을 내리는지 매 커밋 검증한다.")

# ── 15. 2차 동작 ────────────────────────────────────────────────────────────
S("2차 방어", "동작 — 카메라가 놓쳐도 보드가 받는다",
  f"""
  <div class="seq">
    <div class="step">{img("assets/p2_enter_c.jpg", None, "main")}{img("assets/p2_enter_b.jpg", None, "band")}<div class="cap"><b>① 빙판 진입 <span class="t">5.84 s</span></b><br>1차를 끈 상태(미인식 가정). 38.7 km/h 로 그대로 들어간다.<br>위는 조감(차선 위치), 아래는 전방 카메라. 주변 차량 8대</div></div>
    <div class="step">{img("assets/p2_slip_c.jpg", None, "main")}{img("assets/p2_slip_b.jpg", None, "band")}<div class="cap"><b>② 미끄러짐 확정 <span class="t">7.62 s</span></b><br><b>STM32N6 RTOS</b> 가 판정. a<sub>y</sub> = −0.364 g, yaw 오차 −0.282<br>진입 1.78 s 만에 확정 → hard_stop</div></div>
    <div class="step">{img("assets/p2_stop_c.jpg", None, "main")}{img("assets/p2_stop_b.jpg", None, "band")}<div class="cap"><b>③ 차선 유지하며 정지 <span class="t">11.68 s</span></b><br>왼쪽 차로가 비어(gap 999 m) evade_left 로 전환 후 정지.<br>차선 안에서 멈췄다 — 스핀도 충돌도 없다</div></div>
  </div>
  """,
  lead="CARLA Town04 · 맑은 낮 · 40 km/h · 마찰 0.08 · 1차 방어 OFF · 2차 판정 주체 = STM32N6 보드",
  foot="2차 방어 발동 62회(보드 판정 53 · 호스트 9). 진입→확정 평균 2.41 s, 확정→정지 평균 3.78 s. 비상 제어 모드 분포 — 차선유지 29 · 정지 37 · 최대제동 20 · 우회피 11 · 좌회피 11.")

# ── 16. RTOS ────────────────────────────────────────────────────────────────
S("2차 방어", "왜 리눅스가 아니라 RTOS 인가 — 평균이 아니라 꼬리",
  f"""
  <p class="lead-in">같은 연산을 Pi 5 리눅스가 <b>평균 146배 빠르게</b> 한다. 그런데도 RTOS 보드를 쓴다. 안전 기능의 기준은 평균이 아니라 <b>최악</b>이기 때문이다.</p>
  <div class="split-5-5">
    <div>
      {table(["플랫폼", "표본", "중앙값", "최악"], [
        ["Pi 5 + Linux · 유휴", "20,000", "69 µs", "<b class='r'>5,790 µs</b>"],
        ["Pi 5 + Linux · 부하", "20,000", "68 µs", "<b class='r'>5,429 µs</b>"],
        ["STM32N6 + ThreadX (응답 전체)", "49,405", "12.4 µs", "<b class='g'>22.6 µs</b>"],
      ])}
      <div class="box red"><h4>이것이 왜 치명적인가</h4>
      <p>제어 주기는 20 ms 다. 리눅스의 최악 지터 5,790 µs 는 <b>한 주기의 29 %</b>를 한 번에 먹는다.
      40 km/h 에서 5.8 ms 는 6.4 cm 지만, 미끄러짐이 시작된 뒤의 제어 루프에서는 그 한 번이
      <b>차선 유지와 스핀을 가른다.</b></p></div>
    </div>
    <div>
      {img("../docs/figures/latency_cdf.jpg", "지연 분포 CDF — 리눅스는 꼬리가 길다")}
      {img("../docs/figures/schedule_rtos.jpg", "RM 스케줄 — IMU(우선순위 3)가 NPU(4)를 선점한다")}
    </div>
  </div>
  """,
  foot="우선순위를 뒤집으면(NPU가 IMU보다 높으면) 25 ms 추론이 20 ms 주기를 막아 스케줄 자체가 불가능해진다 — 근거: docs/evidence/10_스케줄가능성_분석.md")

# ── 17. 기준선 ──────────────────────────────────────────────────────────────
S("검증", "방어가 없으면 — 기준선 9건 전부 제어를 잃었다",
  f"""
  <div class="split-6-4" style="align-items:stretch">
    <div style="display:flex;flex-direction:column;gap:9px;min-height:0">
      {img("assets/base_spin_c.jpg", "<b>차선 이탈 → 스핀</b> &nbsp;12.02 s 이탈(횡오프셋 −1.45 m, 헤딩 오차 −41°) → 14.82 s 스핀(−99°). 차체가 차로를 가로질러 90° 가까이 돌아 있다", "fit grow")}
      {img("assets/base_wall_front.jpg", "<b>방호벽 충돌 순간의 전방 카메라</b> &nbsp;11.42 s 이탈 → 12.22 s 스핀 → 12.32 s 에 12.0 km/h 로 벽에 충돌. 화면이 방호벽으로 가득 찼다", "band")}
    </div>
    <div>
      <div class="box"><h4>결과</h4>
      <p><b>9건 전부</b> 차선을 이탈하고 스핀했다.<br>
      그중 <b>5건</b>은 12~17 km/h 로 방호벽에 충돌했다.<br>
      주변 차량이 있던 <b>4건</b>은 충돌 전에 스핀으로 끝났다.</p></div>
      <div class="box gray" style="margin-top:11px"><h4>기준선의 가정</h4>
      <p>제어를 잃은 뒤에는 <b>운전자 입력을 모형화하지 않는다.</b> 비교 대상은 '우리 시스템이 개입하느냐'이지
      '운전자가 얼마나 잘 대처하느냐'가 아니기 때문이다.</p>
      <p>스핀·차선 이탈이 확정되면 자율주행을 떼고 관성에 맡기며, 정지·충돌하거나 4초가 지나면 주행을 끝낸다.</p>
      <p><b>자율주행을 그대로 두면</b> 스핀으로 역방향을 본 차가 다시 가속해 빙판으로 유턴해 들어갔다.
      그때 난 충돌은 빙판이 아니라 역주행 탓이라 근거가 될 수 없었다.</p></div>
    </div>
  </div>
  """,
  lead="1차·2차를 모두 끈 채 같은 빙판·같은 속도·같은 주행선으로 들어갔다.")

# ── 18. 비교 ────────────────────────────────────────────────────────────────
S("검증", "같은 조건 직접 비교 — 방어 없음 vs STM32N6 RTOS 2차 방어",
  f"""
  {img("assets/cmp_15.0.jpg", None, "fit grow")}
  <div class="two">
    <div class="box"><h4>왼쪽 · 방어 없음</h4>
    <p>15.0 s · <b>25.4 km/h</b> · 12.0 s 에 차선을 이탈해 제어를 잃었고, 차체가 진행 방향과
    어긋난 채 차로를 가로질러 미끄러지는 중이다. 뒤따르던 차량이 그대로 접근하고 있다.</p></div>
    <div class="box red"><h4>오른쪽 · STM32N6 RTOS 2차 방어</h4>
    <p>13.2 s · <b>0.0 km/h</b> · <code>IMU SLIP (lat_acc) a<sub>y</sub> −0.36 g, yaw −0.28 → STOPPED</code><br>
    차선 안에서, 빙판을 벗어난 지점에 정지했다.</p></div>
  </div>
  """,
  lead="같은 날씨 · 같은 주행선 · 같은 빙판 · 같은 주변 차량 8대. 차이는 2차 방어 하나뿐이다.",
  foot="날씨 4종(맑은 낮 · 밤 · 젖은 노면 · 눈)에서 같은 비교를 만들었다. 전체 영상은 저장소 media/ 와 logs/carla_demo/정리/G_비교_방어없음_vs_RTOS/ 에 있다.")

# ── 19. 센서 시각화 ─────────────────────────────────────────────────────────
S("검증", "센서 시각화와 자동 라벨 — 무엇을 보고 판단했는지 남긴다",
  f"""
  <div class="split-5-5" style="align-items:stretch">
    {img("../docs/figures/weather_1차_카메라경고.jpg", "<b>날씨 11종에서 1차 경보가 난 같은 순간</b> — 맑음 · 흐림 · 젖은 노면 · 보슬비 · 폭우 · 해질녘 · 밤 · 비 오는 밤 · 눈. 같은 주행선, 같은 빙판에서 날씨만 바꿔 돌렸다", "fit")}
    <div style="display:flex;flex-direction:column;gap:11px;min-height:0">
      {img("assets/lidar_sem.jpg", "<b>시맨틱 라이다 64채널</b> — 청록이 빙판 구간, 초록 상자가 차량 3D 박스. 미끄러짐 확정 순간", "fit")}
      {img("../docs/figures/triplet_rscdtex_visual_00320.jpg", "<b>카메라 · 2D 분할 라벨 · 라이다</b> — 도로/차선/빙판/차량 라벨을 자동 생성한다 (AI Hub 형식 참고)", "fit")}
    </div>
  </div>
  """,
  foot="주행 108회 전체의 이벤트 JSON 을 저장소에 함께 올렸다. CARLA 도 보드도 없이 scripts/analysis/summarize_runs.py 만으로 집계표를 재생성할 수 있다 — 손으로 적은 숫자가 아니라는 증명이다.")

# ── 20. 한계 ────────────────────────────────────────────────────────────────
S("한계", "한계와 실패 사례 — 숨기지 않고 남긴 것",
  f"""
  <div class="four grow">
    <div class="fail">{img("assets/p1_approach_c.jpg")}
      <h4>① 경보 거리는 기하 문제다</h4>
      <p>모델이 요구하는 최소 80 px 과 ROI 해상도 91 px 이 경보 거리를 정한다.
      알고리즘이 아니라 <b>해상도</b>를 올리는 것이 먼저다.</p></div>
    <div class="fail">{img("assets/lim60_c.jpg")}
      <h4>② 2차는 예방이 아니다</h4>
      <p>60 km/h. 1차가 경고했지만 제동이 늦어 진입했고, 2차가 0.26 s 만에 개입했는데도
      <b>39.2 km/h 로 앞차와 충돌</b>했다.</p></div>
    <div class="fail">{img("assets/falsealarm_c.jpg")}
      <h4>③ 오경보도 난다</h4>
      <p>밤. 빙판이 <b>41 m 밖</b>(ROI 7.9~42.2 m 바깥)에 있는데 어두운 노면을 얼음으로 보아
      위험도 0.52 로 경보가 났다.</p></div>
    <div class="fail">{img("assets/domaingap_c.jpg")}
      <h4>④ 질감 도메인 갭</h4>
      <p>RSCD 실사진 질감을 CARLA 노면에 입히면, 같은 모델이 <b>그 얼음에 반응하지 않는다.</b>
      재수집·재학습이 다음 과제다.</p></div>
  </div>
  <div class="box gray"><h4>그 밖에 남은 것</h4>
  <p>· <b>Hailo-10H 교차 검증</b>은 Dataflow Compiler 미확보로 열지 못했다. 1차는 STM32N6 Neural-ART 단독 결과다.<br>
     · <b>D435i 실측 IMU 노이즈</b>는 PVS 공개 데이터로 대체했다. 실물 연결 측정은 남은 과제다.<br>
     · <b>기준선은 제어 상실 후 운전자 입력을 모형화하지 않는다.</b> '아무 보조도 없을 때의 물리적 귀결'로 읽어야 한다.<br>
     · 젖은 노면 경보율 0.8 %는 더 낮출 여지가 있다. β(반사도 가중치)와 문턱의 재조정이 필요하다.</p></div>
  """)

# ── 21. 결론 ────────────────────────────────────────────────────────────────
S("결론", "결론 · 재현성 · 향후 계획",
  f"""
  <div class="split-5-5" style="flex:0 0 auto;align-items:start">
    <div style="display:flex;flex-direction:column;gap:13px">
      <div class="box blue"><h4>무엇을 만들었나</h4>
      <p>블랙아이스 라벨이 없다는 <b>데이터의 한계</b>에서 출발해, 카메라 한 겹에 기대지 않는
      <b>이중 안전망</b>을 STM32N6 한 보드 위에 올렸다. 1차는 빙판에 닿기 전에 세우고,
      1차가 틀려도 2차가 최악 22.6 µs 안에 반드시 받는다.</p></div>
      <div class="box"><h4>재현</h4>
      <p>모든 숫자는 실측에서 <b>자동 생성</b>된다.</p>
      <pre>git clone &lt;저장소&gt; &amp;&amp; pip install -e .
python3 scripts/analysis/summarize_runs.py   # 집계표 재생성
python3 -m pytest -q                         # 단위 테스트 19개
python3 fw/npu_lib/test_slip_core.py         # C 코어 ↔ 파이썬 동치</pre>
      <p>근거 문서 19건 · 결과 그림 29장 · 주행 이벤트 108건을 저장소에 함께 올렸다.</p></div>
    </div>
    <div style="display:flex;flex-direction:column;gap:13px">
      <div class="box red"><h4>향후 계획</h4>
      <ol>
        <li><b>경보 거리 확대</b> — ROI 해상도를 올려 1차가 보는 거리를 늘린다. 2차 의존도를 낮추는 가장 직접적인 길이다</li>
        <li><b>실사진 질감 재학습</b> — RSCD 질감을 CARLA 노면에 입혀 재수집하고 도메인 갭을 닫는다</li>
        <li><b>Hailo-10H 교차 검증</b> — Dataflow Compiler 확보 후 동일 모델을 두 NPU 에서 비교</li>
        <li><b>D435i 실측 IMU</b> — 실물 노이즈로 칼만 공분산을 재조정</li>
      </ol></div>
      <div class="repo"><h4>소스 코드</h4>
      <p class="url">github.com/gkgk0119gmail-arch/<br>2026ESWContest_mobility_ARTS</p>
      <p class="sub">MIT 라이선스 · 영상·그림·이벤트 데이터 포함 · README 에서 바로 재생된다</p></div>
    </div>
  </div>
  {img("assets/cmp_15.0.jpg", "같은 빙판 · 같은 속도 · 같은 주변 차량 — 왼쪽은 방어 없음, 오른쪽은 STM32N6 RTOS 2차 방어", "grow")}
  """,
  foot="팀 ARTS · 이지성 · 남윤상 · 김진찬 — 제24회 임베디드SW경진대회 자동차/모빌리티(현대자동차) 부문")


CSS = """
@font-face{font-family:NSR;src:url('file:///usr/share/fonts/truetype/nanum/NanumSquareRoundR.ttf');font-weight:400;font-style:normal}
@font-face{font-family:NSR;src:url('file:///usr/share/fonts/truetype/nanum/NanumSquareRoundB.ttf');font-weight:700;font-style:normal}
@font-face{font-family:NGC;src:url('file:///usr/share/fonts/truetype/nanum/NanumGothicCoding.ttf')}
@page{size:13.333in 7.5in;margin:0}
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1280px;background:#fff}
body{font-family:NSR,'NanumSquareRound',sans-serif;color:#0f172a;
     -webkit-font-smoothing:antialiased}
.slide{width:1280px;height:720px;page-break-after:always;position:relative;overflow:hidden;
       padding:46px 56px 52px;display:flex;flex-direction:column;background:#fff}
.slide:last-child{page-break-after:auto}

/* 머리글 */
.hdr{display:flex;align-items:baseline;gap:14px;border-bottom:2px solid #e2e8f0;padding-bottom:9px;margin-bottom:16px}
.sec{font-size:13px;font-weight:700;color:#fff;background:#1e293b;padding:3px 11px;border-radius:11px;letter-spacing:.02em}
.sec.b1{background:#2563eb}.sec.b2{background:#dc2626}
h2{font-size:28px;font-weight:700;letter-spacing:-.01em;flex:1}
.pg{font-size:13px;color:#94a3b8;font-weight:700}
.lead{font-size:14.5px;color:#475569;margin:-6px 0 14px;font-weight:700}
.lead-in{font-size:15px;color:#334155;margin-bottom:14px;line-height:1.55}
.body{flex:1;display:flex;flex-direction:column;gap:13px;min-height:0;overflow:hidden}
.grow{flex:1;min-height:0}
.grow img{height:100%;object-fit:cover;object-position:center top}
.grow figure.fit img{object-fit:contain}
.two.grow{height:100%}
.two.grow figure{display:flex;flex-direction:column;min-height:0}
.three.grow figure{display:flex;flex-direction:column;min-height:0}
.three.grow figure img{flex:1;min-height:0}
.foot{font-size:11.5px;color:#64748b;line-height:1.5;border-top:1px solid #e2e8f0;padding-top:8px;margin-top:10px}

/* 표지 */
.cover{background:#0b1220;color:#fff;padding:0;display:block}
.cover .bg{position:absolute;inset:0;opacity:.30}
.cover .bg img{width:100%;height:100%;object-fit:cover}
.cover .grad{position:absolute;inset:0;background:linear-gradient(105deg,#0b1220 32%,rgba(11,18,32,.72) 62%,rgba(11,18,32,.42))}
.cover .in{position:absolute;inset:0;padding:74px 76px;display:flex;flex-direction:column;justify-content:center}
.cover .tag{font-size:14px;font-weight:700;color:#60a5fa;letter-spacing:.06em;margin-bottom:16px}
.cover h1{font-size:50px;font-weight:700;line-height:1.24;letter-spacing:-.02em;max-width:900px}
.cover .sub{font-size:20px;color:#cbd5e1;margin-top:20px;font-weight:700;line-height:1.5;max-width:800px}
.cover .meta{margin-top:46px;font-size:14.5px;color:#94a3b8;line-height:1.8}
.cover .rule{width:92px;height:5px;background:#dc2626;margin:26px 0 0;border-radius:3px}
.cover .doc{position:absolute;right:76px;bottom:62px;text-align:right;font-size:13px;color:#64748b;line-height:1.8}

/* 배치 */
.split-5-5{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start;flex:1;min-height:0}
.split-5-5.fill{align-items:stretch}
.split-5-5.fill>table{align-self:start}
.split-5-5.fill>div{display:flex;flex-direction:column;gap:11px}
.split-5-5.fill>div>.box:last-child{flex:1}
.split-6-4{display:grid;grid-template-columns:1.42fr 1fr;gap:16px;align-items:start;flex:1;min-height:0}
.split-4-6{display:grid;grid-template-columns:1fr 1.3fr;gap:16px;align-items:stretch;flex:1;min-height:0}
.two{display:grid;grid-template-columns:1fr 1fr;gap:13px;min-height:0}
.two>figure{min-height:0}
.three{display:grid;grid-template-columns:repeat(3,1fr);gap:13px}
.four{display:grid;grid-template-columns:repeat(4,1fr);gap:11px}
.four.grow{flex:1;min-height:0}
.four.grow>figure{display:flex;flex-direction:column;min-height:0}
.four.grow>figure img{flex:1;min-height:0;object-fit:cover}
.six{display:grid;grid-template-columns:repeat(3,1fr);grid-template-rows:1fr 1fr;gap:11px;flex:1;min-height:0}
.tight{gap:9px}

figure{margin:0;min-width:0}
figure img{width:100%;display:block;border-radius:7px;border:1px solid #e2e8f0;object-fit:cover}
figure.fit{display:flex;flex-direction:column;min-height:0}
figure.fit img{flex:1;min-height:0;object-fit:contain;background:#fff}
figure.wide{flex:1;min-height:0;display:flex;flex-direction:column}
figure.wide img{flex:1;min-height:0;object-fit:contain;background:#f8fafc}
figcaption{font-size:10.8px;color:#64748b;margin-top:5px;line-height:1.45}
.six figure{display:flex;flex-direction:column;min-height:0}
.six figure img{flex:1;min-height:0;object-fit:cover}

/* 표 */
table{width:100%;border-collapse:collapse;font-size:12.8px}
th{background:#1e293b;color:#fff;font-weight:700;text-align:left;padding:7px 10px;font-size:12px}
td{padding:6.5px 10px;border-bottom:1px solid #e9eef4;vertical-align:top;line-height:1.45}
tbody tr:nth-child(even){background:#f8fafc}
table.small{font-size:11.6px}
table.small td{padding:5.5px 9px}
table.cmp td:first-child{font-weight:700;color:#475569;width:88px;background:#f1f5f9}

/* 상자 */
.box{background:#f8fafc;border:1px solid #e2e8f0;border-left:4px solid #94a3b8;border-radius:7px;padding:12px 15px}
.box.blue{border-left-color:#2563eb;background:#eff6ff}
.box.red{border-left-color:#dc2626;background:#fef2f2}
.box.gray{border-left-color:#64748b}
.box h4{font-size:13.5px;font-weight:700;margin-bottom:6px;color:#0f172a}
.box p{font-size:12.6px;line-height:1.62;color:#334155}
.box p+p{margin-top:7px}
.box ul,.box ol{margin-left:17px;font-size:12.6px;line-height:1.62;color:#334155}
.box li{margin-bottom:4px}
.box pre{font-family:NGC,monospace;font-size:11px;background:#0f172a;color:#e2e8f0;
         padding:10px 12px;border-radius:6px;line-height:1.62;margin:7px 0;white-space:pre-wrap}
ul.map{list-style:none;margin-left:0}
ul.map li{font-size:13px;margin-bottom:5px}
.g{color:#16a34a}.b{color:#2563eb}.r{color:#dc2626}.k{color:#475569}
code{font-family:NGC,monospace;font-size:.93em;background:#eef2f7;padding:1px 5px;border-radius:4px}

/* 지표 */
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:13px;margin-top:4px}
.kpi{background:#f0f7ff;border:1px solid #dbeafe;border-radius:9px;padding:15px 16px;text-align:center}
.kpi.red{background:#fef2f2;border-color:#fecaca}
.kpi .n{display:block;font-size:40px;font-weight:700;color:#2563eb;letter-spacing:-.02em;line-height:1}
.kpi.red .n{color:#dc2626}
.kpi .n small{font-size:20px;margin-left:2px}
.kpi .l{display:block;font-size:11.6px;color:#475569;margin-top:9px;line-height:1.5;font-weight:700}
.card{border-radius:9px;padding:15px 17px;border:1px solid #e2e8f0;background:#f8fafc}
.card.blue{background:#eff6ff;border-color:#bfdbfe}
.card.red{background:#fef2f2;border-color:#fecaca}
.card h4{font-size:15px;font-weight:700;margin-bottom:7px}
.card p{font-size:12.8px;line-height:1.65;color:#334155}

/* 시나리오 3단 */
.seq{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;flex:1;min-height:0;align-content:start}
.step{display:flex;flex-direction:column;gap:7px;min-height:0}
.step figure.main img{width:100%;height:auto;object-fit:contain}
.step figure.band img{width:100%;height:118px;object-fit:cover;object-position:center 42%}
figure.band img{width:100%;height:124px;object-fit:cover;object-position:center 50%}
.step figure.band{margin-top:-2px}
.step .cap{font-size:12.2px;line-height:1.55;color:#334155}
.step .cap b{font-size:13.5px;color:#0f172a}
.step .cap .t{color:#dc2626;font-weight:700}
.fail{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:11px;display:flex;flex-direction:column;gap:8px}
.four.grow .fail figure{flex:1;min-height:0;display:flex}
.four.grow .fail figure img{width:100%;height:100%;object-fit:cover}
.fail figure img{width:100%;height:auto;object-fit:contain}
.fail h4{font-size:13.5px;font-weight:700}
.fail p{font-size:12px;line-height:1.55;color:#475569}

.formula{background:#0f172a;color:#fff;border-radius:8px;padding:15px;text-align:center;
         font-size:21px;font-weight:700;letter-spacing:.01em}
.repo{background:#0f172a;color:#fff;border-radius:8px;padding:14px 17px;margin-top:13px}
.repo h4{font-size:12.5px;color:#94a3b8;font-weight:700;margin-bottom:6px}
.repo .url{font-size:16.5px;font-weight:700;line-height:1.4;word-break:break-all}
.repo .sub{font-size:11.5px;color:#94a3b8;margin-top:7px}
"""

SEC_CLS = {"1차 방어": "b1", "2차 방어": "b2"}


def render():
    out = [f"<!doctype html><html lang='ko'><meta charset='utf-8'><title>{TITLE}</title><style>{CSS}</style><body>"]
    n = 0
    for s in slides:
        n += 1
        if s.get("cover"):
            out.append(f"""
<section class="slide cover">
  <div class="bg"><img src="assets/cmp_15.0.jpg"></div>
  <div class="grad"></div>
  <div class="in">
    <div class="tag">개발완료보고서</div>
    <h1>IcePredict<br><span style="font-size:36px;font-weight:700">AI 예측과 RTOS 반응의<br>이중 안전망 블랙아이스 대응 시스템</span></h1>
    <div class="rule"></div>
    <div class="sub">AI가 노면을 미리 보고, 못 보더라도 RTOS가 차량 거동으로 확실히 잡는다.</div>
    <div class="meta">제24회 임베디드SW경진대회 · 자동차/모빌리티 부문 (현대자동차)<br>
      <b style="color:#e2e8f0">팀 ARTS</b> · 이지성 · 남윤상 · 김진찬</div>
  </div>
  <div class="doc">STM32N6570-DK · Neural-ART NPU · ThreadX<br>
    Raspberry Pi 5 + AI HAT+ 2 · CARLA HIL<br>
    github.com/gkgk0119gmail-arch/2026ESWContest_mobility_ARTS</div>
</section>""")
            continue
        cls = SEC_CLS.get(s["section"], "")
        lead = f'<div class="lead">{s["lead"]}</div>' if s.get("lead") else ""
        foot = f'<div class="foot">{s["foot"]}</div>' if s.get("foot") else ""
        out.append(f"""
<section class="slide">
  <div class="hdr"><span class="sec {cls}">{s['section']}</span><h2>{s['title']}</h2><span class="pg">{n} / {len(slides)}</span></div>
  {lead}
  <div class="body">{s['body']}</div>
  {foot}
</section>""")
    out.append("</body></html>")
    OUT.write_text("\n".join(out), encoding="utf-8")
    print(f"{OUT}  — {len(slides)}쪽")


if __name__ == "__main__":
    render()
