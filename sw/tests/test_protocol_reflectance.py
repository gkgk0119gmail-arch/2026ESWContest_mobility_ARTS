import numpy as np, cv2
from icepredict.common.protocol import encode, decode, TOPIC_CTX, ContextMsg, Weights, age_ms
from icepredict.pi.reflectance import analyze

def test_protocol_roundtrip():
    msg = ContextMsg(risk_prior=0.9, threshold=0.42, weights=Weights(0.4, 0.4, 0.2), reason="test")
    parts = encode(TOPIC_CTX, msg, payload=b"\x00\x01")
    topic, body, payload = decode(parts)
    assert topic == TOPIC_CTX and body["threshold"] == 0.42 and body["weights"]["beta"] == 0.4
    assert payload == b"\x00\x01" and "ts" in body and "seq" in body
    assert 0 <= age_ms(body) < 1000

def _road(h=240, w=360, base=70, lanes=True):
    img = np.full((h, w, 3), base, np.uint8)
    img[: h // 2] = (180, 160, 120)                       # 하늘
    if lanes:
        cv2.line(img, (w // 2 - 20, h // 2), (20, h - 1), (230, 230, 230), 3)
        cv2.line(img, (w // 2 + 20, h // 2), (w - 20, h - 1), (230, 230, 230), 3)
    return img

def test_dry_asphalt_low_spec_high_lane():
    r = analyze(_road())
    assert r.spec < 0.3 and r.lane > 0.4, r

def test_ice_patch_high_spec_low_lane():
    img = _road(lanes=False)
    h, w = img.shape[:2]
    # 헤드라이트 전반사: 도로 중앙에 밝은 타원
    cv2.ellipse(img, (w // 2, int(h * 0.8)), (110, 28), 0, 0, 360, (245, 245, 245), -1)
    r = analyze(img)
    assert r.spec > 0.6 and r.lane < 0.3, r
