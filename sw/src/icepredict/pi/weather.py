"""기상청 초단기실황(10분 갱신) 수신 → WeatherObs.

공공데이터포털 '기상청_단기예보 조회서비스' getUltraSrtNcst 사용.
환경변수 KMA_API_KEY 또는 프로젝트 루트 secrets.yaml 의 kma_api_key 를 읽는다.
키가 없거나 오프라인이면 mock=True 로 고정 관측값을 돌려준다.
"""
from __future__ import annotations
import os, math, datetime as dt
from pathlib import Path
import requests
from icepredict.pi.context import WeatherObs

URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst"

def load_key() -> str | None:
    k = os.environ.get("KMA_API_KEY")
    if k:
        return k
    p = Path(__file__).resolve().parents[3] / "secrets.yaml"
    if p.exists():
        import yaml
        return yaml.safe_load(p.read_text()).get("kma_api_key")
    return None

def latlon_to_grid(lat: float, lon: float) -> tuple[int, int]:
    """기상청 LCC 격자 변환 (DFS)."""
    RE, GRID = 6371.00877, 5.0
    SLAT1, SLAT2, OLON, OLAT, XO, YO = 30.0, 60.0, 126.0, 38.0, 43, 136
    D = math.pi / 180.0
    re = RE / GRID
    slat1, slat2, olon, olat = SLAT1 * D, SLAT2 * D, OLON * D, OLAT * D
    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5) ** sn * math.cos(slat1) / sn
    ro = re * sf / math.tan(math.pi * 0.25 + olat * 0.5) ** sn
    ra = re * sf / math.tan(math.pi * 0.25 + lat * D * 0.5) ** sn
    theta = lon * D - olon
    theta = (theta + math.pi) % (2 * math.pi) - math.pi
    theta *= sn
    x = int(ra * math.sin(theta) + XO + 0.5)
    y = int(ro - ra * math.cos(theta) + YO + 0.5)
    return x, y

def _base_datetime(now: dt.datetime) -> tuple[str, str]:
    # 실황은 매시 40분 이후 해당 시각 자료가 나온다
    t = now if now.minute >= 40 else now - dt.timedelta(hours=1)
    return t.strftime("%Y%m%d"), t.strftime("%H00")

_prev_temp: dict[str, float] = {}

def fetch(lat: float, lon: float, key: str | None = None, mock: bool = False, timeout: float = 5.0) -> WeatherObs:
    if mock:
        return WeatherObs(temp_c=-3.0, humidity=88.0, precip_mm=0.0, wind_mps=1.5, temp_trend_c_per_h=-1.0)
    key = key or load_key()
    if not key:
        raise RuntimeError("KMA_API_KEY 없음: 환경변수 또는 secrets.yaml 에 설정하거나 mock=True 사용")
    nx, ny = latlon_to_grid(lat, lon)
    bd, bt = _base_datetime(dt.datetime.now())
    r = requests.get(URL, params={"serviceKey": key, "pageNo": 1, "numOfRows": 20, "dataType": "JSON",
                                  "base_date": bd, "base_time": bt, "nx": nx, "ny": ny}, timeout=timeout)
    r.raise_for_status()
    items = r.json()["response"]["body"]["items"]["item"]
    v = {it["category"]: float(it["obsrValue"]) for it in items}
    temp = v.get("T1H", 0.0)
    key_loc = f"{nx},{ny}"
    trend = temp - _prev_temp.get(key_loc, temp)
    _prev_temp[key_loc] = temp
    return WeatherObs(temp_c=temp, humidity=v.get("REH", 50.0), precip_mm=v.get("RN1", 0.0),
                      wind_mps=v.get("WSD", 0.0), temp_trend_c_per_h=trend,
                      snow_on_ground=v.get("PTY", 0) in (2, 3, 7))
