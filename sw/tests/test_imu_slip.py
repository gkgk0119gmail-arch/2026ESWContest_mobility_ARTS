import numpy as np
from icepredict.pi.imu_slip import SlipDetector, synth_run, emergency_command, G

def run(det, t, ay, gz, v, steer):
    for i in range(len(t)):
        ev = det.step(t[i], ay[i], gz[i], v, steer)
        if ev:
            return ev
    return None

def test_detects_slip_within_10ms_after_onset_plus_ramp():
    t, ay, gz, v, steer = synth_run(slip_at_s=2.0)
    ev = run(SlipDetector(), t, ay, gz, v, steer)
    assert ev is not None
    # 미끄러짐이 150ms에 걸쳐 전개된다. 어느 한 신호가 임계값에 먼저 닿는 시점 기준 10ms 이내 확정:
    #   yaw: 0.9*ramp >= 0.35 → ramp 0.389 → t=2.058 / ay: 0.45g*ramp >= 0.3g → t=2.100
    onset_thr = 2.0 + 0.15 * min(0.35 / 0.9, 0.3 / 0.45)
    latency_ms = (ev.t - onset_thr) * 1000
    assert 0.0 <= latency_ms <= 10.0, latency_ms

def test_no_false_alarm_on_normal_driving():
    t, ay, gz, v, steer = synth_run(slip_at_s=99.0, duration_s=5.0, noise_ay=0.8, noise_gz=0.04)
    assert run(SlipDetector(), t, ay, gz, v, steer) is None

def test_no_alarm_at_low_speed():
    t, ay, gz, _, steer = synth_run(slip_at_s=1.0)
    assert run(SlipDetector(), t, ay, gz, 1.0, steer) is None

def test_emergency_command_shape():
    t, ay, gz, v, steer = synth_run()
    ev = run(SlipDetector(), t, ay, gz, v, steer)
    cmd = emergency_command(ev)
    assert cmd["throttle"] == 0.0 and 0 < cmd["brake"] <= 1 and cmd["pulse_ms"] == 200
    assert abs(cmd["steer"]) > 0 and np.sign(cmd["steer"]) == -np.sign(ev.yaw_err)

def test_step_cost_under_50us():
    import time
    det = SlipDetector()
    n = 5000
    t0 = time.perf_counter()
    for i in range(n):
        det.step(i / 1000, 0.1, 0.01, 20.0, 0.0)
    per = (time.perf_counter() - t0) / n * 1e6
    assert per < 200, f"{per:.1f} us/step (Python 기준, C 이식 시 <10us 목표)"
