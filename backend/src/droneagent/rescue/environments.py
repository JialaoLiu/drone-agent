"""随机救援场景的环境类型（第三轮反馈：场景太单一，要有山林、河岸等）。

每种环境给出：围栏内的水面、障碍物（树、建筑轮廓）、地标（仿真里真的建了模型或有明确位置）、行人可走区域、
环境专属模型、地面贴图，以及给规划员的一句环境说明。公园湖泊仍在 scene.py 里（保持已有种子生成结果不变）。

仿真地面是平面：“山林”表现为密林、林间小道与溪流造成的遮挡，没有坡度（地面说明里写明）。
"""
from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw, ImageFilter

from ..sim.scene import _ground, _inside
from ..twin import _edge_dist

ENVIRONMENTS = {
    "park": {"label": "公园湖泊", "note": "开阔草地与湖面，有树和步道"},
    "forest": {"label": "山林", "note": "树木密集，人可能在树冠下被遮挡；有林间小道和溪流"},
    "riverside": {"label": "河岸", "note": "一条较宽的河穿过场地，岸边有步道、桥和码头，落水者会随水流漂移"},
    "plaza": {"label": "城市广场", "note": "四周是高楼，中间是广场，行人多；建筑是障碍物"},
}


def _box(name: str, pose: tuple, size: tuple, rgb: tuple) -> str:
    return (f'<visual name="{name}"><pose>{" ".join(f"{v:.3f}" for v in pose)}</pose><geometry><box><size>{size[0]:.3f} {size[1]:.3f} {size[2]:.3f}</size></box></geometry>'
            f'<material><diffuse>{rgb[0]} {rgb[1]} {rgb[2]} 1</diffuse><ambient>{rgb[0] * 0.6:.2f} {rgb[1] * 0.6:.2f} {rgb[2] * 0.6:.2f} 1</ambient></material></visual>')


def _model(name: str, c: list[float], yaw: float, visuals: str) -> str:
    return f'<model name="{name}"><static>true</static><pose>{c[0]:.2f} {c[1]:.2f} 0 0 0 {yaw:.3f}</pose><link name="l">{visuals}</link></model>'


def hut_sdf(c, yaw=0.0) -> str:
    return _model("ranger_hut", c, yaw, _box("wall", (0, 0, 1.3, 0, 0, 0), (4.0, 3.0, 2.6), (0.55, 0.42, 0.28))
                  + _box("roof", (0, 0, 2.8, 0, 0, 0), (4.6, 3.6, 0.3), (0.30, 0.22, 0.16)))


def deck_sdf(name: str, c, yaw: float, length: float, width: float, z: float, rgb=(0.45, 0.32, 0.20)) -> str:
    return _model(name, c, yaw, _box("deck", (0, 0, z, 0, 0, 0), (length, width, 0.25), rgb))


def fountain_sdf(c) -> str:
    return _model("fountain", c, 0.0,
                  f'<visual name="basin"><pose>0 0 0.3 0 0 0</pose><geometry><cylinder><radius>3.2</radius><length>0.6</length></cylinder></geometry>'
                  f'<material><diffuse>0.75 0.74 0.70 1</diffuse></material></visual>'
                  f'<visual name="water"><pose>0 0 0.62 0 0 0</pose><geometry><cylinder><radius>2.8</radius><length>0.04</length></cylinder></geometry>'
                  f'<material><diffuse>0.25 0.50 0.65 1</diffuse></material></visual>')


def kiosk_sdf(name: str, c, yaw: float, rgb=(0.35, 0.45, 0.55)) -> str:
    return _model(name, c, yaw, _box("body", (0, 0, 1.25, 0, 0, 0), (3.0, 2.0, 2.5), rgb))


def _band(rng: random.Random, half: float, width: float, clearance: float, sign: int, vertical: bool) -> tuple[list[list[float]], list[list[float]]]:
    """弯曲的带状水面（河、溪）：返回多边形与中线。clearance 是起飞点到最近水边的最小距离。"""
    amp, k, ph = rng.uniform(4, 9), rng.uniform(0.025, 0.05), rng.uniform(0, 2 * math.pi)
    offset = sign * (width / 2 + amp + clearance)
    line = []
    for i in range(int(2 * (half + 20) / 5) + 1):
        t = -half - 20 + 5 * i
        line.append([t, offset + amp * math.sin(k * t + ph)])
    upper = [[t, c + width / 2] for t, c in line]
    lower = [[t, c - width / 2] for t, c in reversed(line)]
    poly = upper + lower
    if vertical:
        poly, line = [[y, x] for x, y in poly], [[y, x] for x, y in line]
    return [[round(x, 2), round(y, 2)] for x, y in poly], line


def _clear_of(obstacles: list[dict], x: float, y: float, margin: float) -> bool:
    for o in obstacles:
        if o.get("polygon"):
            if _inside(o["polygon"], x, y) or _edge_dist(o["polygon"], x, y) < margin:
                return False
        elif math.hypot(x - o["center"][0], y - o["center"][1]) < o["radius"] + margin:
            return False
    return True


def _trees(rng: random.Random, half: float, n: int, ok, spacing: float, radius: tuple, height: tuple, start: int = 1) -> list[dict]:
    out = []
    for k in range(n):
        for _ in range(40):
            x, y = rng.uniform(-half + 5, half - 5), rng.uniform(-half + 5, half - 5)
            if ok(x, y) and math.hypot(x, y) > 10 and all(math.hypot(x - o["center"][0], y - o["center"][1]) > spacing for o in out):
                out.append({"id": f"T{start + k}", "label": "树", "center": [round(x, 1), round(y, 1)],
                            "radius": round(rng.uniform(*radius), 1), "height": round(rng.uniform(*height), 1)})
                break
    return out


def _to_px(extent: float, size: int):
    return lambda x, y: ((x + extent / 2) / extent * size, (extent / 2 - y) / extent * size)


def _tint(img: Image.Image, rgb: tuple, alpha: float) -> Image.Image:
    return Image.blend(img, Image.new("RGB", img.size, rgb), alpha)


# ---------- 山林 ----------

def forest(rng: random.Random, half: float) -> dict:
    fence = [[-half, -half], [half, -half], [half, half], [-half, half]]
    sign = rng.choice((-1, 1))
    stream, stream_line = _band(rng, half, rng.uniform(4, 6), rng.uniform(12, 32), sign, rng.random() < 0.5)
    water = [stream]
    in_water = lambda x, y: _inside(stream, x, y)                                         # noqa: E731
    # 林间小道：从西到东的折线，树不长在道上
    trail = [[-half, rng.uniform(-20, 20)]]
    while trail[-1][0] < half:
        x, y = trail[-1]
        trail.append([x + 12, max(-half + 8, min(half - 8, y + rng.uniform(-8, 8)))])
    near_trail = lambda x, y: min(math.hypot(x - p[0], y - p[1]) for p in trail) < 4      # noqa: E731
    obstacles = _trees(rng, half, rng.randint(55, 75), lambda x, y: not in_water(x, y) and not near_trail(x, y),
                       5.5, (2.5, 4.0), (8.0, 14.0))
    c = min((p for p in stream_line if abs(p[0]) < half - 5 and abs(p[1]) < half - 5), key=lambda p: math.hypot(*p))
    r = math.hypot(*c) or 1
    shore = [c[0] * (1 - 5 / r), c[1] * (1 - 5 / r)]          # 溪边：从中线朝起飞点方向移到岸上
    dry = [p for p in trail[1:-1] if not in_water(*p) and min(math.hypot(p[0] - q[0], p[1] - q[1]) for q in stream_line) > 8]
    dry = dry or [[-half / 2, 0.0], [0.0, -half / 2], [half / 2, 0.0]]
    mid = min(dry, key=lambda p: abs(p[0]))
    hut = [dry[0][0], dry[0][1] + 6] if not in_water(dry[0][0], dry[0][1] + 6) else [dry[0][0], dry[0][1] - 6]
    view = dry[-1]
    landmarks = [
        {"id": "ranger_hut", "label": "护林站", "center": [round(hut[0], 1), round(hut[1], 1)], "source": "procedural"},
        {"id": "fork", "label": "林间岔路口", "center": [round(mid[0], 1), round(mid[1], 1)], "source": "procedural"},
        {"id": "creek", "label": "溪边", "center": [round(shore[0], 1), round(shore[1], 1)], "shore": True, "source": "procedural"},
        {"id": "lookout", "label": "观景台", "center": [round(view[0], 1), round(view[1], 1)], "source": "procedural"},
    ]
    obstacles = [o for o in obstacles if all(math.hypot(o["center"][0] - l["center"][0], o["center"][1] - l["center"][1]) > o["radius"] + 3 for l in landmarks)]
    # 行人在林下也能走（树冠挡的是俯视视线，不挡人）：只避开树干
    walkable = lambda x, y: abs(x) < half - 4 and abs(y) < half - 4 and not in_water(x, y) and \
        all(math.hypot(x - o["center"][0], y - o["center"][1]) > 1.0 for o in obstacles)          # noqa: E731

    def ground(rng2: random.Random, extent: float) -> Image.Image:
        img = _tint(_ground(rng2), (46, 62, 34), 0.45)
        d, to_px = ImageDraw.Draw(img), _to_px(extent, img.size[0])
        d.line([to_px(*p) for p in trail], fill=(122, 100, 70), width=max(2, int(2.0 / extent * img.size[0])))
        d.polygon([to_px(*p) for p in stream], fill=(92, 80, 60))
        return img.filter(ImageFilter.GaussianBlur(0.8))
    models = [hut_sdf(hut), deck_sdf("lookout_deck", view, 0.0, 5, 4, 0.5)]
    return {"geofence": fence, "water": water, "obstacles": obstacles, "landmarks": landmarks, "no_fly_zones": [],
            "walkable": walkable, "in_water": in_water, "models": models, "ground": ground, "bystanders": (2, 5),
            "callers": (["同行的驴友", "护林员"], ["同行的驴友", "我同事", "一位游客"])}


# ---------- 河岸 ----------

def riverside(rng: random.Random, half: float) -> dict:
    fence = [[-half, -half], [half, -half], [half, half], [-half, half]]
    vertical = rng.random() < 0.5
    width = rng.uniform(14, 20)
    sign = rng.choice((-1, 1))
    river, line = _band(rng, half, width, rng.uniform(12, 20), sign, vertical)
    in_water = lambda x, y: _inside(river, x, y)                                          # noqa: E731
    inside = [p for p in line if abs(p[0]) < half - 10 and abs(p[1]) < half - 10]
    across = (1, 0) if not vertical else (0, 1)        # 沿河方向
    normal = (0, 1) if not vertical else (1, 0)        # 垂直于河
    bridge_c = inside[len(inside) // 3]
    pier_c = inside[2 * len(inside) // 3]
    side = -sign                                       # 起飞点所在的一侧
    bank = lambda c, d: [c[0] + side * normal[0] * (width / 2 + d), c[1] + side * normal[1] * (width / 2 + d)]   # noqa: E731
    beach = min(inside, key=lambda p: math.hypot(*p))
    # 步道：沿河、离岸 6 米；树沿步道成排
    path = [bank(p, 6) for p in line]
    obstacles = []
    for k, p in enumerate(path[2:-2:2]):
        q = bank(line[2 + 2 * k], 10)
        if abs(q[0]) < half - 5 and abs(q[1]) < half - 5 and math.hypot(*q) > 10:
            obstacles.append({"id": f"T{len(obstacles) + 1}", "label": "树", "center": [round(q[0], 1), round(q[1], 1)],
                              "radius": round(rng.uniform(2.5, 3.5), 1), "height": round(rng.uniform(8, 12), 1)})
    obstacles += _trees(rng, half, rng.randint(6, 10), lambda x, y: not in_water(x, y) and _clear_of(obstacles, x, y, 4),
                        7, (2.0, 3.5), (6.0, 11.0), start=len(obstacles) + 1)
    u = rng.uniform(-30, 30)                           # 停车场：起飞点这一侧、靠围栏
    park_c = [side * normal[0] * (half - 14) + across[0] * u, side * normal[1] * (half - 14) + across[1] * u]
    landmarks = [
        {"id": "bridge", "label": "桥头", "center": [round(v, 1) for v in bank(bridge_c, 2)], "source": "procedural"},
        {"id": "pier", "label": "码头", "center": [round(v, 1) for v in bank(pier_c, 0)], "shore": True, "source": "procedural"},
        {"id": "beach", "label": "河滩", "center": [round(v, 1) for v in bank(beach, 1)], "shore": True, "source": "procedural"},
        {"id": "parking", "label": "河边停车场", "center": [round(v, 1) for v in park_c], "source": "procedural"},
    ]
    yaw = 0.0 if vertical else math.pi / 2              # 桥垂直于河
    models = [deck_sdf("bridge", bridge_c, yaw, width + 8, 3.0, 0.8, (0.55, 0.55, 0.55)),
              deck_sdf("river_pier", [pier_c[0] + side * normal[0] * (width / 2 - 3), pier_c[1] + side * normal[1] * (width / 2 - 3)], yaw, 7, 2.2, 0.25)]   # 从岸边伸进水里
    walkable = lambda x, y: abs(x) < half - 4 and abs(y) < half - 4 and not in_water(x, y) and _clear_of(obstacles, x, y, 0.5)   # noqa: E731

    def ground(rng2: random.Random, extent: float) -> Image.Image:
        img = _ground(rng2)
        d, to_px = ImageDraw.Draw(img), _to_px(extent, img.size[0])
        size = img.size[0]
        d.line([to_px(*p) for p in path], fill=(176, 170, 150), width=max(2, int(3.0 / extent * size)))
        d.polygon([to_px(*p) for p in river], fill=(120, 105, 80))
        cx, cy = to_px(*park_c)
        r = 9 / extent * size
        d.rectangle([cx - r, cy - r * 0.7, cx + r, cy + r * 0.7], fill=(105, 105, 108))
        return img.filter(ImageFilter.GaussianBlur(0.8))
    return {"geofence": fence, "water": [river], "obstacles": obstacles, "landmarks": landmarks, "no_fly_zones": [],
            "walkable": walkable, "in_water": in_water, "models": models, "ground": ground, "bystanders": (5, 9),
            "drift": (across[0], across[1]),
            "callers": (["报案人（家长）", "河边钓鱼的人"], ["报案人（家属）", "河边钓鱼的人", "巡河员"])}


# ---------- 城市广场 ----------

def _rect(c, w, h, yaw):
    cs, sn = math.cos(yaw), math.sin(yaw)
    return [[round(c[0] + dx * cs - dy * sn, 2), round(c[1] + dx * sn + dy * cs, 2)] for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]


def plaza(rng: random.Random, half: float) -> dict:
    fence = [[-half, -half], [half, -half], [half, half], [-half, half]]
    buildings = []
    for k in range(rng.randint(5, 8)):
        for _ in range(60):
            a = rng.uniform(0, 2 * math.pi)
            r = rng.uniform(36, half - 14)
            c = [r * math.cos(a), r * math.sin(a)]
            w, h = rng.uniform(14, 30), rng.uniform(10, 22)
            yaw = rng.choice((0.0, math.pi / 2))
            poly = _rect(c, w, h, yaw)
            if all(abs(v) < half - 3 for p in poly for v in p) and all(math.dist(c, b["center"]) > b["radius"] + math.hypot(w, h) / 2 + 6 for b in buildings):
                buildings.append({"id": f"B{len(buildings) + 1}", "label": "建筑", "center": [round(c[0], 1), round(c[1], 1)],
                                  "radius": round(math.hypot(w, h) / 2, 1), "height": round(rng.uniform(12, 36), 1),
                                  "polygon": poly, "area_m2": round(w * h, 1)})
                break
    fountain = [0.0, 18.0]
    for _ in range(40):
        fountain = [round(rng.uniform(-12, 12), 1), round(rng.choice((-1, 1)) * rng.uniform(15, 24), 1)]
        if _clear_of(buildings, fountain[0], fountain[1], 6):
            break
    trees = _trees(rng, half, rng.randint(6, 10), lambda x, y: _clear_of(buildings, x, y, 4) and math.dist((x, y), fountain) > 7,
                   8, (1.8, 2.6), (6.0, 8.0), start=1)
    obstacles = buildings + trees
    mall = max(buildings, key=lambda b: b["area_m2"])
    d = math.hypot(*mall["center"]) or 1
    door = [mall["center"][0] * (1 - (mall["radius"] + 2) / d), mall["center"][1] * (1 - (mall["radius"] + 2) / d)]
    free = lambda x, y: _clear_of(obstacles, x, y, 3) and math.dist((x, y), fountain) > 6                    # noqa: E731
    metro = next(([x, y] for x, y in ((rng.uniform(-half + 10, half - 10), rng.uniform(-half + 10, half - 10)) for _ in range(80))
                  if free(x, y) and math.hypot(x, y) > 20 and math.dist((x, y), door) > 20), [-half + 10, 0.0])
    bus = next(([x, y] for x, y in ((rng.choice((-1, 1)) * (half - 6), rng.uniform(-half + 10, half - 10)) for _ in range(40))
                if free(x, y) and math.dist((x, y), metro) > 20), [half - 6, 0.0])
    landmarks = [
        {"id": "fountain", "label": "喷泉", "center": fountain, "source": "procedural"},
        {"id": "mall_door", "label": "商场正门", "center": [round(v, 1) for v in door], "source": "procedural"},
        {"id": "metro", "label": "地铁口", "center": [round(v, 1) for v in metro], "source": "procedural"},
        {"id": "bus_stop", "label": "公交站", "center": [round(v, 1) for v in bus], "source": "procedural"},
    ]
    models = [fountain_sdf(fountain), kiosk_sdf("metro_entrance", metro, 0.0), kiosk_sdf("bus_shelter", bus, 0.0, (0.6, 0.62, 0.6))]
    walkable = lambda x, y: abs(x) < half - 4 and abs(y) < half - 4 and _clear_of(obstacles, x, y, 0.8) \
        and math.dist((x, y), fountain) > 3.6                                                                 # noqa: E731

    def ground(rng2: random.Random, extent: float) -> Image.Image:
        size = 2048
        img = Image.new("RGB", (size, size), (168, 166, 160))
        dr, to_px = ImageDraw.Draw(img), _to_px(extent, size)
        step = 6 / extent * size                        # 6 米一格的铺装分缝
        for i in range(int(size / step) + 1):
            dr.line([(i * step, 0), (i * step, size)], fill=(150, 148, 142), width=2)
            dr.line([(0, i * step), (size, i * step)], fill=(150, 148, 142), width=2)
        noise = Image.effect_noise((size, size), 14).convert("RGB")
        img = Image.blend(img, noise, 0.08)
        for b in buildings:                              # 建筑底座的阴影色，卫星视角下更像楼
            ImageDraw.Draw(img).polygon([to_px(*p) for p in b["polygon"]], fill=(110, 110, 112))
        return img
    return {"geofence": fence, "water": [], "obstacles": obstacles, "landmarks": landmarks, "no_fly_zones": [],
            "walkable": walkable, "in_water": lambda x, y: False, "models": models, "ground": ground, "bystanders": (10, 16),
            "callers": (["报案人（家长）", "商场保安"], ["报案人（家属）", "商场保安", "一位路人"])}


BUILDERS = {"forest": forest, "riverside": riverside, "plaza": plaza}
