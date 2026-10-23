"""원호 차로 (곡률 일정) — 대리 모델에서 차선 오차·횡위치·진행 거리를 계산한다 (torch, env 마다 곡률이 다르다).

오른손 좌표(X 앞, Y 왼쪽, 반시계 양수). 곡률 κ > 0 이면 왼쪽으로 굽는다.
차로 기준점 Q0 (차량 시작 좌표계에서), 그 점의 차로 방향 ψ0, 곡률 κ 로 원호를 정한다.
  · 원 중심 C = Q0 + n0/κ  (n0 = ψ0 의 왼쪽 법선)
  · 차량 위치 P 의 횡위치(왼쪽 +) d = sign(κ)·(1/|κ| − |P−C|), 진행 거리 s = (1/|κ|)·Δθ, 차로 방향 ψ_L = θ(P−C) ± π/2
|κ| 가 아주 작으면 직선으로 계산한다.
CARLA Town04 시험 구간은 반경 약 120 m 곡선이었다 (closed_replay 가 확정 직전 3 s 의 차로 방향 변화로 복원한다).
"""
from __future__ import annotations

import math

import torch

_EPS = 1e-5


class ArcLane:
    def __init__(self, kappa, q0x, q0y, psi0):
        self.k = kappa; self.qx, self.qy, self.p0 = q0x, q0y, psi0
        self.straight = kappa.abs() < _EPS
        ks = torch.where(self.straight, torch.full_like(kappa, _EPS), kappa)
        self.R = 1.0 / ks.abs()
        nx, ny = -torch.sin(psi0), torch.cos(psi0)                   # 왼쪽 법선
        self.cx = q0x + nx / ks; self.cy = q0y + ny / ks
        self.th0 = torch.atan2(q0y - self.cy, q0x - self.cx)
        self.sg = torch.sign(ks)

    def frenet(self, X, Y):
        """→ (s 진행 거리, d 횡위치 왼쪽 +, ψ_L 차로 방향)."""
        dx, dy = X - self.cx, Y - self.cy
        r = torch.sqrt(dx * dx + dy * dy)
        th = torch.atan2(dy, dx)
        dth = torch.remainder(th - self.th0 + math.pi, 2 * math.pi) - math.pi
        s_arc = self.R * dth * self.sg
        d_arc = self.sg * (self.R - r)
        psiL_arc = th + self.sg * (math.pi / 2)
        # 직선
        c0, s0 = torch.cos(self.p0), torch.sin(self.p0)
        ex, ey = X - self.qx, Y - self.qy
        s_l = ex * c0 + ey * s0; d_l = -ex * s0 + ey * c0
        s = torch.where(self.straight, s_l, s_arc)
        d = torch.where(self.straight, d_l, d_arc)
        psiL = torch.where(self.straight, self.p0, psiL_arc)
        return s, d, psiL

    def point(self, s, d=0.0):
        """진행 거리 s, 횡위치 d 의 세계 좌표 (앞차·옆차 배치용)."""
        th = self.th0 + self.sg * s / self.R
        rr = self.R - self.sg * d
        xa = self.cx + rr * torch.cos(th); ya = self.cy + rr * torch.sin(th)
        c0, s0 = torch.cos(self.p0), torch.sin(self.p0)
        xl = self.qx + s * c0 - d * s0; yl = self.qy + s * s0 + d * c0
        return torch.where(self.straight, xl, xa), torch.where(self.straight, yl, ya)


def wrap(a):
    return torch.remainder(a + math.pi, 2 * math.pi) - math.pi
