"""真实场地数字孪生：高程（Copernicus DEM）与地物（OpenStreetMap）。

全部数据是 WGS-84，进入仿真前换算为本地平面坐标（x 东、y 北，米），不需要任何国内坐标系换算。
- 高程：Copernicus DEM GLO-30（约 30 米网格，© DLR e.V. 2010–2014 与 © Airbus Defence and Space GmbH 2014–2018，
  由欧空局与欧盟哥白尼计划提供），按 1°×1° 瓦片从公开存储下载并缓存；
- 地物：OpenStreetMap（© OpenStreetMap 贡献者，ODbL），先用 Overpass 查询（多个镜像、退避重试），失败时用官方地图数据接口取小范围原始数据；
  结果按位置缓存，不会反复请求。
仿真地面仍是平面：地形起伏只用于安全员的离地高度检查，起伏过大时在场景中注明。
"""
from __future__ import annotations

import json
import math
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import numpy as np

from . import config
from .geo import Origin

CACHE = config.DATA_DIR / "cache"
DEM_URL = "https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
OSM_MAP = "https://api.openstreetmap.org/api/0.6/map"
USER_AGENT = "drone-agent-simulation/0.1 (personal research project)"
FLAT_RELIEF = 3.0           # 场地内高差不超过这个值（米）时，平面仿真地面与真实地形一致


class TwinError(RuntimeError):
    pass


# ---------- 高程 ----------

def _tile_name(lat: float, lon: float) -> str:
    la, lo = math.floor(lat), math.floor(lon)
    ns, ew = ("N" if la >= 0 else "S"), ("E" if lo >= 0 else "W")
    return f"Copernicus_DSM_COG_10_{ns}{abs(la):02d}_00_{ew}{abs(lo):03d}_00_DEM"


_tiles: dict[str, tuple[np.ndarray, float, float, float]] = {}


def _tile(lat: float, lon: float):
    name = _tile_name(lat, lon)
    if name in _tiles:
        return _tiles[name]
    path = CACHE / "dem" / f"{name}.tif"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with httpx.stream("GET", DEM_URL.format(name=name), timeout=120) as r:
                if r.status_code == 404:
                    raise TwinError(f"该位置没有高程数据（{name}，可能是海面）")
                r.raise_for_status()
                tmp = path.with_suffix(".part")
                with tmp.open("wb") as fh:
                    for chunk in r.iter_bytes():
                        fh.write(chunk)
                tmp.rename(path)
        except httpx.HTTPError as error:
            raise TwinError(f"高程数据下载失败：{type(error).__name__}") from error
    import tifffile
    with tifffile.TiffFile(path) as t:
        page = t.pages[0]
        tie = page.tags["ModelTiepointTag"].value
        scale = page.tags["ModelPixelScaleTag"].value
        data = page.asarray().astype(np.float32)
    _tiles[name] = (data, tie[3], tie[4], scale[0])      # 数组、左上角经度、左上角纬度、像素大小（度）
    return _tiles[name]


def elevation(lat: float, lon: float) -> float:
    """双线性插值的地面高程（米，海拔）。"""
    data, lon0, lat0, step = _tile(lat, lon)
    c, r = (lon - lon0) / step, (lat0 - lat) / step
    c0, r0 = int(math.floor(c)), int(math.floor(r))
    c0, r0 = max(0, min(data.shape[1] - 2, c0)), max(0, min(data.shape[0] - 2, r0))
    fc, fr = c - c0, r - r0
    v = data[r0:r0 + 2, c0:c0 + 2]
    return float(v[0, 0] * (1 - fc) * (1 - fr) + v[0, 1] * fc * (1 - fr) + v[1, 0] * (1 - fc) * fr + v[1, 1] * fc * fr)


def terrain_grid(origin: dict, half: float, step: float = 10.0) -> dict:
    """以原点为中心、边长 2*half 的高程网格（相对起飞点的高度，米）。"""
    o = Origin(origin["lat"], origin["lon"], 0.0)
    e0 = elevation(origin["lat"], origin["lon"])
    n = int(2 * half / step) + 1
    grid = []
    for j in range(n):
        y = -half + j * step
        row = []
        for i in range(n):
            lat, lon, _ = o.to_global(-half + i * step, y)
            row.append(round(elevation(lat, lon) - e0, 2))
        grid.append(row)
    flat = [v for row in grid for v in row]
    return {"origin_elevation": round(e0, 2), "half": half, "step": step, "rel": grid,
            "relief": round(max(flat) - min(flat), 2), "source": "Copernicus DEM GLO-30"}


def relative_height(terrain: dict, x: float, y: float) -> float:
    """本地坐标处地面相对起飞点的高度（米）。"""
    h, s, g = terrain["half"], terrain["step"], terrain["rel"]
    n = len(g)
    fi, fj = (x + h) / s, (y + h) / s
    i0, j0 = max(0, min(n - 2, int(math.floor(fi)))), max(0, min(n - 2, int(math.floor(fj))))
    fi, fj = min(1.0, max(0.0, fi - i0)), min(1.0, max(0.0, fj - j0))
    return (g[j0][i0] * (1 - fi) * (1 - fj) + g[j0][i0 + 1] * fi * (1 - fj)
            + g[j0 + 1][i0] * (1 - fi) * fj + g[j0 + 1][i0 + 1] * fi * fj)


# ---------- 地物 ----------

def _bbox(lat: float, lon: float, half: float) -> tuple[float, float, float, float]:
    dlat = math.degrees(half / 6378137.0)
    dlon = math.degrees(half / (6378137.0 * math.cos(math.radians(lat))))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


def _classify(tags: dict) -> str | None:
    if tags.get("natural") == "water" or tags.get("water") or tags.get("waterway") in ("riverbank",) or tags.get("landuse") in ("reservoir", "basin"):
        return "water"
    if tags.get("building"):
        return "building"
    if tags.get("landuse") == "solar" or (tags.get("power") in ("plant", "generator") and "solar" in (tags.get("plant:source", "") + tags.get("generator:source", ""))):
        return "solar"
    if tags.get("natural") == "wood" or tags.get("landuse") == "forest":
        return "wood"
    if tags.get("aeroway") in ("aerodrome", "helipad", "runway"):
        return "aeroway"
    return None


def _height(tags: dict) -> float:
    try:
        return float(str(tags.get("height", "")).split()[0])
    except (ValueError, IndexError):
        pass
    try:
        return float(tags.get("building:levels")) * 3.0
    except (TypeError, ValueError):
        return 9.0          # 没有高度信息的建筑按 3 层估计


def _overpass(s: float, w: float, n: float, e: float) -> list[dict]:
    q = (f"[out:json][timeout:40];("
         f'way["natural"="water"]({s},{w},{n},{e});relation["natural"="water"]({s},{w},{n},{e});'
         f'way["building"]({s},{w},{n},{e});way["landuse"~"solar|forest|reservoir|basin"]({s},{w},{n},{e});'
         f'way["power"~"plant|generator"]({s},{w},{n},{e});way["natural"="wood"]({s},{w},{n},{e});'
         f'way["aeroway"]({s},{w},{n},{e});node["natural"="tree"]({s},{w},{n},{e}););out geom tags;')
    last = None
    for attempt in range(3):
        for url in OVERPASS:
            try:
                r = httpx.post(url, data={"data": q}, headers={"User-Agent": USER_AGENT}, timeout=60)
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
                    out = []
                    for el in r.json().get("elements", []):
                        tags = el.get("tags", {})
                        if el["type"] == "node":
                            out.append({"tags": tags, "points": [(el["lat"], el["lon"])]})
                        elif el["type"] == "way" and el.get("geometry"):
                            out.append({"tags": tags, "points": [(p["lat"], p["lon"]) for p in el["geometry"]]})
                        elif el["type"] == "relation":
                            for m in el.get("members", []):
                                if m.get("role") == "outer" and m.get("geometry"):
                                    out.append({"tags": tags, "points": [(p["lat"], p["lon"]) for p in m["geometry"]]})
                    return out
                last = f"{url} 返回 {r.status_code}"
            except httpx.HTTPError as error:
                last = f"{url} {type(error).__name__}"
        time.sleep(3 * 2 ** attempt)
    raise TwinError(f"Overpass 不可用（{last}）")


def _osm_api(s: float, w: float, n: float, e: float) -> list[dict]:
    try:
        r = httpx.get(OSM_MAP, params={"bbox": f"{w},{s},{e},{n}"}, headers={"User-Agent": USER_AGENT}, timeout=60)
        r.raise_for_status()
    except httpx.HTTPError as error:
        raise TwinError(f"OpenStreetMap 接口不可用：{type(error).__name__}") from error
    root = ET.fromstring(r.content)
    nodes = {nd.get("id"): (float(nd.get("lat")), float(nd.get("lon"))) for nd in root.iter("node")}
    out = []
    for nd in root.iter("node"):
        tags = {t.get("k"): t.get("v") for t in nd.iter("tag")}
        if tags.get("natural") == "tree":
            out.append({"tags": tags, "points": [nodes[nd.get("id")]]})
    ways = {}
    for way in root.iter("way"):
        pts = [nodes[nd.get("ref")] for nd in way.iter("nd") if nd.get("ref") in nodes]
        tags = {t.get("k"): t.get("v") for t in way.iter("tag")}
        ways[way.get("id")] = pts
        if tags:
            out.append({"tags": tags, "points": pts})
    for rel in root.iter("relation"):                      # 水面多为多边形关系：取外环中已下载的部分
        tags = {t.get("k"): t.get("v") for t in rel.iter("tag")}
        if _classify(tags) != "water":
            continue
        for m in rel.iter("member"):
            if m.get("type") == "way" and m.get("role") == "outer" and m.get("ref") in ways:
                out.append({"tags": tags, "points": ways[m.get("ref")]})
    return out


def features(origin: dict, half: float = 160.0) -> dict:
    """场地周围的地物（本地坐标）。返回 {source, items: [{kind, polygon | center, height, tags}]}。"""
    key = f"{origin['lat']:.5f}_{origin['lon']:.5f}_{int(half)}.json"
    path = CACHE / "osm" / key
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    s, w, n, e = _bbox(origin["lat"], origin["lon"], half)
    try:
        raw, source = _overpass(s, w, n, e), "Overpass"
    except TwinError:
        raw, source = _osm_api(s, w, n, e), "OpenStreetMap 地图数据接口"
    o = Origin(origin["lat"], origin["lon"], 0.0)
    items = []
    for f in raw:
        kind = _classify(f["tags"]) or ("tree" if f["tags"].get("natural") == "tree" else None)
        if not kind or not f["points"]:
            continue
        local = [[round(v, 2) for v in o.to_local(la, lo)[:2]] for la, lo in f["points"]]
        item = {"kind": kind, "tags": {k: v for k, v in f["tags"].items() if k in ("name", "height", "building:levels", "natural", "landuse", "building")}}
        if kind == "tree":
            item["center"] = local[0]
        else:
            if len(local) < 3:
                continue
            item["polygon"] = local
            if kind == "building":
                item["height"] = round(_height(f["tags"]), 1)
        items.append(item)
    data = {"source": source, "attribution": "© OpenStreetMap 贡献者（ODbL）", "half": half, "items": items}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


# ---------- 几何 ----------

def _inside(poly: list, x: float, y: float) -> bool:
    inside, j = False, len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _edge_dist(poly: list, x: float, y: float) -> float:
    best = float("inf")
    for i in range(len(poly)):
        (ax, ay), (bx, by) = poly[i], poly[(i + 1) % len(poly)]
        dx, dy = bx - ax, by - ay
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(x - ax - t * dx, y - ay - t * dy))
    return best


def blocked_fn(data: dict, kinds=("water", "building")):
    """返回 blocked(x, y, r)：半径 r 的圆是否与水面或建筑相交（阵列、起飞点不能放在这些地方）。"""
    polys = [it["polygon"] for it in data["items"] if it["kind"] in kinds and "polygon" in it]

    def blocked(x: float, y: float, r: float = 0.0) -> bool:
        return any(_inside(p, x, y) or _edge_dist(p, x, y) < r for p in polys)
    return blocked


def preferred_fn(data: dict):
    """返回 preferred(x, y)：是否在真实光伏电站轮廓内；附近没有电站轮廓时返回 None。"""
    polys = [it["polygon"] for it in data["items"] if it["kind"] == "solar" and "polygon" in it]
    if not polys:
        return None
    return lambda x, y: any(_inside(p, x, y) for p in polys)


def obstacles(data: dict, limit: float = 150.0) -> list[dict]:
    """建筑与单棵树作为障碍物（圆近似：面积等效半径，最多 15 米；高度取标签或按层数估计）。"""
    out = []
    for it in data["items"]:
        if it["kind"] == "building":
            p = np.array(it["polygon"])
            cx, cy = float(p[:, 0].mean()), float(p[:, 1].mean())
            area = 0.5 * abs(float(np.dot(p[:, 0], np.roll(p[:, 1], 1)) - np.dot(p[:, 1], np.roll(p[:, 0], 1))))
            if math.hypot(cx, cy) > limit:
                continue
            # 安全员按真实轮廓（polygon）判断净空；radius 取外接圆，只用于显示和粗筛，不再用面积等效圆（审查 H02）
            far = float(np.max(np.hypot(p[:, 0] - cx, p[:, 1] - cy)))
            out.append({"id": f"B{len(out) + 1}", "label": "建筑", "center": [round(cx, 1), round(cy, 1)],
                        "radius": round(max(2.0, far), 1), "height": it["height"], "polygon": [[round(x, 2), round(y, 2)] for x, y in it["polygon"]],
                        "area_m2": round(area, 1)})
        elif it["kind"] == "tree" and math.hypot(*it["center"]) <= limit:
            out.append({"id": f"T{len(out) + 1}", "label": "树", "center": it["center"], "radius": 2.5, "height": 9.0})
    return out
