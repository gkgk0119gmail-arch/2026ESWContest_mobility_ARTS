"""RSCD → IcePredict 4클래스 학습 데이터셋.

- train/  : 27개 클래스 폴더 (파일명에도 라벨 있음)
- vali_20k/, test_50k/ : 평탄 폴더, 파일명 라벨
인덱스(경로, cls4_idx)는 처음 한 번 스캔해 index_<split>.txt 로 캐시한다 (100만 파일 스캔 ≈ 수십 초).
"""
from __future__ import annotations
import os, random
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
