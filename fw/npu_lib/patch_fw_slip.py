#!/usr/bin/env python3
"""icepredict_n6 펌웨어(app_netxduo.c)에 2차 방어 — IMU 미끄러짐 감지 — 를 추가한다.
UDP 5557 융합 스레드가 68B 패킷(ip_imu_t)을 받으면 slip_core.h 로 판정하고 28B(ip_slip_t)로 응답한다.
길이로 구분: 24B ctx / 28B infer / 68B imu.  멱등: 이미 패치돼 있으면 건너뛴다.
사용: patch_fw_slip.py <app_netxduo.c>   (slip_core.h 는 이 스크립트 옆에서 읽어 인라인으로 넣는다)"""
import sys, pathlib
F = sys.argv[1]
s = open(F, encoding="utf-8", errors="surrogateescape").read()
if "IP_IMU_MAGIC" in s:
    print("app_netxduo.c: slip 경로 이미 있음"); sys.exit(0)
core = (pathlib.Path(__file__).parent / "slip_core.h").read_text()

# 1) 정의 + 코어 (NPU 블록 뒤, USER CODE END PD 앞)
anchor = "/* USER CODE END PD */"
block = r'''
/* ===================== IcePredict 2차 방어: IMU 미끄러짐 감지 (UDP 5557, 32B 패킷) =====================
   Pi가 매 샘플 (ay, gz, speed, steer, dt, min_speed)를 보내면 보드가 칼만 필터 + 자전거 모델 잔차로
   미끄러짐을 판정하고 비상 명령(브레이크·카운터스티어)까지 돌려준다. 1차(카메라)가 놓쳤을 때의 방어선이며
   판정 주체는 이 보드(ThreadX 스레드)다. magic 'IMU0' = 상태 초기화(새 주행), 'IMU1' = 샘플. */
''' + core + r'''
typedef struct __attribute__((packed)) {
  uint32_t seq; float ay; float gz; float speed; float steer; float dt; float min_speed;
  float lane_err; float lat_off; float gap_front; float gap_left; float gap_right; float ax; float brake_cmd;
  float ice_lane_off; float front_rel_v; uint32_t magic;
} ip_imu_t;                                   /* 68 bytes (v7: 미끄러진 차로 위치, 앞차 상대속도) */
typedef struct __attribute__((packed)) {
  uint32_t seq; uint8_t slip; uint8_t trigger; uint8_t emerg; uint8_t mode; float ay_g; float yaw_err; uint32_t latency_ns; float brake; float steer;
} ip_slip_t;                                  /* 28 bytes: emerg=1 이면 brake/steer 는 이 샘플의 제어 명령, mode = 제어 모드 */
#ifndef IP_SLIP_VERBOSE
#define IP_SLIP_VERBOSE 0                     /* 1이면 미끄러짐/모드 변화 때 printf — UART 출력이 그 샘플 응답을 6~12ms 늘려 WCET를 오염시킨다 */
#endif
#define IP_IMU_MAGIC  0x31554D49u             /* 'IMU1' (LE) */
#define IP_IMU_RESET  0x30554D49u             /* 'IMU0' */
static slipdet_t g_slip;
'''
assert anchor in s; s = s.replace(anchor, block + "\n" + anchor, 1)

# 2) 융합 스레드 루프에 IMU 분기 (infer 분기 앞)
anchor = "    if (len == sizeof(ip_infer_t) &&"
assert s.count(anchor) == 1
branch = r'''    if (len == sizeof(ip_imu_t))
    {
      ip_imu_t im;
      if (nx_packet_data_extract_offset(rx_packet, 0, &im, sizeof(im), &bytes_copied) == NX_SUCCESS &&
          (im.magic == IP_IMU_MAGIC || im.magic == IP_IMU_RESET))
      {
        ip_slip_t sv; memset(&sv, 0, sizeof(sv)); sv.seq = im.seq;
        if (im.magic == IP_IMU_RESET || !g_slip.ready || g_slip.dt != im.dt)
        {
          slip_reset(&g_slip, im.dt, 3, im.min_speed);
          if (IP_SLIP_VERBOSE && im.magic == IP_IMU_RESET)
            printf("IP: slip detector reset dt=%lu us min_speed=%lu/100 m/s\n",
                   (unsigned long)(im.dt * 1e6f), (unsigned long)(im.min_speed * 100.f));
        }
        if (im.magic == IP_IMU_MAGIC)
        {
          uint8_t trig = 0u; float ay_g = 0.f, ye = 0.f, brk = 0.f, st = 0.f;
          sv.slip = (uint8_t)slip_step2(&g_slip, im.ay, im.gz, im.speed, im.steer, im.ax, im.brake_cmd, &trig, &ay_g, &ye);
          sv.trigger = trig; sv.ay_g = ay_g; sv.yaw_err = ye;
          if (sv.slip) g_slip.emerg = 1;                    /* 비상 모드 진입: 이후 정지까지 보드가 매 샘플 제어 */
          if (g_slip.emerg)
          {
            uint8_t prev = g_slip.mode;
            slip_control2(&g_slip, im.speed, im.gz, im.lane_err, im.lat_off, im.gap_front, im.gap_left, im.gap_right,
                          g_slip.kax.x0, im.brake_cmd, im.ice_lane_off, im.front_rel_v, &brk, &st);
            sv.emerg = 1u; sv.brake = brk; sv.steer = st; sv.mode = g_slip.mode;
            if (IP_SLIP_VERBOSE && g_slip.mode != prev)
              printf("IP: emergency mode %u (front %ld m, left %ld m, right %ld m, v %lu/10 m/s)\n", g_slip.mode,
                     (long)im.gap_front, (long)im.gap_left, (long)im.gap_right, (unsigned long)(im.speed * 10.f));
          }
          if (sv.slip)
          {
            if (IP_SLIP_VERBOSE) printf("IP: SLIP (%s) ay=%ld/1000 g yaw_err=%ld/1000 -> emergency control\n",
                   trig == SLIP_TRIG_LAT ? "lat_acc" : (trig == SLIP_TRIG_YAW ? "yaw_rate" : "low_mu"),
                   (long)(ay_g * 1000.f), (long)(ye * 1000.f));
            HAL_GPIO_WritePin(LED_RED_GPIO_Port,   LED_RED_Pin,   GPIO_PIN_SET);
            HAL_GPIO_WritePin(LED_GREEN_GPIO_Port, LED_GREEN_Pin, GPIO_PIN_RESET);
          }
        }
        sv.latency_ns = (uint32_t)(((uint64_t)(DWT->CYCCNT - t0) * 1000000000ULL) / (uint64_t)SystemCoreClock);
        if (nx_packet_allocate(&NxAppPool, &tx_packet, NX_UDP_PACKET, TX_WAIT_FOREVER) == NX_SUCCESS)
        {
          if (nx_packet_data_append(tx_packet, &sv, sizeof(sv), &NxAppPool, TX_WAIT_FOREVER) == NX_SUCCESS)
          { if (nx_udp_socket_send(&IcePredictSocket, tx_packet, src_ip, src_port) != NX_SUCCESS) nx_packet_release(tx_packet); }
          else nx_packet_release(tx_packet);
        }
      }
      nx_packet_release(rx_packet);
      continue;
    }

'''
s = s.replace(anchor, branch + anchor, 1)
s = s.replace("     28 bytes -> ip_infer_t  : 추론 확률 -> 융합 -> 판정 응답 (ip_verdict_t)",
              "     28 bytes -> ip_infer_t  : 추론 확률 -> 융합 -> 판정 응답 (ip_verdict_t)\n"
              "     68 bytes -> ip_imu_t    : IMU 샘플(+차선 오차·주변 차 간격) -> 미끄러짐 감지(2차 방어) -> 비상 제어 명령 응답 (ip_slip_t)", 1)
open(F, "w", encoding="utf-8", errors="surrogateescape").write(s)
print("app_netxduo.c: IMU slip (2차 방어) 경로 추가")
