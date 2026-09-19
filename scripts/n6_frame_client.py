#!/usr/bin/env python3
"""Pi → STM32N6 **NPU 추론** 클라이언트: 이미지를 모델 입력 형식(int8)으로 양자화해 UDP 5559로 보내고
보드의 로짓·위험도를 받는다. 같은 이미지를 ORT(int8 QDQ ONNX)로 돌린 로짓과 대조한다.

형식 (펌웨어 app_netxduo.c와 일치해야 함)
  청크 : <IHHHH  frame_id, idx, n, len, flags  + payload(<=1400B)      입력 = int8 NCHW 150528B
  응답 : <I4ffBBHII frame_id, logits[4], risk, alarm, level, pad, infer_us, total_us   = 36B
  입력 양자화: q = clip(round(x_norm / 0.018658448) + (-14), -128, 127)   (x_norm = ImageNet 정규화)
  출력 역양자화(보드가 함): logit = 0.0294518489 * (q - (-23))
"""
import argparse, socket, struct, sys, time
from pathlib import Path
import numpy as np, cv2
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
IN_SCALE, IN_ZP = 0.018658448, -14
MEAN = np.array([0.485, 0.456, 0.406], np.float32).reshape(3, 1, 1)
STD = np.array([0.229, 0.224, 0.225], np.float32).reshape(3, 1, 1)
HDR = "<IHHHH"; VD = "<I4fffBBHII"; CHUNK = 1400   # 응답에 spec(반사도) 추가 = 40B
CLASSES = ("normal", "wet", "black_ice", "pothole")

def preprocess(img_bgr):
    im = cv2.resize(img_bgr, (224, 224), interpolation=cv2.INTER_AREA)
    x = (im[:, :, ::-1].astype(np.float32).transpose(2, 0, 1) / 255.0 - MEAN) / STD
    return x  # [3,224,224] float

def quantize(x):
    return np.clip(np.round(x / IN_SCALE) + IN_ZP, -128, 127).astype(np.int8)

def send_frame(sock, addr, q_nchw, frame_id, timeout=3.0, gap_us=0):
    """청크를 gap_us 간격으로 보낸다. 0으로 몰아 보내면 보드의 NetX 패킷 풀이 넘쳐 일부가 버려지고
    프레임이 완성되지 않는다 (108개 × 1.4KB 버스트 vs 풀 수십 개)."""
    data = q_nchw.tobytes(); n = (len(data) + CHUNK - 1) // CHUNK
    t0 = time.perf_counter()
    for i in range(n):
        pl = data[i * CHUNK:(i + 1) * CHUNK]
        sock.sendto(struct.pack(HDR, frame_id, i, n, len(pl), 0) + pl, addr)
        if gap_us:
            t_next = time.perf_counter() + gap_us / 1e6
            while time.perf_counter() < t_next: pass
    sock.settimeout(timeout)
    d, _ = sock.recvfrom(128)
    rtt = (time.perf_counter() - t0) * 1e3
    fid, l0, l1, l2, l3, spec, risk, alarm, level, _pad, infer_us, total_us = struct.unpack(VD, d)
    return dict(frame_id=fid, logits=np.array([l0, l1, l2, l3]), spec=spec, risk=risk, alarm=bool(alarm),
                level=level, infer_us=infer_us, total_us=total_us, rtt_ms=rtt)

def softmax(z): e = np.exp(z - z.max()); return e / e.sum()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default="192.168.50.158"); ap.add_argument("--port", type=int, default=5559)
    ap.add_argument("--image", help="테스트 이미지 (없으면 회색 상수 이미지)")
    ap.add_argument("--onnx", default="", help="대조용 int8 QDQ ONNX (데스크탑에만 있으면 생략)")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--gap-us", type=int, default=300, help="청크 간격 (0=버스트)")
    ap.add_argument("--bench", type=int, default=0, help="N프레임 연속 전송 처리율 측정")
    a = ap.parse_args()
    if a.image:
        img = cv2.imread(a.image); assert img is not None, a.image
    else:
        img = np.full((480, 640, 3), 128, np.uint8)
    x = preprocess(img); q = quantize(x)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for k in range(a.repeat):
        r = send_frame(s, (a.ip, a.port), q, frame_id=1000 + k, gap_us=a.gap_us)
        p = softmax(r["logits"])
        pj = " ".join(f"{c[:5]}={v:.3f}" for c, v in zip(CLASSES, p))
        print(f"[{k}] 보드 logits={np.round(r['logits'],3).tolist()} {pj} | risk={r['risk']:.3f} alarm={r['alarm']} "
              f"lvl={r['level']} spec={r['spec']:.3f} | NPU {r['infer_us']/1000:.1f}ms 보드총 {r['total_us']/1000:.1f}ms RTT {r['rtt_ms']:.1f}ms")
    if a.bench:
        t0 = time.perf_counter(); ok = 0; npu = []
        for k in range(a.bench):
            try:
                r = send_frame(s, (a.ip, a.port), q, frame_id=5000 + k, gap_us=a.gap_us); ok += 1; npu.append(r["infer_us"])
            except socket.timeout: pass
        dt = time.perf_counter() - t0
        print(f"연속 {a.bench}프레임: 성공 {ok}, {ok/dt:.1f} fps (프레임당 {dt/max(ok,1)*1000:.1f}ms, NPU 평균 {np.mean(npu)/1000:.1f}ms)")
    if a.onnx:
        import onnxruntime as ort
        so = ort.SessionOptions(); so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        sess = ort.InferenceSession(a.onnx, so, providers=["CPUExecutionProvider"])
        lo = sess.run(None, {sess.get_inputs()[0].name: x[None]})[0][0]
        print(f"ORT  logits={np.round(lo,3).tolist()} " + " ".join(f"{c[:5]}={v:.3f}" for c, v in zip(CLASSES, softmax(lo))))
        print(f"차이 : 최대 |Δlogit| {np.abs(lo - r['logits']).max():.3f}, argmax {'일치' if lo.argmax()==r['logits'].argmax() else '불일치'}")

if __name__ == "__main__":
    main()
