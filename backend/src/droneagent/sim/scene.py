"""巡检场景生成：光伏电站（多个阵列）、禁飞区、树木障碍，按随机种子在部分光伏板上放置缺陷。

输出到 WORLDS_DIR/<场景名>/：
  <场景名>.sdf   Gazebo 世界
  textures/      地面与光伏板贴图（缺陷画在贴图上）
  site.json      场地信息与标准答案：电子围栏、禁飞区、障碍物、每块板的位置、四角坐标和缺陷

缺陷类型：crack 裂纹、soiling 污损（鸟粪、积灰）、broken 玻璃破碎、missing 整块缺失。
同一种子生成的场景完全相同，评测可以复现。
"""
from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from .. import config
from ..geo import mat_vec, rpy_to_matrix

PANEL_W, PANEL_L, PANEL_T = 1.0, 1.7, 0.04    # 板宽（沿行）、板长（沿坡）、厚度，米
PANEL_GAP = 0.05
TILT = math.radians(25)                        # 朝南倾斜
PANEL_HEIGHT = 1.2                             # 板中心离地高度
TEX_W, TEX_H = 400, 680                        # 贴图像素：约 2.5 毫米 / 像素
DEFECT_TYPES = ("crack", "soiling", "broken", "missing")
DEFECT_LABELS = {"crack": "裂纹", "soiling": "污损", "broken": "玻璃破碎", "missing": "缺失"}
ORIGIN = {"lat": 47.397971057728974, "lon": 8.546163739800146, "alt": 488.0}   # 与 PX4 默认起飞点一致


@dataclass
class Panel:
    id: str
    array: str
    row: int
    col: int
    center: list[float]
    corners: list[list[float]]
    defect: dict | None = None
    distractor: str | bool | None = None   # 正常板上的干扰（阴影、反光），不算缺陷
    yaw: float = 0.0                        # 所在阵列的朝向（弧度，0 为正南朝向）


@dataclass
class Site:
    name: str
    seed: int
    origin: dict
    geofence: list[list[float]]          # 多边形（本地 x, y）
    max_altitude: float
    min_altitude: float
    no_fly_zones: list[dict]             # {id, label, center: [x, y], radius}
    obstacles: list[dict]                # {id, label, center: [x, y], radius, height}
    arrays: list[dict]                   # {id, center, rows, cols, size: [长, 宽]}
    panels: list[Panel] = field(default_factory=list)

    def defects(self) -> list[Panel]:
        return [p for p in self.panels if p.defect]


# ---------- 贴图 ----------

def _panel_base(rng: random.Random) -> Image.Image:
    """电池板：深蓝色电池片 6×10，银色边框与栅线，轻微的明暗变化。"""
    img = Image.new("RGB", (TEX_W, TEX_H), (190, 195, 200))
    d = ImageDraw.Draw(img)
    margin, cols, rows = 12, 6, 10
    cw, ch = (TEX_W - 2 * margin) / cols, (TEX_H - 2 * margin) / rows
    for r in range(rows):
        for c in range(cols):
            shade = rng.randint(-6, 6)
            x0, y0 = margin + c * cw + 2, margin + r * ch + 2
            d.rectangle([x0, y0, x0 + cw - 4, y0 + ch - 4], fill=(24 + shade, 36 + shade, 78 + shade))
            for k in (1, 2):   # 细栅线
                d.line([x0 + 2, y0 + k * ch / 3, x0 + cw - 6, y0 + k * ch / 3], fill=(70, 80, 120), width=1)
    return img


def _crack(img: Image.Image, rng: random.Random, severity: str) -> None:
    """轻微裂纹细而暗（约 5 毫米宽、对比度低），只有近照能看清；严重裂纹多分叉、发亮。"""
    d = ImageDraw.Draw(img)
    minor = severity == "minor"
    branches = rng.randint(1, 2) if minor else 5
    for _ in range(branches):
        x, y = rng.uniform(80, TEX_W - 80), rng.uniform(100, TEX_H - 100)
        angle = rng.uniform(0, 2 * math.pi)
        points = [(x, y)]
        for _ in range(rng.randint(4, 7) if minor else rng.randint(8, 12)):
            angle += rng.uniform(-0.6, 0.6)
            step = rng.uniform(15, 30)
            x, y = x + step * math.cos(angle), y + step * math.sin(angle)
            points.append((x, y))
        d.line(points, fill=(120, 132, 165) if minor else (225, 228, 232), width=2 if minor else 5)


def _soiling(img: Image.Image, rng: random.Random, severity: str) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    # 鸟粪、泥灰是不透明的；轻微污损只是面积小（与半透明的反光区分开）
    minor = severity == "minor"
    blobs = rng.randint(2, 4) if minor else rng.randint(10, 18)
    color = rng.choice([(235, 235, 225), (150, 120, 80)])        # 鸟粪 / 泥灰
    alpha = 235
    cx, cy = rng.uniform(80, TEX_W - 80), rng.uniform(100, TEX_H - 100)
    for _ in range(blobs):
        r = rng.uniform(6, 14) if minor else rng.uniform(20, 55)
        x, y = cx + rng.gauss(0, 40), cy + rng.gauss(0, 60)
        d.ellipse([x - r, y - r * rng.uniform(0.6, 1.2), x + r, y + r], fill=(*color, alpha))
    img.paste(Image.alpha_composite(img.convert("RGBA"), layer.filter(ImageFilter.GaussianBlur(3))).convert("RGB"))


def _broken(img: Image.Image, rng: random.Random, severity: str) -> None:
    d = ImageDraw.Draw(img)
    x0, y0 = rng.uniform(100, TEX_W - 100), rng.uniform(150, TEX_H - 150)
    spokes = 7 if severity == "minor" else 18
    reach = 55 if severity == "minor" else 220
    ends = []
    for k in range(spokes):
        a = 2 * math.pi * k / spokes + rng.uniform(-0.15, 0.15)
        length = rng.uniform(0.6, 1.0) * reach
        end = (x0 + length * math.cos(a), y0 + length * math.sin(a))
        ends.append(end)
        d.line([(x0, y0), end], fill=(215, 220, 228), width=3)
    for ring in (0.35, 0.7):
        pts = [(x0 + (e[0] - x0) * ring, y0 + (e[1] - y0) * ring) for e in ends]
        d.line(pts + [pts[0]], fill=(200, 205, 215), width=2)
    d.ellipse([x0 - 18, y0 - 18, x0 + 18, y0 + 18], fill=(170, 178, 190))


DRAW = {"crack": _crack, "soiling": _soiling, "broken": _broken}


def _distractor(img: Image.Image, rng: random.Random) -> str:
    """正常板上的干扰：树枝阴影或反光。远看像缺陷，近看能分辨，用来检验误报。"""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    kind = rng.choice(["shadow", "glare"])
    if kind == "shadow":
        for _ in range(rng.randint(3, 6)):
            x, y, r = rng.uniform(0, TEX_W), rng.uniform(0, TEX_H), rng.uniform(30, 80)
            d.ellipse([x - r, y - r * 0.5, x + r, y + r * 0.5], fill=(0, 0, 0, 90))
        blur = 18
    else:
        x, y, r = rng.uniform(100, TEX_W - 100), rng.uniform(150, TEX_H - 150), rng.uniform(60, 120)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, 70))
        blur = 30
    img.paste(Image.alpha_composite(img.convert("RGBA"), layer.filter(ImageFilter.GaussianBlur(blur))).convert("RGB"))
    return kind


def _ground(rng: random.Random, size: int = 2048) -> Image.Image:
    """草地底色加碎石小路的地面贴图（覆盖 160 米 × 160 米）。"""
    img = Image.new("RGB", (size, size), (92, 120, 64))
    d = ImageDraw.Draw(img)
    for _ in range(9000):
        x, y, r = rng.uniform(0, size), rng.uniform(0, size), rng.uniform(2, 7)
        g = rng.randint(-22, 22)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(92 + g, 120 + g, 64 + g // 2))
    return img.filter(ImageFilter.GaussianBlur(1.2))


# ---------- 场地布局 ----------

STANDOFF = 20.0          # 规划时默认的整列拍摄距离，生成布局时保证这个距离的拍摄点可用
MAX_PANELS = 120         # 单个场景的板数上限，控制拍摄与评测耗时
NFZ_LABELS = ["变电站", "民房", "高压线塔", "养殖场"]


def _inside(poly: list[list[float]], x: float, y: float) -> bool:
    inside, j = False, len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _edge_distance(poly: list[list[float]], x: float, y: float) -> float:
    best = float("inf")
    for i in range(len(poly)):
        (ax, ay), (bx, by) = poly[i], poly[(i + 1) % len(poly)]
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(x - (ax + t * dx), y - (ay + t * dy)))
    return best


def _array_size(rows: int, cols: int) -> tuple[float, float]:
    row_pitch = PANEL_L * math.cos(TILT) + 0.4
    return cols * (PANEL_W + PANEL_GAP) - PANEL_GAP, rows * row_pitch


def _viewpoint_xy(center: list[float], yaw: float, distance: float = STANDOFF) -> tuple[float, float]:
    """整列拍摄点的水平位置：沿板面法线（朝南偏 yaw）退后 distance 米。"""
    s = math.sin(TILT) * distance
    return center[0] + s * math.sin(yaw), center[1] - s * math.cos(yaw)


def _layout(rng: random.Random) -> dict:
    """完全随机的场地布局：围栏形状、禁飞区、阵列数量 / 位置 / 朝向 / 行列数、树的位置都随种子变化。

    用拒绝采样保证布局可飞：阵列互不重叠且在围栏内；每列在默认拍摄距离处的拍摄点在围栏内、
    远离禁飞区和树；起飞点不在阵列或禁飞区里。生成失败时换一个子种子重试。
    """
    for _ in range(200):
        # 围栏：以场地中心为圆心、半径随机起伏的六边形，起飞点 (0, 0) 位于其中靠边的位置
        cx, cy = rng.uniform(15, 35), rng.uniform(10, 30)
        sides = rng.randint(5, 7)
        base = rng.uniform(55, 70)
        start = rng.uniform(0, 2 * math.pi)
        fence = [[round(cx + base * rng.uniform(0.8, 1.15) * math.cos(start + 2 * math.pi * k / sides), 1),
                  round(cy + base * rng.uniform(0.8, 1.15) * math.sin(start + 2 * math.pi * k / sides), 1)] for k in range(sides)]
        if not _inside(fence, 0, 0) or _edge_distance(fence, 0, 0) < 6:
            continue
        # 禁飞区：1–2 个，离起飞点至少 20 米
        zones = []
        for k in range(rng.randint(1, 2)):
            for _ in range(50):
                zx, zy = rng.uniform(cx - base * 0.8, cx + base * 0.8), rng.uniform(cy - base * 0.8, cy + base * 0.8)
                r = rng.uniform(6, 11)
                if (_inside(fence, zx, zy) and math.hypot(zx, zy) > r + 20 and _edge_distance(fence, zx, zy) > 2
                        and all(math.dist((zx, zy), z["center"]) > z["radius"] + r + 10 for z in zones)):
                    zones.append({"id": f"NFZ-{k + 1}", "label": rng.choice(NFZ_LABELS), "center": [round(zx, 1), round(zy, 1)], "radius": round(r, 1)})
                    break
        # 阵列：2–6 列，行列数与朝向随机（朝南，偏角 ±30°）
        arrays, panels = [], 0
        target = rng.randint(2, 6)
        for _ in range(400):
            if len(arrays) >= target:
                break
            rows, cols = rng.randint(1, 3), rng.randint(6, 14)
            if panels + rows * cols > MAX_PANELS:
                continue
            w, length = _array_size(rows, cols)
            yaw = math.radians(rng.uniform(-30, 30))
            ax, ay = rng.uniform(cx - base, cx + base), rng.uniform(cy - base, cy + base)
            radius = math.hypot(w, length) / 2 + 2
            vx, vy = _viewpoint_xy([ax, ay], yaw)
            ok = (_inside(fence, ax, ay) and _edge_distance(fence, ax, ay) > radius + 2
                  and _inside(fence, vx, vy) and _edge_distance(fence, vx, vy) > 3
                  and math.hypot(ax, ay) > radius + 6
                  and all(math.dist((ax, ay), z["center"]) > z["radius"] + radius + 4 for z in zones)
                  and all(math.dist((vx, vy), z["center"]) > z["radius"] + 6 for z in zones)
                  and all(math.dist((ax, ay), a["center"]) > radius + a["radius"] + 4 for a in arrays)
                  and all(math.dist((vx, vy), a["center"]) > a["radius"] + 3 for a in arrays)
                  and all(math.dist(_viewpoint_xy(a["center"], math.radians(a["yaw_deg"])), (ax, ay)) > radius + 3 for a in arrays))
            if ok:
                arrays.append({"id": "ABCDEF"[len(arrays)], "center": [round(ax, 2), round(ay, 2)], "rows": rows, "cols": cols,
                               "yaw_deg": round(math.degrees(yaw), 1), "radius": round(radius, 2)})
                panels += rows * cols
        if len(arrays) < 2:
            continue
        # 树：3–8 棵，避开阵列、拍摄点、起飞点
        trees = []
        for _ in range(300):
            if len(trees) >= rng.randint(3, 8):
                break
            tx, ty = rng.uniform(cx - base, cx + base), rng.uniform(cy - base, cy + base)
            if (_inside(fence, tx, ty) and math.hypot(tx, ty) > 8
                    and all(math.dist((tx, ty), a["center"]) > a["radius"] + 4 for a in arrays)
                    and all(math.dist((tx, ty), _viewpoint_xy(a["center"], math.radians(a["yaw_deg"]))) > 6 for a in arrays)
                    and all(math.dist((tx, ty), z["center"]) > z["radius"] + 3 for z in zones)):
                trees.append({"id": f"T{len(trees) + 1}", "label": "树", "center": [round(tx, 1), round(ty, 1)],
                              "radius": round(rng.uniform(2, 3.2), 1), "height": round(rng.uniform(6, 11), 1)})
        return {"geofence": fence, "max_altitude": 40.0, "min_altitude": 5.0, "no_fly_zones": zones, "obstacles": trees,
                "arrays": arrays}
    raise RuntimeError("没能生成可飞的布局")


def _panels(arrays: list[dict], defect_rate: float, rng: random.Random, distractor_rate: float = 0.15) -> list[Panel]:
    row_pitch = PANEL_L * math.cos(TILT) + 0.4
    panels = []
    for a in arrays:
        yaw = math.radians(a.get("yaw_deg", 0.0))
        rotation = rpy_to_matrix(TILT, 0, yaw)
        heading = rpy_to_matrix(0, 0, yaw)
        total_w, total_l = _array_size(a["rows"], a["cols"])
        a["size"] = [round(total_w, 2), round(total_l, 2)]
        for r in range(a["rows"]):
            for c in range(a["cols"]):
                # 阵列坐标系中的偏移（x 沿行、y 朝北坡顶），按阵列朝向旋转到世界坐标
                dx = -total_w / 2 + PANEL_W / 2 + c * (PANEL_W + PANEL_GAP)
                dy = -total_l / 2 + row_pitch / 2 + r * row_pitch
                ox, oy, _ = mat_vec(heading, (dx, dy, 0.0))
                center = (a["center"][0] + ox, a["center"][1] + oy, PANEL_HEIGHT)
                corners = []
                for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                    off = mat_vec(rotation, (sx * PANEL_W / 2, sy * PANEL_L / 2, PANEL_T / 2))
                    corners.append([round(center[i] + off[i], 4) for i in range(3)])
                panels.append(Panel(id=f"{a['id']}-R{r + 1}-C{c + 1:02d}", array=a["id"], row=r + 1, col=c + 1,
                                    center=[round(v, 4) for v in center], corners=corners, yaw=round(yaw, 5)))
    count = max(1, round(len(panels) * defect_rate))
    weights = {"crack": 0.35, "soiling": 0.3, "broken": 0.25, "missing": 0.1}
    for p in rng.sample(panels, count):
        kind = rng.choices(list(weights), list(weights.values()))[0]
        p.defect = {"type": kind, "label": DEFECT_LABELS[kind],
                    "severity": "major" if kind == "missing" else rng.choice(["minor", "major"])}
    normal = [p for p in panels if not p.defect]
    for p in rng.sample(normal, round(len(normal) * distractor_rate)):
        p.distractor = True
    return panels


# ---------- SDF ----------

def _panel_model(p: Panel, texture: str) -> str:
    x, y, z = p.center
    frame = ""
    for i, (dx, dy, w, length) in enumerate(((0, PANEL_L / 2, PANEL_W, 0.04), (0, -PANEL_L / 2, PANEL_W, 0.04),
                                              (PANEL_W / 2, 0, 0.04, PANEL_L), (-PANEL_W / 2, 0, 0.04, PANEL_L))):
        frame += (f'<visual name="frame{i}"><pose>{dx} {dy} 0 0 0 0</pose><geometry><box><size>{w} {length} 0.05</size>'
                  f'</box></geometry><material><diffuse>0.75 0.76 0.78 1</diffuse><ambient>0.6 0.6 0.62 1</ambient></material></visual>')
    glass = "" if p.defect and p.defect["type"] == "missing" else (
        f'<visual name="glass"><geometry><box><size>{PANEL_W} {PANEL_L} {PANEL_T}</size></box></geometry>'
        f'<material><diffuse>1 1 1 1</diffuse><ambient>0.9 0.9 0.9 1</ambient><specular>0.3 0.3 0.3 1</specular>'
        f'<pbr><metal><albedo_map>{texture}</albedo_map><roughness>0.35</roughness><metalness>0.1</metalness></metal></pbr></material></visual>')
    return (f'<model name="panel_{p.id}"><static>true</static><pose>{x} {y} {z} {TILT} 0 {p.yaw}</pose>'
            f'<link name="link">{glass}{frame}</link></model>')


def _array_stand(a: dict) -> str:
    x, y = a["center"]
    w, length = a["size"]
    yaw = math.radians(a.get("yaw_deg", 0.0))
    return (f'<model name="stand_{a["id"]}"><static>true</static><pose>{x} {y} 0.45 0 0 {yaw:.5f}</pose><link name="link">'
            f'<visual name="beam"><geometry><box><size>{w} 0.12 0.9</size></box></geometry>'
            f'<material><diffuse>0.5 0.5 0.52 1</diffuse></material></visual>'
            f'<collision name="c"><geometry><box><size>{w} {length} 2.0</size></box></geometry></collision></link></model>')


def _tree(o: dict) -> str:
    x, y = o["center"]
    h = o["height"]
    return (f'<model name="tree_{o["id"]}"><static>true</static><pose>{x} {y} 0 0 0 0</pose><link name="link">'
            f'<visual name="trunk"><pose>0 0 {h * 0.3} 0 0 0</pose><geometry><cylinder><radius>0.3</radius><length>{h * 0.6}</length></cylinder></geometry>'
            f'<material><diffuse>0.35 0.25 0.15 1</diffuse></material></visual>'
            f'<visual name="crown"><pose>0 0 {h * 0.7} 0 0 0</pose><geometry><sphere><radius>{o["radius"]}</radius></sphere></geometry>'
            f'<material><diffuse>0.15 0.4 0.15 1</diffuse></material></visual>'
            f'<collision name="c"><pose>0 0 {h / 2} 0 0 0</pose><geometry><cylinder><radius>{o["radius"]}</radius><length>{h}</length></cylinder></geometry></collision>'
            f'</link></model>')


def _substation(z: dict) -> str:
    x, y = z["center"]
    return (f'<model name="nfz_{z["id"]}"><static>true</static><pose>{x} {y} 0 0 0 0</pose><link name="link">'
            f'<visual name="house"><pose>0 0 1.5 0 0 0</pose><geometry><box><size>6 4 3</size></box></geometry>'
            f'<material><diffuse>0.7 0.68 0.6 1</diffuse></material></visual>'
            f'<visual name="transformer"><pose>2 3.5 1 0 0 0</pose><geometry><box><size>2 1.5 2</size></box></geometry>'
            f'<material><diffuse>0.4 0.45 0.4 1</diffuse></material></visual></link></model>')


def _world_sdf(name: str, site: Site, textures: dict[str, str], ground: str, sun: tuple[float, float, float]) -> str:
    parts = [_panel_model(p, textures[p.id]) for p in site.panels]
    parts += [_array_stand(a) for a in site.arrays]
    parts += [_tree(o) for o in site.obstacles]
    parts += [_substation(z) for z in site.no_fly_zones]
    # 巡检机放在起飞点；PX4 以 PX4_GZ_MODEL_NAME 连接这个实例
    parts.append('<include><uri>model://x500_inspect</uri><name>x500_inspect_0</name><pose>0 0 0.25 0 0 0</pose></include>')
    o = site.origin
    xs, ys = [p[0] for p in site.geofence], [p[1] for p in site.geofence]
    gx, gy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    ground_size = max(max(xs) - min(xs), max(ys) - min(ys)) + 40     # 地面覆盖围栏外 20 米
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!-- 由 droneagent.sim.scene 生成：场景 {name}，种子 {site.seed}。不要手改，重新生成即可。 -->
<sdf version="1.9">
  <world name="{name}">
    <physics type="ode"><max_step_size>0.004</max_step_size><real_time_factor>1.0</real_time_factor><real_time_update_rate>250</real_time_update_rate></physics>
    <gravity>0 0 -9.8</gravity>
    <magnetic_field>6e-06 2.3e-05 -4.2e-05</magnetic_field>
    <atmosphere type="adiabatic"/>
    <scene><grid>false</grid><ambient>0.55 0.55 0.55 1</ambient><background>0.62 0.75 0.9 1</background><shadows>true</shadows></scene>
    <model name="ground_plane"><static>true</static><link name="link">
      <collision name="collision"><geometry><plane><normal>0 0 1</normal><size>1 1</size></plane></geometry></collision>
      <visual name="visual"><geometry><plane><normal>0 0 1</normal><size>{ground_size:.0f} {ground_size:.0f}</size></plane></geometry>
        <material><diffuse>1 1 1 1</diffuse><ambient>0.8 0.8 0.8 1</ambient><pbr><metal><albedo_map>{ground}</albedo_map><roughness>1</roughness></metal></pbr></material></visual>
    </link><pose>{gx:.1f} {gy:.1f} 0 0 0 0</pose></model>
    <light name="sun" type="directional"><pose>0 0 500 0 0 0</pose><cast_shadows>true</cast_shadows><intensity>1</intensity>
      <direction>{sun[0]:.3f} {sun[1]:.3f} {sun[2]:.3f}</direction><diffuse>0.95 0.95 0.92 1</diffuse><specular>0.3 0.3 0.3 1</specular></light>
    {chr(10).join("    " + p for p in parts)}
    <spherical_coordinates><surface_model>EARTH_WGS84</surface_model><world_frame_orientation>ENU</world_frame_orientation>
      <latitude_deg>{o["lat"]}</latitude_deg><longitude_deg>{o["lon"]}</longitude_deg><elevation>{o["alt"]}</elevation></spherical_coordinates>
  </world>
</sdf>
'''


def generate(seed: int, name: str | None = None, defect_rate: float = 0.12, out_dir: Path | None = None,
             textures: bool = True) -> Path:
    """生成场景，返回场景目录。textures=False 时只生成场地信息（用于布局评测，快）。"""
    name = name or f"solar_{seed}"
    rng = random.Random(seed)
    out = (out_dir or config.WORLDS_DIR) / name
    tex_dir = out / "textures"
    (tex_dir if textures else out).mkdir(parents=True, exist_ok=True)
    layout = _layout(rng)
    site = Site(name=name, seed=seed, origin=ORIGIN, geofence=layout["geofence"], max_altitude=layout["max_altitude"],
                min_altitude=layout["min_altitude"], no_fly_zones=layout["no_fly_zones"], obstacles=layout["obstacles"],
                arrays=layout["arrays"])
    site.panels = _panels(site.arrays, defect_rate, rng)
    if not textures:
        (out / "site.json").write_text(json.dumps(asdict(site), ensure_ascii=False, indent=1), encoding="utf-8")
        return out

    ground = tex_dir / "ground.png"
    _ground(rng).save(ground)
    normal = tex_dir / "panel_normal.png"
    _panel_base(random.Random(seed * 7 + 1)).save(normal)
    tex_paths = {}
    for p in site.panels:
        if p.defect and p.defect["type"] in DRAW:
            img = _panel_base(rng)
            DRAW[p.defect["type"]](img, rng, p.defect["severity"])
        elif p.distractor:
            img = _panel_base(rng)
            p.distractor = _distractor(img, rng)
        else:
            tex_paths[p.id] = str(normal)
            continue
        path = tex_dir / f"panel_{p.id}.png"
        img.save(path)
        tex_paths[p.id] = str(path)

    azimuth, elevation = rng.uniform(0, 2 * math.pi), rng.uniform(math.radians(35), math.radians(70))
    sun = (math.cos(elevation) * math.sin(azimuth), math.cos(elevation) * math.cos(azimuth), -math.sin(elevation))
    (out / f"{name}.sdf").write_text(_world_sdf(name, site, tex_paths, str(ground), sun), encoding="utf-8")
    data = asdict(site)
    (out / "site.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def load_site(name: str) -> dict:
    return json.loads((config.WORLDS_DIR / name / "site.json").read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="生成光伏巡检场景")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--name")
    parser.add_argument("--defect-rate", type=float, default=0.12)
    args = parser.parse_args()
    config.ensure_dirs()
    out = generate(args.seed, args.name, args.defect_rate)
    site = load_site(out.name)
    print(f"已生成 {out}：{len(site['panels'])} 块板，缺陷 {sum(1 for p in site['panels'] if p['defect'])} 块")


if __name__ == "__main__":
    main()
