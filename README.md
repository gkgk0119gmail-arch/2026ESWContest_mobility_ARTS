# IcePredict — Pi 측 코드

AI 예측(1차) + RTOS 반응(2차) 이중 안전망 블랙아이스 대응 시스템. 이 저장소는 Raspberry Pi 5 쪽 코드와
STM32N6 이식 전 참조 구현·검증 코드를 담는다.

## 폴더
- `src/icepredict/common/protocol.py` — Pi↔N6↔CARLA ZeroMQ 메시지 규약(토픽·포트·데이터클래스)
- `src/icepredict/pi/context.py`    — 기상·위치 → 사전 위험도, N6 임계값(0.7→0.4), 융합 가중치
- `src/icepredict/pi/weather.py`    — 기상청 초단기실황 API (KMA_API_KEY 필요, `--mock` 가능)
- `src/icepredict/pi/fusion.py`     — 베이지안 융합 α·P_cls + β·spec + γ·(1-lane), 히스테리시스
- `src/icepredict/pi/reflectance.py`— OpenCV 반사도(전반사 피크)·차선 가시성(Hough)
- `src/icepredict/pi/imu_slip.py`   — 2차 IMU 미끄러짐 감지: 2상태 칼만 + 자전거 모델 잔차. N6 C 이식 원본
- `scripts/pi_context_node.py`      — 기상 수신→ctx 발행 노드
- `pi/zmq_echo_*.py`                — 통신 RTT 측정
- `dataset/` `logs/` `models/`      — SSD (git 제외)

## 실행
```bash
python3 -m pytest -q                                   # 단위 테스트
python3 scripts/pi_context_node.py --mock --once --feature bridge --hour 5
python3 pi/zmq_echo_server.py                          # PC에서 zmq_echo_client.py <Pi_IP>
```

## 기상청 API 키
공공데이터포털(data.go.kr) → "기상청_단기예보 조회서비스" 활용신청 → 일반 인증키(Decoding)를
`secrets.yaml`에 `kma_api_key: <키>` 로 저장 (git 제외).

## 하드웨어 상태 (2026-09-16)
- Raspberry Pi 5 16GB, Debian 13, SSD 1TB → /mnt/ssd (~/icepredict)
- AI HAT+ 2 (Hailo-10H) PCIe Gen3 인식, hailo-h10-all 5.1.1
- OpenCV 4.10, pyzmq 26.4, pygame 2.6
