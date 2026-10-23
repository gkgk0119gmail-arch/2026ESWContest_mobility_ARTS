"""대리 모델용 torch 제어기(gs_controller) = 보드 C 코어(ctypes, SLIP_MU_EST=1) — 샘플 단위 비교.
torch 가 없는 환경(Pi)에서는 건너뛴다. 5090 에서 돈다."""
import pathlib, sys
import numpy as np
import pytest

torch = pytest.importorskip("torch")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "fw" / "npu_lib"))
from icepredict.pi.slip_ctypes import SlipCoreC
from icepredict.sim.gs_controller import SlipControllerV


def scenario(kind, seed, n=600, dt=0.02):
    rng = np.random.default_rng(seed); t = np.arange(n) * dt
    v = np.clip(11.0 - np.maximum(t - 4.0, 0) * 1.2, 0.0, None); steer = 0.02 * np.sin(t * 0.7)
    ay = rng.normal(0, 0.3, n); gz = rng.normal(0, 0.015, n); s = t >= 2.0
    ay[s] += -3.5 * np.clip((t[s] - 2) / 0.2, 0, 1); gz[s] += -0.4 * np.clip((t[s] - 2) / 0.2, 0, 1)
    ax = np.where(t > 2.1, -0.6, -0.05) + rng.normal(0, 0.1, n); brake = np.where(t > 2.1, 0.6, 0.0)
    gf = np.full(n, 999.0); gl = np.full(n, 999.0); gr = np.full(n, 999.0); ice = np.zeros(n); frv = np.zeros(n)
    le = 0.05 * np.sin(t); lo = 0.3 * np.cos(t * 0.5)
    if kind == "grip": ax[t > 3] = -6 + rng.normal(0, .2, (t > 3).sum()); gf[t > 3.5] = 8
    if kind == "evade": gf[:] = np.linspace(40, 5, n); gl[:] = 5
    if kind == "ice_lane": gf[:] = 10; ice[:] = 3.5
    if kind == "overlap": gf[t > 2.3] = -1.5
    return np.stack([ay, gz, v, steer, ax, brake, le, lo, gf, gl, gr, ice, frv], 1)


@pytest.mark.parametrize("kind", ["plain", "grip", "evade", "ice_lane", "overlap"])
def test_torch_equals_c(kind):
    X = scenario(kind, 1)
    c = SlipCoreC(0.02, 3, 2.0, variant="mu")
    v = SlipControllerV(1, 0.02, 2.0, gains=None, mu_est=True, dtype=torch.float64)
    fb = None
    for i, row in enumerate(X):
        row = row.copy()
        if fb is not None: row[5] = fb
        a = c.board(*row.tolist())
        T = [torch.tensor([x], dtype=torch.float64) for x in row]
        fire, act, b, st = v.board(*T, step_idx=i)
        assert int(fire[0]) == a["slip"], i
        assert int(act[0]) == a["emerg"], i
        assert int(v.mode[0]) == a["mode"], (i, int(v.mode[0]), a["mode"])
        assert abs(float(b[0]) - a["brake"]) < 1e-5 and abs(float(st[0]) - a["steer"]) < 1e-4, i
        assert abs(float(v.mu_hat[0]) - a["mu_hat"]) < 1e-4 and int(v.mu_n[0]) == a["mu_n"], i
        fb = a["brake"] if a["emerg"] else None


@pytest.mark.parametrize("kind", ["plain", "grip", "evade", "ice_lane"])
def test_torch_gains_equals_c_override(kind):
    """제어표 경로(후보 파라미터 덮어쓰기): torch = C (SLIP_GAIN_SCHED=1)."""
    from icepredict.pi.gain_lut import BOUNDS, PARAM_NAMES
    rng = np.random.default_rng(11)
    g = {k: float(BOUNDS[k][0] + (BOUNDS[k][1] - BOUNDS[k][0]) * rng.random()) for k in PARAM_NAMES}
    X = scenario(kind, 2)
    c = SlipCoreC(0.02, 3, 2.0, variant="gs"); c.set_gains(g)
    v = SlipControllerV(1, 0.02, 2.0, gains={k: torch.tensor([g[k]], dtype=torch.float64) for k in PARAM_NAMES}, mu_est=True, dtype=torch.float64)
    fb = None
    for i, row in enumerate(X):
        row = row.copy()
        if fb is not None: row[5] = fb
        a = c.board(*row.tolist())
        fire, act, b, st = v.board(*[torch.tensor([x], dtype=torch.float64) for x in row], step_idx=i)
        assert int(v.mode[0]) == a["mode"], i
        assert abs(float(b[0]) - a["brake"]) < 1e-5 and abs(float(st[0]) - a["steer"]) < 1e-4, (i, float(b[0]), a["brake"])
        fb = a["brake"] if a["emerg"] else None


def test_torch_lut_equals_c_table():
    """제어표 경로(표 보간): torch lut_torch = C (저장소 slip_gain_lut.h)."""
    from icepredict.pi.gain_lut import GainLUT
    from icepredict.sim.gs_controller import lut_torch
    import pathlib as _p
    lut = GainLUT.from_header(_p.Path(__file__).resolve().parents[1] / "fw" / "npu_lib" / "slip_gain_lut.h")
    X = scenario("grip", 4)
    c = SlipCoreC(0.02, 3, 2.0, variant="gs")
    v = SlipControllerV(1, 0.02, 2.0, gains={}, mu_est=True, dtype=torch.float64, lut_fn=lut_torch(lut, dtype=torch.float64))
    fb = None
    for i, row in enumerate(X):
        row = row.copy()
        if fb is not None: row[5] = fb
        a = c.board(*row.tolist())
        fire, act, b, st = v.board(*[torch.tensor([x], dtype=torch.float64) for x in row], step_idx=i)
        assert int(v.mode[0]) == a["mode"], i
        assert abs(float(b[0]) - a["brake"]) < 1e-5 and abs(float(st[0]) - a["steer"]) < 1e-4, i
        fb = a["brake"] if a["emerg"] else None
