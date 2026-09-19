#!/usr/bin/env python3
"""icepredict_n6 펌웨어에 NPU 프레임 추론 경로를 추가한다 (app_netxduo.c)."""
import sys
F = sys.argv[1]
s = open(F, encoding="utf-8", errors="surrogateescape").read()

# 1) include + 정의
anchor = "/* USER CODE END PD */"
block = r'''
/* ===================== IcePredict NPU 프레임 추론 (UDP 5559) =====================
   Pi가 224x224 ROI를 모델 입력 형식(int8, scale 0.018658 / zp -14, NCHW 150528B)으로 양자화해
   1400B 청크로 보낸다. 보드는 재조립 → Neural-ART NPU 추론(~22ms) → 로짓 역양자화 → softmax →
   ip_fuse(같은 위험도 융합) → 판정 응답. 이로써 보드가 '판정'만이 아니라 '추론'을 한다.
   청크 헤더(LE): u32 frame_id, u16 idx, u16 n, u16 len, u16 flags  = 12B, payload <= 1400B */
#include "npu_infer.h"
#include <math.h>
#define FR_UDP_PORT        5559
#define FR_CHUNK           1400u
#define FR_MAX_CHUNKS      ((NPU_IN_BYTES + FR_CHUNK - 1) / FR_CHUNK)   /* 108 */
#define FR_THREAD_PRIORITY 4
#define FR_THREAD_STACK    8192
typedef struct __attribute__((packed)) { uint32_t frame_id; uint16_t idx; uint16_t n; uint16_t len; uint16_t flags; } fr_hdr_t;
typedef struct __attribute__((packed)) {
  uint32_t frame_id;
  float    logits[4];     /* normal, wet, black_ice, pothole (역양자화) */
  float    risk;
  uint8_t  alarm;
  uint8_t  level;
  uint16_t pad;
  uint32_t infer_us;      /* NPU 추론만 */
  uint32_t total_us;      /* 첫 청크 수신 → 응답 직전 */
} fr_verdict_t;
'''
assert anchor in s; s = s.replace(anchor, block + "\n" + anchor, 1)

# 2) 전역
anchor = "/* ===== Context-switch benchmark (metric i: <5.7us) ===== */"
block = r'''/* ===== NPU 프레임 추론 객체 ===== */
NX_UDP_SOCKET  FrameSocket;
TX_THREAD      FrameThread;
static UCHAR   FrameStack[FR_THREAD_STACK];
static int8_t  fr_buf[NPU_IN_BYTES] __attribute__((aligned(32)));
static uint8_t fr_have[FR_MAX_CHUNKS];
static void Frame_Thread_Entry(ULONG thread_input);
static int   npu_ok = 0;

'''
assert anchor in s; s = s.replace(anchor, block + anchor, 1)

# 3) 소켓·스레드 생성 (IcePredict 스레드 생성 뒤)
anchor = '''  ret = tx_thread_create(&IcePredictThread, "IcePredict Fusion", IcePredict_Thread_Entry, 0,'''
idx = s.find(anchor); assert idx > 0
end = s.find("\n\n", s.find("}", idx))
ins = r'''

  /* ===== NPU 프레임 소켓 + 스레드 ===== */
  ret = nx_udp_socket_create(&NetXDuoEthIpInstance, &FrameSocket, "Frame UDP",
                             NX_IP_NORMAL, NX_FRAGMENT_OKAY, NX_IP_TIME_TO_LIVE, 128);
  if (ret != NX_SUCCESS) printf("FR: socket create failed: 0x%02x\n", ret);
  ret = tx_thread_create(&FrameThread, "NPU Frames", Frame_Thread_Entry, 0,
                         FrameStack, FR_THREAD_STACK, FR_THREAD_PRIORITY, FR_THREAD_PRIORITY,
                         TX_NO_TIME_SLICE, TX_AUTO_START);
  if (ret != TX_SUCCESS) printf("FR: thread create failed: 0x%02x\n", ret);
'''
s = s[:end] + ins + s[end:]

# 4) 스레드 본문
s += r'''

/* ===================== NPU 프레임 스레드 =====================
   부팅 시 NPU 초기화 + 0 입력 자가진단 추론(로짓·시간 출력) 후 UDP 5559 청크를 받는다. */
static void fr_softmax4(const float *z, float *p)
{
  float m = z[0]; for (int i = 1; i < 4; i++) if (z[i] > m) m = z[i];
  float s = 0.f;  for (int i = 0; i < 4; i++) { p[i] = expf(z[i] - m); s += p[i]; }
  for (int i = 0; i < 4; i++) p[i] /= s;
}

static void Frame_Thread_Entry(ULONG thread_input)
{
  NX_PACKET *rx, *tx;
  ULONG bytes, src_ip; UINT src_port, status;
  (void)thread_input;

  /* --- NPU 초기화 (커널이 도는 스레드 컨텍스트에서: ThreadX OSAL이 세마포어를 만든다) --- */
  int r = npu_init();
  if (r != 0) { printf("NPU: init failed (%d)\n", r); }
  else {
    int8_t q[NPU_OUT_N]; uint32_t us = 0;
    memset(fr_buf, (int8_t)NPU_IN_ZP, sizeof(fr_buf));          /* 정규화 공간의 0 = zp */
    if (npu_infer_s8(fr_buf, q, &us) == 0) {
      npu_ok = 1;
      printf("NPU: ready, self-test %lu us, logits q=[%d %d %d %d]\n", (unsigned long)us, q[0], q[1], q[2], q[3]);
    } else printf("NPU: self-test infer failed\n");
  }

  while (IpAddress == 0u) { tx_thread_sleep(10); }
  status = nx_udp_socket_bind(&FrameSocket, FR_UDP_PORT, TX_WAIT_FOREVER);
  if (status != NX_SUCCESS) { printf("FR: bind failed: 0x%02x\n", status); return; }
  printf("NPU frames listening on UDP port %d (%s)\n", FR_UDP_PORT, npu_ok ? "NPU ok" : "NPU DISABLED");

  uint32_t cur_id = 0xFFFFFFFFu, have_n = 0, need_n = 0, t_first = 0;
  fr_hdr_t h;
  while (1)
  {
    status = nx_udp_socket_receive(&FrameSocket, &rx, TX_WAIT_FOREVER);
    if (status != NX_SUCCESS) continue;
    ULONG len = 0; nx_packet_length_get(rx, &len);
    nx_udp_source_extract(rx, &src_ip, &src_port);
    if (len < sizeof(h) || nx_packet_data_extract_offset(rx, 0, &h, sizeof(h), &bytes) != NX_SUCCESS)
    { nx_packet_release(rx); continue; }
    if (h.n == 0 || h.n > FR_MAX_CHUNKS || h.idx >= h.n || h.len > FR_CHUNK ||
        (uint32_t)h.idx * FR_CHUNK + h.len > NPU_IN_BYTES || len != sizeof(h) + h.len)
    { nx_packet_release(rx); continue; }

    if (h.frame_id != cur_id) {                                  /* 새 프레임 시작 */
      cur_id = h.frame_id; have_n = 0; need_n = h.n; memset(fr_have, 0, sizeof(fr_have)); t_first = DWT->CYCCNT;
    }
    if (!fr_have[h.idx]) {
      nx_packet_data_extract_offset(rx, sizeof(h), fr_buf + (uint32_t)h.idx * FR_CHUNK, h.len, &bytes);
      fr_have[h.idx] = 1; have_n++;
    }
    nx_packet_release(rx);
    if (have_n < need_n) continue;

    /* --- 프레임 완성: 추론 → 융합 → 응답 --- */
    fr_verdict_t v; memset(&v, 0, sizeof(v)); v.frame_id = cur_id;
    int8_t q[NPU_OUT_N]; uint32_t us = 0;
    if (npu_ok && npu_infer_s8(fr_buf, q, &us) == 0) {
      float p[4];
      for (int i = 0; i < 4; i++) v.logits[i] = NPU_OUT_SCALE * ((float)q[i] - (float)NPU_OUT_ZP);
      fr_softmax4(v.logits, p);
      ip_infer_t inf; inf.seq = cur_id; for (int i = 0; i < 4; i++) inf.p[i] = p[i];
      inf.spec = NAN; inf.lane = NAN;                              /* 반사도·차선 미학습 → 제외 */
      uint8_t alarm = 0, level = 0;
      v.risk = ip_fuse(&inf, &alarm, &level); v.alarm = alarm; v.level = level;
      HAL_GPIO_WritePin(LED_RED_GPIO_Port,   LED_RED_Pin,   alarm ? GPIO_PIN_SET : GPIO_PIN_RESET);
      HAL_GPIO_WritePin(LED_GREEN_GPIO_Port, LED_GREEN_Pin, alarm ? GPIO_PIN_RESET : GPIO_PIN_SET);
    } else { v.risk = -1.0f; }                                      /* NPU 불가 신호 */
    v.infer_us = us;
    v.total_us = (uint32_t)(((uint64_t)(DWT->CYCCNT - t_first) * 1000000ULL) / (uint64_t)SystemCoreClock);
    cur_id = 0xFFFFFFFFu;
    if (nx_packet_allocate(&NxAppPool, &tx, NX_UDP_PACKET, TX_WAIT_FOREVER) == NX_SUCCESS) {
      if (nx_packet_data_append(tx, &v, sizeof(v), &NxAppPool, TX_WAIT_FOREVER) == NX_SUCCESS) {
        if (nx_udp_socket_send(&FrameSocket, tx, src_ip, src_port) != NX_SUCCESS) nx_packet_release(tx);
      } else nx_packet_release(tx);
    }
  }
}
'''
open(F, "w", encoding="utf-8", errors="surrogateescape").write(s)
print("app_netxduo.c: NPU frame path added")
