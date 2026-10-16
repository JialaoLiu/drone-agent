"""本地初筛分类器：在调用视觉模型之前，用一个小模型先过滤“明显正常”的板。

做法：
  - 训练数据来自仿真录制（captures/<场景>/）：整列照片按相机位姿裁切、校正出每块板，标签取场景生成器记录的标准答案，
    不需要人工标注；
  - 特征是手工设计的图像统计量（亮度、梯度、亮线与暗斑比例、4×4 分块的局部异常），并减去同一张照片中所有板的中位数，
    抵消光照与太阳方向的差异；
  - 分类器为梯度提升树（scikit-learn，BSD 许可证），输出“有缺陷”的概率；
  - 级联：概率低于阈值的板视为正常、不交给视觉模型；其余照常初筛。阈值在训练场景上按“漏掉的缺陷不超过 x%”选取，
    在没见过的测试场景上评估。只用于减少初筛调用，任何板都不会因为分类器而被判为“有缺陷”。
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from . import config, vision
from .sim.camera import Frame

MODEL_PATH = config.DATA_DIR / "models" / "triage.pkl"
GRID = 4


def _sobel(g: np.ndarray) -> np.ndarray:
    gx = np.zeros_like(g)
    gy = np.zeros_like(g)
    gx[:, 1:-1] = g[:, 2:] - g[:, :-2]
    gy[1:-1, :] = g[2:, :] - g[:-2, :]
    return np.hypot(gx, gy)


def features(img: Image.Image) -> np.ndarray:
    """一块校正后板块图的特征向量。"""
    a = np.asarray(img.convert("RGB").resize((vision.TILE_W // 2, vision.TILE_H // 2)), dtype=np.float32) / 255.0
    g = a.mean(axis=2)
    mx, mn = a.max(axis=2), a.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    edge = _sobel(g)
    lap = np.abs(4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:])
    mu, sd = g.mean(), g.std() + 1e-6
    h, w = g.shape
    cells = np.array([[g[i * h // GRID:(i + 1) * h // GRID, j * w // GRID:(j + 1) * w // GRID].mean()
                       for j in range(GRID)] for i in range(GRID)])
    cell_edges = np.array([[edge[i * h // GRID:(i + 1) * h // GRID, j * w // GRID:(j + 1) * w // GRID].mean()
                            for j in range(GRID)] for i in range(GRID)])
    dev = np.abs(cells - np.median(cells))
    return np.array([
        mu, sd, *np.percentile(g, [1, 5, 50, 95, 99]),
        *a.reshape(-1, 3).mean(axis=0), *a.reshape(-1, 3).std(axis=0),
        sat.mean(), sat.std(),
        edge.mean(), edge.std(), np.percentile(edge, 95), (edge > 0.15).mean(),
        lap.mean(), lap.var(),
        (g > mu + 2 * sd).mean(), (g < mu - 2 * sd).mean(),        # 亮线（裂纹、破碎）与暗斑（污损）的比例
        (g > 0.75).mean(), (g < 0.12).mean(),
        dev.max(), dev.std(), cells.std(),
        cell_edges.max() - np.median(cell_edges), cell_edges.std(),
    ], dtype=np.float32)


def photo_features(frame: Frame, panels: list[dict]) -> tuple[list[str], np.ndarray]:
    """一张整列照片中所有能看清的板：原始特征加上“减去本照片中位数”的相对特征。"""
    ids, rows = [], []
    for p in panels:
        t = vision.rectify(frame, p)
        if t is not None:
            ids.append(p["id"])
            rows.append(features(t.image))
    if not rows:
        return [], np.zeros((0, 0), dtype=np.float32)
    x = np.stack(rows)
    return ids, np.hstack([x, x - np.median(x, axis=0)])


@dataclass
class Sample:
    scene: str
    panel_id: str
    defect: str | None
    severity: str | None
    distractor: str | None


def dataset(scenes: list[str], captures: Path | None = None) -> tuple[np.ndarray, np.ndarray, list[Sample]]:
    """从录制的场景构造训练 / 测试数据（整列照片中的板块图 + 标准答案）。"""
    captures = captures or (config.DATA_DIR / "captures")
    xs, ys, meta = [], [], []
    for scene in scenes:
        site = json.loads((captures / scene / "site.json").read_text(encoding="utf-8"))
        by_array: dict[str, list[dict]] = {}
        for p in site["panels"]:
            by_array.setdefault(p["array"], []).append(p)
        truth = {p["id"]: p for p in site["panels"]}
        for aid, panels in by_array.items():
            path = captures / scene / f"overview_{aid}"
            if not path.with_suffix(".png").exists():
                continue
            ids, x = photo_features(Frame.load(path), panels)
            for pid, row in zip(ids, x):
                p = truth[pid]
                d = p.get("defect") or None
                xs.append(row)
                ys.append(1 if d else 0)
                meta.append(Sample(scene, pid, d["type"] if d else None, d.get("severity") if d else None,
                                   (p.get("distractor") or {}).get("type") if isinstance(p.get("distractor"), dict) else p.get("distractor")))
    return np.stack(xs), np.array(ys), meta


def train(x: np.ndarray, y: np.ndarray, seed: int = 0):
    from sklearn.ensemble import HistGradientBoostingClassifier
    clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, l2_regularization=1.0,
                                         class_weight="balanced", random_state=seed)
    return clf.fit(x, y)


def threshold_for_recall(y: np.ndarray, p: np.ndarray, recall: float) -> float:
    """最大的阈值，使得概率不低于它的板覆盖至少 recall 比例的缺陷。"""
    pos = np.sort(p[y == 1])
    if not len(pos):
        return 0.0
    k = int(np.floor((1 - recall) * len(pos)))
    return float(pos[k]) - 1e-9


def save(model, threshold: float, info: dict, path: Path = MODEL_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pickle.dumps({"model": model, "threshold": threshold, "info": info}))


def load(path: Path = MODEL_PATH) -> dict | None:
    return pickle.loads(path.read_bytes()) if path.exists() else None
