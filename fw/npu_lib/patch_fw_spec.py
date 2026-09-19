#!/usr/bin/env python3
"""펌웨어: 모델 출력이 5개(4클래스 + 반사도 로짓)일 때 반사도를 융합에 넘긴다.
NPU_OUT_N=5 로 빌드하면 활성화되고, 4면 기존 동작(반사도 제외) 그대로다."""
import sys
F = sys.argv[1]
s = open(F, encoding="utf-8", errors="surrogateescape").read()

old = '''      float p[4];
      for (int i = 0; i < 4; i++) v.logits[i] = NPU_OUT_SCALE * ((float)q[i] - (float)NPU_OUT_ZP);
      fr_softmax4(v.logits, p);
      ip_infer_t inf; inf.seq = cur_id; for (int i = 0; i < 4; i++) inf.p[i] = p[i];
      inf.spec = NAN; inf.lane = NAN;                              /* 반사도·차선 미학습 → 제외 */'''
new = '''      float p[4];
      for (int i = 0; i < 4; i++) v.logits[i] = NPU_OUT_SCALE * ((float)q[i] - (float)NPU_OUT_ZP);
      fr_softmax4(v.logits, p);
      ip_infer_t inf; inf.seq = cur_id; for (int i = 0; i < 4; i++) inf.p[i] = p[i];
#if NPU_OUT_N >= 5
      /* 5번째 출력 = 반사도 로짓 (시그모이드는 모델 밖). 융합 beta(0.45)가 이 신호를 쓴다. */
      float sl = NPU_OUT_SCALE * ((float)q[4] - (float)NPU_OUT_ZP);
      v.spec = 1.0f / (1.0f + expf(-sl));
      inf.spec = v.spec;
#else
      v.spec = -1.0f;                                              /* 반사도 헤드 없음 */
      inf.spec = NAN;
#endif
      inf.lane = NAN;                                              /* 차선 헤드 미학습 → 제외 */'''
assert old in s, "융합 호출부 패턴 불일치"
s = s.replace(old, new, 1)

old = '''typedef struct __attribute__((packed)) {
  uint32_t frame_id;
  float    logits[4];     /* normal, wet, black_ice, pothole (역양자화) */
  float    risk;'''
new = '''typedef struct __attribute__((packed)) {
  uint32_t frame_id;
  float    logits[4];     /* normal, wet, black_ice, pothole (역양자화) */
  float    spec;          /* 반사도 0~1 (헤드 없으면 -1) */
  float    risk;'''
assert old in s, "응답 구조체 패턴 불일치"
s = s.replace(old, new, 1)
s = s.replace('    } else { v.risk = -1.0f; }', '    } else { v.risk = -1.0f; v.spec = -1.0f; }', 1)
open(F, "w", encoding="utf-8", errors="surrogateescape").write(s)
print("app_netxduo.c: 반사도 융합 경로 (NPU_OUT_N>=5에서 활성)")
