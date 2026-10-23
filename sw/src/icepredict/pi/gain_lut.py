"""마찰별 제어표(slip_gain_lut.h) 의 파이썬 판 — 파라미터 이름·범위·고정값, 헤더 읽기, C 와 같은 쌍선형 보간.
torch 없이 돈다 (Pi·보드 도구용). 대리 모델용 torch 보간은 gs_controller.lut_torch() 가 이 표를 쓴다."""
from __future__ import annotations

import math
import pathlib
import re

PARAM_NAMES = ["Kp", "Kl", "Kd", "steer_max", "B_HI", "B_LO", "PULSE_HALF", "B_EVADE", "a_stop_scale", "margin"]
FIXED = {"Kp": 1.4, "Kl": 0.15, "Kd": 0.30, "steer_max": 0.5, "B_HI": 0.7, "B_LO": 0.35, "PULSE_HALF": 8.0,
         "B_EVADE": 0.25, "a_stop_scale": float("nan"), "margin": 4.0}
BOUNDS = {"Kp": (0.3, 3.0), "Kl": (0.0, 0.5), "Kd": (0.0, 0.8), "steer_max": (0.15, 0.8), "B_HI": (0.02, 1.0),
          "B_LO": (0.0, 0.6), "PULSE_HALF": (2.0, 16.0), "B_EVADE": (0.0, 0.6), "a_stop_scale": (0.3, 1.2), "margin": (2.0, 12.0)}


class GainLUT:
    def __init__(self, mu, kph, table):
        self.mu, self.kph, self.table = list(mu), list(kph), table            # table[i][j][k]
        self.logmu = [math.log(m) for m in self.mu]

    @classmethod
    def from_header(cls, path):
        s = pathlib.Path(path).read_text()
        arr = lambda name: [float(x.rstrip("f")) for x in re.search(name + r"\[[^\]]*\]\s*=\s*\{([^}]*)\}", s).group(1).split(",")]
        mu, kph = arr("SLIP_GS_MU"), arr("SLIP_GS_KPH")
        body = s[s.index("SLIP_GS_TABLE"):]
        rows = [[float(x.rstrip("f")) for x in m.split(",")] for m in re.findall(r"\{\s*(-?[0-9][^{}]*?)\}", body)]
        nv = len(kph)
        table = [[rows[i * nv + j] for j in range(nv)] for i in range(len(mu))]
        return cls(mu, kph, table)

    @staticmethod
    def _find(axis, x):
        if x <= axis[0]: return 0, 0.0
        if x >= axis[-1]: return len(axis) - 2, 1.0
        k = sum(1 for a in axis[1:-1] if x >= a)
        return k, (x - axis[k]) / (axis[k + 1] - axis[k])

    def lookup(self, mu_hat, speed_mps):
        """C slip_gain_lookup 과 같은 계산 (μ 는 로그 축)."""
        im, tm = self._find(self.logmu, math.log(max(mu_hat, self.mu[0])))
        iv, tv = self._find(self.kph, speed_mps * 3.6)
        T = self.table; g = []
        for k in range(len(PARAM_NAMES)):
            a, b = T[im][iv][k], T[im][iv + 1][k]; c, e = T[im + 1][iv][k], T[im + 1][iv + 1][k]
            lo = a + (b - a) * tv; hi = c + (e - c) * tv
            g.append(lo + (hi - lo) * tm)
        return dict(zip(PARAM_NAMES, g))
