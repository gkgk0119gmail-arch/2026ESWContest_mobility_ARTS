#include "npu_infer.h"
#include <string.h>
#include "stm32n6xx_hal.h"
#include "stm32n6570_discovery_xspi.h"
#include "ll_aton_runtime.h"
#include "ll_aton_util.h"
#include "npu_cache.h"

LL_ATON_DECLARE_NAMED_NN_INSTANCE_AND_INTERFACE(network);

static int8_t  *s_in  = NULL;
static int8_t  *s_out = NULL;
static uint32_t s_in_bytes = 0, s_out_bytes = 0;
static int      s_ready = 0;

/* hello_world/misc_toolbox.c 에서 가져옴: RISAF 영역을 기본(전체 접근 허용)으로 */
static void set_risaf_default(RISAF_TypeDef *risaf)
{
  RISAF_BaseRegionConfig_t cfg = {0};
  cfg.Filtering = RISAF_FILTER_ENABLE;
  cfg.PrivWhitelist = RIF_CID_MASK;
  cfg.ReadWhitelist = RIF_CID_MASK;
  cfg.WriteWhitelist = RIF_CID_MASK;
  cfg.Secure = RIF_ATTRIBUTE_SEC;
  cfg.StartAddress = 0x0;
  cfg.EndAddress = 0xFFFFFFFF;
  HAL_RIF_RISAF_ConfigBaseRegion(risaf, RISAF_REGION_1, &cfg);
}

static int external_flash_init(void)
{
  /* 우리 FSBL 시스템 초기화가 XSPI2를 리셋하므로 가중치(0x71000000)를 읽으려면 메모리맵을 다시 켠다 */
  BSP_XSPI_NOR_Init_t f;
  f.InterfaceMode = MX66UW1G45G_OPI_MODE;
  f.TransferRate  = MX66UW1G45G_DTR_TRANSFER;
  if (BSP_XSPI_NOR_Init(0, &f) != BSP_ERROR_NONE) return -1;
  if (BSP_XSPI_NOR_EnableMemoryMappedMode(0) != BSP_ERROR_NONE) return -2;
  return 0;
}

static void npu_hw_config(void)
{
  __HAL_RCC_NPU_CLK_ENABLE();
  __HAL_RCC_NPU_FORCE_RESET();
  __HAL_RCC_NPU_RELEASE_RESET();
  npu_cache_enable();
  RIMC_MasterConfig_t mc = {0};
  mc.MasterCID = RIF_CID_1;
  mc.SecPriv   = RIF_ATTRIBUTE_SEC | RIF_ATTRIBUTE_PRIV;
  HAL_RIF_RIMC_ConfigMasterAttributes(RIF_MASTER_INDEX_NPU, &mc);
  HAL_RIF_RISC_SetSlaveSecureAttributes(RIF_RISC_PERIPH_INDEX_NPU, RIF_ATTRIBUTE_PRIV | RIF_ATTRIBUTE_SEC);
  /* NPU 마스터가 SRAM/플래시에 접근할 수 있게 */
  set_risaf_default(RISAF2_S);  /* SRAM1_AXI */
  set_risaf_default(RISAF3_S);  /* SRAM2_AXI */
  set_risaf_default(RISAF4_S);  /* NPU MST0 */
  set_risaf_default(RISAF5_S);  /* NPU MST1 */
  set_risaf_default(RISAF6_S);  /* SRAM3..6 (npuRAM) */
  set_risaf_default(RISAF7_S);  /* FLEXMEM */
  set_risaf_default(RISAF8_S);  /* NPU cache */
  set_risaf_default(RISAF15_S); /* NPU cache cfg */
  set_risaf_default(RISAF12_S); /* OCTOSPI2 0x70000000 (가중치) */
}

int npu_init(void)
{
  int r = external_flash_init();
  if (r) return r;
  npu_hw_config();
  LL_ATON_RT_RuntimeInit();
  LL_ATON_RT_Init_Network(&NN_Instance_network);
  const LL_Buffer_InfoTypeDef *ib = LL_ATON_Input_Buffers_Info(&NN_Instance_network);
  const LL_Buffer_InfoTypeDef *ob = LL_ATON_Output_Buffers_Info(&NN_Instance_network);
  if (!ib || !ob) return -3;
  s_in  = (int8_t *)LL_Buffer_addr_start(&ib[0]);
  s_out = (int8_t *)LL_Buffer_addr_start(&ob[0]);
  s_in_bytes  = LL_Buffer_len(&ib[0]);
  s_out_bytes = LL_Buffer_len(&ob[0]);
  if (s_in_bytes != NPU_IN_BYTES || s_out_bytes != NPU_OUT_N) return -4;   /* 생성물과 헤더 상수 불일치 */
  s_ready = 1;
  return 0;
}

const int8_t *npu_input_buffer(void) { return s_in; }

int npu_infer_s8(const int8_t *in, int8_t out[NPU_OUT_N], uint32_t *infer_us)
{
  if (!s_ready) return -1;
  if (in != s_in) memcpy(s_in, in, NPU_IN_BYTES);
  SCB_CleanDCache_by_Addr((uint32_t *)((uintptr_t)s_in & ~31u), (int32_t)(NPU_IN_BYTES + 64));   /* NPU가 메모리에서 읽는다 */
  uint32_t t0 = DWT->CYCCNT;
  LL_ATON_RT_Reset_Network(&NN_Instance_network);
  LL_ATON_RT_RetValues_t st;
  do {
    st = LL_ATON_RT_RunEpochBlock(&NN_Instance_network);
    if (st == LL_ATON_RT_WFE) LL_ATON_OSAL_WFE();
  } while (st != LL_ATON_RT_DONE);
  uint32_t t1 = DWT->CYCCNT;
  SCB_InvalidateDCache_by_Addr((uint32_t *)((uintptr_t)s_out & ~31u), 64);                         /* NPU가 쓴 출력을 읽기 전에 */
  memcpy(out, s_out, NPU_OUT_N);
  if (infer_us) *infer_us = (uint32_t)(((uint64_t)(t1 - t0) * 1000000ULL) / SystemCoreClock);
  return 0;
}
