"""RSCD → IcePredict 4클래스 학습 데이터셋.

- train/  : 27개 클래스 폴더 (파일명에도 라벨 있음)
- vali_20k/, test_50k/ : 평탄 폴더, 파일명 라벨
인덱스(경로, cls4_idx)는 처음 한 번 스캔해 index_<split>.txt 로 캐시한다 (100만 파일 스캔 ≈ 수십 초).
"""
from __future__ import annotations
import json, os, random
from pathlib import Path
import torch
from torch.utils.data import Dataset, WeightedRandomSampler
from torchvision import transforms as T
from torchvision.io import decode_jpeg, read_file, ImageReadMode
from icepredict.common.rscd import parse
from icepredict.common.protocol import ROAD_CLASSES

MEAN, STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)

def build_index(root: Path, split: str, cache_dir: Path | None = None) -> list[tuple[str, int]]:
    cache = (cache_dir or root) / f"index_{split}.txt"
    if cache.exists():
        out = []
        for line in cache.read_text().splitlines():
            p, c = line.rsplit("\t", 1)
            out.append((p, int(c)))
        return out
    items = []
    for dirpath, _, files in os.walk(root / split):
        for f in files:
            l = parse(f)
            if l is None:
                continue
            items.append((os.path.join(dirpath, f), l.cls4_idx))
    items.sort()
    cache.write_text("\n".join(f"{p}\t{c}" for p, c in items))
    return items

def train_tf(size=224):
    return T.Compose([
        T.RandomResizedCrop(size, scale=(0.6, 1.0), ratio=(0.9, 1.6), antialias=True),
        T.RandomHorizontalFlip(),
        T.ColorJitter(0.3, 0.3, 0.2, 0.02),
        T.ConvertImageDtype(torch.float32),
        T.Normalize(MEAN, STD),
    ])

def eval_tf(size=224):
    return T.Compose([
        T.Resize((size, size), antialias=True),
        T.ConvertImageDtype(torch.float32),
        T.Normalize(MEAN, STD),
    ])

class RscdDataset(Dataset):
    def __init__(self, items: list[tuple[str, int]], tf):
        self.items, self.tf = items, tf
    def __len__(self):
        return len(self.items)
    def __getitem__(self, i):
        p, c = self.items[i]
        img = decode_jpeg(read_file(p), mode=ImageReadMode.RGB)   # uint8 CxHxW
        return self.tf(img), c

def class_counts(items):
    n = [0] * len(ROAD_CLASSES)
    for _, c in items:
        n[c] += 1
    return n

def balanced_sampler(items, num_samples: int, seed: int = 0) -> WeightedRandomSampler:
    """클래스별 등확률 샘플링. normal이 압도적으로 많으므로 black_ice/pothole 학습 비중을 맞춘다."""
    n = class_counts(items)
    w = torch.tensor([1.0 / n[c] for _, c in items], dtype=torch.double)
    g = torch.Generator().manual_seed(seed)
    return WeightedRandomSampler(w, num_samples=num_samples, replacement=True, generator=g)

def subsample(items, per_class: int, seed: int = 0):
    """빠른 실험용: 클래스별 최대 per_class 장."""
    rng = random.Random(seed)
    by = {}
    for it in items:
        by.setdefault(it[1], []).append(it)
    out = []
    for c, lst in by.items():
        rng.shuffle(lst)
        out += lst[:per_class]
    rng.shuffle(out)
    return out

# ---- CARLA 합성 데이터셋 -------------------------------------------------
# 파일명 규칙: <seq>_<town>_<weather>_<cls>.jpg  (cls = normal | wet | black_ice)
# pothole은 CARLA 재현이 어려워 수집하지 않는다. 그래서 CARLA만으로 미세조정하면 4클래스
# 헤드의 pothole 쪽이 무너지고 실제 도메인 성능도 함께 잊는다 → 반드시 RSCD와 섞어 쓴다.
CARLA_CLASSES = ("normal", "wet", "black_ice")

def parse_carla(fname: str) -> int | None:
    """파일명에서 4클래스 인덱스. black_ice는 '_black_ice.jpg'로 끝난다."""
    stem = fname.rsplit(".", 1)[0]
    for c in ("black_ice", "normal", "wet"):          # black_ice를 먼저 봐야 한다
        if stem.endswith("_" + c):
            return ROAD_CLASSES.index(c)
    return None

def build_carla_index(root: Path, cache_dir: Path | None = None) -> list[tuple[str, int]]:
    root = Path(root)
    cache = (cache_dir or root) / "index_carla.txt"
    if cache.exists():
        out = []
        for line in cache.read_text().splitlines():
            p, c = line.rsplit("\t", 1)
            out.append((p, int(c)))
        return out
    items = []
    for dirpath, _, files in os.walk(root / "images"):
        for f in files:
            l = parse_carla(f)
            if l is None:
                continue
            items.append((os.path.join(dirpath, f), l))
    items.sort()
    cache.write_text("\n".join(f"{p}\t{c}" for p, c in items))
    return items

def carla_key(path) -> str:
    """파일명에서 town_weather 키. 클래스 접미사를 먼저 떼야 한다 — 'black_ice'는 언더스코어를
    포함하므로 단순 split으로 자르면 얼음 프레임만 다른 키가 되어 split이 어긋난다."""
    stem = Path(path).stem
    for c in ("black_ice", "normal", "wet"):
        if stem.endswith("_" + c):
            stem = stem[: -len(c) - 1]; break
    return stem.split("_", 1)[1] if "_" in stem else stem   # 앞의 seq 제거

def split_items(items, val_frac: float = 0.15, seed: int = 0):
    """CARLA는 공식 split이 없다. 같은 에피소드 프레임이 train/val에 섞이면 검증이 낙관적으로
    나오므로 (town, weather) 조합 단위로 나눈다 — 에피소드 경계에 가장 가까운 기준이다.

    건조/젖음 그룹에서 각각 뽑는다. 무작위로 뽑으면 val이 전부 건조 조합이 되어 wet 클래스가
    한 장도 없는 검증셋이 만들어질 수 있다."""
    by = {}
    for p, c in items:
        by.setdefault(carla_key(p), []).append((p, c))
    dry = sorted(k for k in by if "_dry_" in k or k.endswith("_dry"))
    wet = sorted(k for k in by if k not in dry)
    rng = random.Random(seed)
    vk = set()
    for group in (dry, wet):
        if not group:
            continue
        g = list(group); rng.shuffle(g)
        vk.update(g[: max(1, int(len(g) * val_frac))])
    tr = [x for k in sorted(by) if k not in vk for x in by[k]]
    va = [x for k in sorted(vk) for x in by[k]]
    return tr, va, sorted(vk)

def finetune_tf(size=224):
    """미세조정용 증강. 선명도·밝기를 클래스 단서로 쓰지 못하게 블러/노이즈/밝기를 모든
    클래스에 무작위로 건다 (합성기 v1의 실패를 학습 단계에서 한 번 더 막는 안전장치)."""
    return T.Compose([
        T.RandomResizedCrop(size, scale=(0.7, 1.0), ratio=(0.9, 1.6), antialias=True),
        T.RandomHorizontalFlip(),
        T.ColorJitter(0.35, 0.35, 0.25, 0.03),
        T.RandomApply([T.GaussianBlur(5, sigma=(0.4, 2.2))], p=0.5),
        T.ConvertImageDtype(torch.float32),
        T.RandomApply([T.Lambda(lambda x: (x + torch.randn_like(x) * 0.02).clamp(0, 1))], p=0.4),
        T.Normalize(MEAN, STD),
    ])


# ---- 반사도(멀티태스크) 데이터셋 ------------------------------------------
# CARLA 수집기가 meta.json에 프레임별 spec(ROI 평균 반사도)을 남긴다. 얼음은 합성이 실제로 쓴
# alpha·R 평균이고, 엔진 렌더 젖음은 날씨 wetness에서 유도한 값이다 (carla_collect.py 주석 참고).
# RSCD 실사진에는 이 라벨이 없으므로 has_spec=0으로 표시해 손실에서 제외한다.
class SpecDataset(Dataset):
    def __init__(self, items: list[tuple[str, int, float, float]], tf):
        self.items, self.tf = items, tf
    def __len__(self):
        return len(self.items)
    def __getitem__(self, i):
        p, c, spec, has = self.items[i]
        img = decode_jpeg(read_file(p), mode=ImageReadMode.RGB)
        return self.tf(img), c, torch.tensor(spec, dtype=torch.float32), torch.tensor(has, dtype=torch.float32)

def build_carla_spec_index(root: Path) -> list[tuple[str, int, float, float]]:
    """(경로, 클래스, spec, has_spec=1). meta.json이 없으면 파일명 라벨만 쓰고 has_spec=0."""
    root = Path(root)
    meta_p = root / "meta.json"
    by_file = {}
    if meta_p.exists():
        for m in json.loads(meta_p.read_text()):
            if "spec" in m:
                by_file[m["file"]] = float(m["spec"])
    out = []
    for p, c in build_carla_index(root):
        name = Path(p).name
        if name in by_file:
            out.append((p, c, by_file[name], 1.0))
        else:
            out.append((p, c, 0.0, 0.0))
    return out

def with_spec(items: list[tuple[str, int]], spec: float = 0.0, has: float = 0.0):
    """RSCD 등 반사도 라벨이 없는 목록을 SpecDataset 형식으로."""
    return [(p, c, spec, has) for p, c in items]
