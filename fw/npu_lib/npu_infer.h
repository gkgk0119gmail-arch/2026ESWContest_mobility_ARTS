/* IcePredict — STM32N6 Neural-ART NPU 추론 글루.
 * app_netxduo.c 등 펌웨어 쪽은 이 헤더만 본다 (ll_aton 헤더 불필요).
 * 네트워크: roadnet eq3 (RSCD 0.8800, 실보드 21.7 ms), stedgeai generate 프로파일 icepredict-fsbl3
 * (활성 npuRAM3~5 0x34200000~, 가중치 외부 플래시 0x71000000). */
#ifndef NPU_INFER_H
#define NPU_INFER_H
#include <stdint.h>
#define NPU_IN_BYTES   150528u   /* int8 [1,3,224,224] NCHW */
#define NPU_OUT_N      4u        /* int8 logits: normal, wet, black_ice, pothole */
#define NPU_IN_SCALE   0.018658448f
#define NPU_IN_ZP      (-14)
#define NPU_OUT_SCALE  0.0294518489f
#define NPU_OUT_ZP     (-23)
int      npu_init(void);                                            /* 0 = OK. XSPI NOR 메모리맵, NPU 클럭/RIF/RISAF, LL_ATON 초기화 */
int      npu_infer_s8(const int8_t *in, int8_t out[NPU_OUT_N], uint32_t *infer_us);  /* 0 = OK */
const int8_t *npu_input_buffer(void);                               /* 네트워크 입력 버퍼 주소 (직접 채워도 됨) */
#endif
