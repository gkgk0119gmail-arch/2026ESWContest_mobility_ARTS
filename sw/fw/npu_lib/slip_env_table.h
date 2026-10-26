/* 자동 생성: sw/fw/npu_lib/build_env_table.py — 손으로 고치지 말 것.
   근거: sw/docs/data/gain_sched/mu_table.json (CARLA 0.9.16 Tesla Model 3, 평탄 직선, friction 0.02, 0.04, 0.06, 0.08, 0.12, 0.2, 0.3, 0.5, 1) */
#ifndef SLIP_ENV_TABLE_H
#define SLIP_ENV_TABLE_H
#define ENV_NMU 9
static const float env_mu[ENV_NMU]        = { 0.0178f, 0.0340f, 0.0502f, 0.0665f, 0.0990f, 0.1640f, 0.2452f, 0.4076f, 0.6725f };
static const float env_dec_full[ENV_NMU]  = { 0.1715f, 0.3288f, 0.4865f, 0.6446f, 0.9631f, 1.6017f, 2.4001f, 3.9746f, 5.4124f };   /* 제동 1.0, m/s² */
static const float env_dec_probe[ENV_NMU] = { 0.1715f, 0.3288f, 0.4865f, 0.6446f, 0.9631f, 1.5666f, 2.0604f, 2.5867f, 2.8167f };  /* 제동 0.3, m/s² */
#endif
