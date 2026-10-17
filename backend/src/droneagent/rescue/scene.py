"""应急救援场景生成：公园（湖面、步道、树、地标）、走失目标、路人、干扰衣物。

与光伏场景共用的部分（围栏、限高、禁飞区、障碍物、起飞点、真实场地的地物与高程）字段名保持一致，
安全员、航线绕行、网页地图都不用改。人员的真实位置与路线写在 site.json 的 people 里（网页接口会去掉），
走动由相机桥按 crowd.json 执行（见 sim/camera_bridge.py）。
"""
from __future__ import annotations

import io
import json
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from .. import config
from ..sim.scene import ORIGIN, _ground, _inside, obstacle_sdf

COLORS = {"红色": (0.80, 0.10, 0.10), "蓝色": (0.12, 0.25, 0.75), "白色": (0.92, 0.92, 0.92), "黑色": (0.08, 0.08, 0.08),
          "黄色": (0.95, 0.80, 0.10), "绿色": (0.15, 0.55, 0.20), "灰色": (0.50, 0.50, 0.52), "橙色": (0.95, 0.45, 0.08),
          "紫色": (0.45, 0.20, 0.60), "粉色": (0.95, 0.55, 0.70)}
BOTTOMS = ["蓝色", "黑色", "灰色", "白色", "绿色"]
SKIN = (0.85, 0.66, 0.52)
HAIR = (0.08, 0.06, 0.05)
STATES = ("walking", "sitting", "water")
STATE_LABELS = {"walking": "走动", "sitting": "静止", "water": "落水"}
HALF = 70.0                     # 随机场地围栏半边长（米）
MAX_ALT = 40.0


# ---------- 人物模型（程序生成的人形，不依赖外部模型） ----------

def _box(name: str, pose: tuple, size: tuple, rgb: tuple) -> str:
    return (f'<visual name="{name}"><pose>{" ".join(f"{v:.3f}" for v in pose)}</pose><geometry><box><size>{size[0]:.3f} {size[1]:.3f} {size[2]:.3f}</size></box></geometry>'
            f'<material><diffuse>{rgb[0]} {rgb[1]} {rgb[2]} 1</diffuse><ambient>{rgb[0] * 0.6:.2f} {rgb[1] * 0.6:.2f} {rgb[2] * 0.6:.2f} 1</ambient></material></visual>')


def person_sdf(p: dict) -> str:
    """站立或行走：腿、躯干、手臂、头、头发；按身高缩放。落水与坐姿通过模型高度偏移表现（见 crowd 初始位姿）。"""
    h = p["height"]
    s = h / 1.7
    top, bottom = COLORS[p["top"]], COLORS[p["bottom"]]
    sitting = p["state"] == "sitting"
    leg_h = 0.0 if sitting else 0.80 * s
    torso_z = (0.25 if sitting else leg_h) + 0.30 * s
    parts = []
    if sitting:   # 坐在地上：腿向前伸
        parts += [_box("leg_l", (0.25 * s, 0.09 * s, 0.08 * s, 0, 0, 0), (0.70 * s, 0.13 * s, 0.13 * s), bottom),
                  _box("leg_r", (0.25 * s, -0.09 * s, 0.08 * s, 0, 0, 0), (0.70 * s, 0.13 * s, 0.13 * s), bottom)]
    else:
        parts += [_box("leg_l", (0, 0.09 * s, leg_h / 2, 0, 0, 0), (0.14 * s, 0.13 * s, leg_h), bottom),
                  _box("leg_r", (0, -0.09 * s, leg_h / 2, 0, 0, 0), (0.14 * s, 0.13 * s, leg_h), bottom)]
    parts += [_box("torso", (0, 0, torso_z, 0, 0, 0), (0.24 * s, 0.42 * s, 0.60 * s), top),
              _box("arm_l", (0, 0.26 * s, torso_z - 0.02, 0.15, 0, 0), (0.10 * s, 0.10 * s, 0.58 * s), top),
              _box("arm_r", (0, -0.26 * s, torso_z - 0.02, -0.15, 0, 0), (0.10 * s, 0.10 * s, 0.58 * s), top)]
    head_z = torso_z + 0.30 * s + 0.12 * s
    parts.append(f'<visual name="head"><pose>0 0 {head_z:.3f} 0 0 0</pose><geometry><sphere><radius>{0.115 * s:.3f}</radius></sphere></geometry>'
                 f'<material><diffuse>{SKIN[0]} {SKIN[1]} {SKIN[2]} 1</diffuse></material></visual>')
    parts.append(f'<visual name="hair"><pose>0 0 {head_z + 0.04 * s:.3f} 0 0 0</pose><geometry><sphere><radius>{0.105 * s:.3f}</radius></sphere></geometry>'
                 f'<material><diffuse>{HAIR[0]} {HAIR[1]} {HAIR[2]} 1</diffuse></material></visual>')
    x, y, z = p["start"][0], p["start"][1], p.get("z", 0.0)
    return (f'<model name="{p["model"]}"><static>false</static><pose>{x:.2f} {y:.2f} {z:.2f} 0 0 {p.get("yaw", 0):.2f}</pose>'
            f'<link name="body"><gravity>false</gravity><kinematic>true</kinematic>{"".join(parts)}</link></model>')


def decoy_sdf(d: dict) -> str:
    """地上的衣物或背包（没有人）：最常见的误报来源。"""
    rgb = COLORS[d["color"]]
    x, y = d["center"]
    return (f'<model name="decoy_{d["id"]}"><static>true</static><pose>{x:.2f} {y:.2f} 0.04 0 0 {d["yaw"]:.2f}</pose><link name="l">'
            + _box("cloth", (0, 0, 0, 0, 0, 0), (0.55, 0.40, 0.08), rgb) + '</link></model>')


def water_sdf(i: int, poly: list[list[float]]) -> str:
    pts = "".join(f"<point>{x:.2f} {y:.2f}</point>" for x, y in poly)
    return (f'<model name="water_{i}"><static>true</static><pose>0 0 0.05 0 0 0</pose><link name="l"><visual name="v">'
            f'<geometry><polyline>{pts}<height>0.02</height></polyline></geometry>'
            f'<material><diffuse>0.10 0.30 0.38 1</diffuse><ambient>0.06 0.18 0.24 1</ambient><specular>0.4 0.4 0.4 1</specular></material>'
            f'<transparency>0.15</transparency></visual></link></model>')


def pier_sdf(c: list[float], yaw: float) -> str:
    return (f'<model name="pier"><static>true</static><pose>{c[0]:.2f} {c[1]:.2f} 0.25 0 0 {yaw:.2f}</pose><link name="l">'
            + _box("deck", (4, 0, 0, 0, 0, 0), (8, 2.2, 0.2), (0.45, 0.32, 0.20)) + '</link></model>')


def pavilion_sdf(c: list[float]) -> str:
    return (f'<model name="pavilion"><static>true</static><pose>{c[0]:.2f} {c[1]:.2f} 0 0 0 0</pose><link name="l">'
            + _box("roof", (0, 0, 3.0, 0, 0, 0), (4.5, 4.5, 0.3), (0.55, 0.15, 0.12))
            + "".join(_box(f"post{k}", (sx * 1.9, sy * 1.9, 1.4, 0, 0, 0), (0.25, 0.25, 2.8), (0.5, 0.4, 0.3)) for k, (sx, sy) in enumerate([(1, 1), (1, -1), (-1, 1), (-1, -1)]))
            + '</link></model>')


# ---------- 布局 ----------

def _blob(rng: random.Random, cx: float, cy: float, r: float, n: int = 18) -> list[list[float]]:
    """不规则的湖面轮廓。"""
    ph = [rng.uniform(0, 2 * math.pi) for _ in range(3)]
    out = []
    for k in range(n):
        t = 2 * math.pi * k / n
        rr = r * (1 + 0.18 * math.sin(2 * t + ph[0]) + 0.10 * math.sin(3 * t + ph[1]) + 0.06 * math.sin(5 * t + ph[2]))
        out.append([round(cx + rr * math.cos(t), 2), round(cy + rr * 0.75 * math.sin(t), 2)])
    return out


def _nearest_shore(poly: list[list[float]], x: float, y: float) -> tuple[list[float], float]:
    """多边形边界上离 (x, y) 最近的点，以及该点指向湖心外侧的方向角。"""
    best, best_d, best_n = None, 1e9, 0.0
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        dx, dy = x2 - x1, y2 - y1
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy + 1e-9)))
        px, py = x1 + t * dx, y1 + t * dy
        d = math.hypot(px - x, py - y)
        if d < best_d:
            best, best_d, best_n = [px, py], d, math.atan2(-dx, dy)
    return best, best_n


def _walk_path(rng: random.Random, start: tuple[float, float], ok, steps: int, step_len: float) -> list[list[float]]:
    """在可走区域内的随机折线（不进水、不出围栏）。"""
    pts, (x, y) = [[round(start[0], 2), round(start[1], 2)]], start
    heading = rng.uniform(0, 2 * math.pi)
    for _ in range(steps):
        for _try in range(12):
            h = heading + rng.uniform(-0.9, 0.9)
            nx, ny = x + step_len * math.cos(h), y + step_len * math.sin(h)
            if ok(nx, ny):
                x, y, heading = nx, ny, h
                pts.append([round(x, 2), round(y, 2)])
                break
        else:
            heading += math.pi
    return pts


def _procedural_landmarks(rng: random.Random, water: list, in_water, obstacles: list) -> list[dict]:
    """随机场地的地标：这些地物在仿真里真的建了模型（码头、凉亭），或在围栏边上（门）。"""
    landmarks = []
    if water:
        shore, normal = _nearest_shore(max(water, key=len), 0.0, 0.0)
        landmarks.append({"id": "pier", "label": "码头", "center": [round(shore[0], 1), round(shore[1], 1)], "yaw": normal + math.pi,
                          "shore": True, "source": "procedural"})
    candidates = {"east_gate": ("东门", [HALF - 3, rng.uniform(-30, 30)]), "west_gate": ("西门", [-HALF + 3, rng.uniform(-30, 30)]),
                  "playground": ("儿童游乐场", None), "pavilion": ("凉亭", None)}
    for key, (label, c) in candidates.items():
        if c is None:
            for _ in range(60):
                x, y = rng.uniform(-HALF + 12, HALF - 12), rng.uniform(-HALF + 12, HALF - 12)
                if not in_water(x, y) and all(math.hypot(x - o["center"][0], y - o["center"][1]) > 6 for o in obstacles) \
                        and all(math.hypot(x - l["center"][0], y - l["center"][1]) > 25 for l in landmarks):
                    c = [round(x, 1), round(y, 1)]
                    break
        if c:
            landmarks.append({"id": key, "label": label, "center": c, "source": "procedural"})
    return landmarks


DIRECTIONS = [("north", "场地北侧", (0, 1)), ("south", "场地南侧", (0, -1)), ("east", "场地东侧", (1, 0)), ("west", "场地西侧", (-1, 0))]


def _real_landmarks(twin: dict, water: list, in_water) -> list[dict]:
    """真实场地的地标：只用 OpenStreetMap 里真实存在的地物（twin["pois"]）；不够时补“水边”和方位描述，
    不再程序生成凉亭、码头这类现场可能没有的东西（第三轮反馈：真实场地出现了不存在的凉亭）。"""
    lim = HALF - 3
    landmarks = [l for l in twin.get("pois", []) if abs(l["center"][0]) < lim and abs(l["center"][1]) < lim][:6]
    if water and not any(l.get("shore") for l in landmarks):
        shore, _ = _nearest_shore(max(water, key=len), 0.0, 0.0)
        if abs(shore[0]) < lim and abs(shore[1]) < lim:
            landmarks.append({"id": "shore", "label": "水边", "center": [round(shore[0], 1), round(shore[1], 1)],
                              "shore": True, "source": "osm"})
    for key, label, (dx, dy) in DIRECTIONS:
        if len(landmarks) >= 3:
            break
        c = [dx * (HALF - 12), dy * (HALF - 12)]
        if not in_water(*c):
            landmarks.append({"id": key, "label": label, "center": c, "source": "direction"})
    return landmarks


def _layout(rng: random.Random, twin: dict | None) -> dict:
    if twin:
        fence = [[-HALF, -HALF], [HALF, -HALF], [HALF, HALF], [-HALF, HALF]]
        water = [f["polygon"] for f in twin["features"] if f["kind"] == "water"]
        obstacles = list(twin["obstacles"])          # 安全员需要全部障碍物，不能截断（审查二 H02）
    else:
        fence = [[-HALF, -HALF], [HALF, -HALF], [HALF, HALF], [-HALF, HALF]]
        cx, cy = rng.uniform(-15, 25), rng.uniform(5, 35)
        water = [_blob(rng, cx, cy, rng.uniform(22, 30))]
        obstacles = []
    in_water = lambda x, y: any(_inside(w, x, y) for w in water)                                    # noqa: E731
    inside = lambda x, y, m=4: abs(x) < HALF - m and abs(y) < HALF - m                               # noqa: E731
    if not twin:
        for k in range(rng.randint(14, 22)):          # 树（障碍物）
            for _ in range(40):
                x, y = rng.uniform(-HALF + 6, HALF - 6), rng.uniform(-HALF + 6, HALF - 6)
                if not in_water(x, y) and math.hypot(x, y) > 12 and all(math.hypot(x - o["center"][0], y - o["center"][1]) > 7 for o in obstacles):
                    h = round(rng.uniform(6, 12), 1)
                    obstacles.append({"id": f"T{k + 1}", "label": "树", "center": [round(x, 1), round(y, 1)], "radius": round(rng.uniform(2, 3.5), 1), "height": h})
                    break
    if twin:
        landmarks = _real_landmarks(twin, water, in_water)
    else:
        landmarks = _procedural_landmarks(rng, water, in_water, obstacles)
    # 禁飞区：随机场地放一个（模拟变电站）
    nfz = []
    if not twin:
        for _ in range(40):
            x, y = rng.uniform(-HALF + 15, HALF - 15), rng.uniform(-HALF + 15, HALF - 15)
            if not in_water(x, y) and math.hypot(x, y) > 30 and all(math.hypot(x - l["center"][0], y - l["center"][1]) > 18 for l in landmarks):
                nfz.append({"id": "Z1", "label": "变电站", "center": [round(x, 1), round(y, 1)], "radius": 8.0})
                break
    return {"geofence": fence, "water": water, "obstacles": obstacles, "landmarks": landmarks, "no_fly_zones": nfz,
            "walkable": lambda x, y: inside(x, y) and not in_water(x, y) and all(math.hypot(x - z["center"][0], y - z["center"][1]) > z["radius"] for z in nfz)
            and all(math.hypot(x - o["center"][0], y - o["center"][1]) > o["radius"] + 0.5 for o in obstacles),
            "in_water": in_water}


def _people(rng: random.Random, lay: dict) -> tuple[list[dict], list[dict], dict]:
    ok, in_water = lay["walkable"], lay["in_water"]
    state = rng.choice(STATES) if lay["water"] else rng.choice(("walking", "sitting"))
    pier = [l for l in lay["landmarks"] if l.get("shore")]
    last_seen = pier[0] if state == "water" and pier else rng.choice(lay["landmarks"])     # 落水的人最后出现在水边
    child = rng.random() < 0.6
    target = {"id": "target", "model": "person_target", "role": "target", "state": state, "child": child,
              "height": round(rng.uniform(1.10, 1.30) if child else rng.uniform(1.60, 1.80), 2),
              "top": rng.choice(["红色", "黄色", "橙色", "粉色", "蓝色", "绿色", "白色"]), "bottom": rng.choice(BOTTOMS)}
    lx, ly = last_seen["center"]
    if state == "water":
        poly = max(lay["water"], key=len)
        shore, normal = _nearest_shore(poly, lx, ly)
        inward = normal + math.pi
        dist = rng.uniform(3, 8)
        sx, sy = shore[0] + dist * math.cos(inward), shore[1] + dist * math.sin(inward)
        if not in_water(sx, sy):
            sx, sy = shore[0] - dist * math.cos(inward), shore[1] - dist * math.sin(inward)
        target.update(start=[round(sx, 2), round(sy, 2)], z=round(-target["height"] * 0.80, 2), speed=0.08,
                      path=_walk_path(rng, (sx, sy), in_water, 6, 2.0))
    else:
        for _ in range(80):
            a, r = rng.uniform(0, 2 * math.pi), rng.uniform(10, 40)
            sx, sy = lx + r * math.cos(a), ly + r * math.sin(a)
            if ok(sx, sy):
                break
        speed = rng.uniform(0.3, 0.6) if state == "walking" else 0.0
        target.update(start=[round(sx, 2), round(sy, 2)], z=0.0, speed=round(speed, 2),
                      path=_walk_path(rng, (sx, sy), ok, 12, 4.0) if state == "walking" else [[round(sx, 2), round(sy, 2)]])
    people = [target]
    for k in range(rng.randint(5, 10)):     # 路人
        for _ in range(80):
            sx, sy = rng.uniform(-HALF + 6, HALF - 6), rng.uniform(-HALF + 6, HALF - 6)
            if ok(sx, sy):
                break
        same_top = rng.random() < 0.3        # 约三成路人上衣与目标同色（必须靠裤子、年龄区分）
        kid = rng.random() < 0.25
        bottom = rng.choice(BOTTOMS)
        if same_top and bottom == target["bottom"] and kid == child:
            bottom = rng.choice([b for b in BOTTOMS if b != target["bottom"]])
        moving = rng.random() < 0.75
        people.append({"id": f"B{k + 1}", "model": f"person_b{k + 1}", "role": "bystander", "state": "walking" if moving else "sitting",
                       "child": kid, "height": round(rng.uniform(1.10, 1.30) if kid else rng.uniform(1.55, 1.85), 2),
                       "top": target["top"] if same_top else rng.choice([c for c in COLORS if c != target["top"]]), "bottom": bottom,
                       "start": [round(sx, 2), round(sy, 2)], "z": 0.0, "speed": round(rng.uniform(1.0, 1.4), 2) if moving else 0.0,
                       "path": _walk_path(rng, (sx, sy), ok, 14, 6.0) if moving else [[round(sx, 2), round(sy, 2)]]})
    decoys = []
    for k in range(rng.randint(1, 2)):      # 与目标上衣同色的衣物或背包
        for _ in range(80):
            x, y = rng.uniform(-HALF + 8, HALF - 8), rng.uniform(-HALF + 8, HALF - 8)
            if ok(x, y):
                decoys.append({"id": f"D{k + 1}", "color": target["top"], "center": [round(x, 2), round(y, 2)], "yaw": round(rng.uniform(0, 3.14), 2)})
                break
    brief = {"last_seen": last_seen["id"], "last_seen_label": last_seen["label"], "description": describe(target),
             "incident": incident(rng, target, lay, last_seen)}
    return people, decoys, brief


SIMILAR = {"红色": "橙色", "橙色": "红色", "蓝色": "紫色", "紫色": "蓝色", "白色": "灰色", "灰色": "白色", "黄色": "橙色", "粉色": "红色", "绿色": "蓝色", "黑色": "灰色"}


def incident(rng: random.Random, target: dict, lay: dict, last_seen: dict) -> dict:
    """报案人说的话（警情）：现实中特征来自报案人，常常不准——记错颜色深浅、记不清裤子、位置只记得大概。
    这些误差是有意加入的，用来检验系统在描述不准时的表现。真实答案在 site["brief"]（网页接口不返回）。"""
    top, bottom, place, notes = target["top"], target["bottom"], last_seen, []
    if rng.random() < 0.12:
        top = SIMILAR.get(top, top)
        notes.append("上衣颜色记成了相近的颜色")
    r = rng.random()
    if r < 0.30:
        bottom = None
        notes.append("记不清裤子颜色")
    elif r < 0.42:
        bottom = rng.choice([b for b in BOTTOMS if b != bottom])
        notes.append("裤子颜色记错")
    others = [l for l in lay["landmarks"] if l["id"] != last_seen["id"]]
    if others and rng.random() < 0.2 and target["state"] != "water":
        place = min(others, key=lambda l: math.dist(l["center"], last_seen["center"]))
        notes.append("位置说成了附近的另一个地标")
    who = rng.choice(["我家孩子", "我儿子", "我女儿", "一个小孩"]) if target["child"] else rng.choice(["我老公", "我同事", "我哥", "一位游客"])
    caller = rng.choice(["报案人（家属）", "报案人（路人）", "公园管理员"]) if not target["child"] else rng.choice(["报案人（家长）", "公园管理员"])
    minutes = rng.choice([15, 20, 30, 40, 60])
    clothes = f"穿{top}上衣" + (f"、{bottom}裤子" if bottom else "，裤子记不清了")
    age = "大概七八岁" if target["child"] else "三四十岁"
    if target["state"] == "water":
        text = f"{caller}：有人看到{who}在{place['label']}那边掉进水里了！{age}，{clothes}，就是{minutes}分钟内的事，快！"
    else:
        text = f"{caller}：{who}不见了，{age}，{clothes}，大约{minutes}分钟前在{place['label']}附近，之后就找不到了。"
    return {"text": text, "minutes": minutes, "reported": {"top": top, "bottom": bottom, "child": target["child"], "last_seen": place["id"]},
            "errors": notes}


def describe(t: dict) -> str:
    who = "小孩" if t["child"] else "成年人"
    age = "大约 8 岁" if t["child"] else "三四十岁"
    return f"{who}，{age}，穿{t['top']}上衣、{t['bottom']}裤子"


# ---------- 地面 ----------

def satellite_ground(origin: dict, extent: float, path: Path) -> bool:
    """真实场地：把该位置的卫星影像（Esri World Imagery，WGS-84 网页墨卡托）拼成以起飞点为中心的地面贴图。
    失败时返回 False，由调用方改用程序生成的草地。"""
    import httpx
    z = 19
    R = 6378137.0

    def px(lat, lon):
        n = 256 * 2 ** z
        s = math.sin(math.radians(lat))
        return (lon + 180) / 360 * n, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    m_per_px = 2 * math.pi * R * math.cos(math.radians(origin["lat"])) / (256 * 2 ** z)
    cx, cy = px(origin["lat"], origin["lon"])
    half_px = extent / 2 / m_per_px
    x0, y0, x1, y1 = cx - half_px, cy - half_px, cx + half_px, cy + half_px
    canvas = Image.new("RGB", (int(x1 - x0), int(y1 - y0)))
    try:
        with httpx.Client(timeout=10, headers={"User-Agent": "drone-agent/1.0 (simulation research)"}) as client:
            for tx in range(int(x0 // 256), int(x1 // 256) + 1):
                for ty in range(int(y0 // 256), int(y1 // 256) + 1):
                    r = client.get(f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{ty}/{tx}")
                    r.raise_for_status()
                    canvas.paste(Image.open(io.BytesIO(r.content)).convert("RGB"), (int(tx * 256 - x0), int(ty * 256 - y0)))
    except Exception:       # noqa: BLE001  网络不可用时退回程序生成的地面
        return False
    canvas.resize((2048, 2048), Image.LANCZOS).save(path)
    return True


def _park_ground(rng: random.Random, lay: dict, extent: float) -> Image.Image:
    """随机场地：草地 + 步道（连接地标）+ 湖岸泥土。"""
    img = _ground(rng)
    size = img.size[0]
    d = ImageDraw.Draw(img)
    to_px = lambda x, y: ((x + extent / 2) / extent * size, (extent / 2 - y) / extent * size)   # noqa: E731
    pts = [l["center"] for l in lay["landmarks"]]
    for a in pts:
        for b in pts:
            if a < b:
                d.line([to_px(*a), to_px(*b)], fill=(176, 160, 128), width=int(2.2 / extent * size))
    for w in lay["water"]:
        d.polygon([to_px(*p) for p in w], fill=(110, 95, 70))
    return img.filter(ImageFilter.GaussianBlur(0.8))


# ---------- 生成 ----------

def generate(seed: int, name: str | None = None, location: dict | None = None, out_dir: Path | None = None) -> Path:
    from ..sim.scene import _twin
    name = name or f"rescue_{seed}"
    rng = random.Random(seed)
    out = (out_dir or config.WORLDS_DIR) / name
    (out / "textures").mkdir(parents=True, exist_ok=True)
    twin = _twin(location) if location else None
    if twin:
        from .. import twin as twin_data
        twin["pois"] = twin_data.pois(twin["origin"], HALF)
    lay = _layout(rng, twin)
    people, decoys, brief = _people(rng, lay)
    origin = twin["origin"] if twin else ORIGIN
    extent = 2 * HALF + 40
    ground = out / "textures" / "ground.png"
    source = "procedural"
    if twin and satellite_ground(origin, extent, ground):
        source = "satellite"
    else:
        _park_ground(rng, lay, extent).save(ground)
    features = (twin["features"] if twin else [{"kind": "water", "polygon": w} for w in lay["water"]])
    site = {"name": name, "seed": seed, "scenario": "rescue", "origin": origin, "geofence": lay["geofence"],
            "max_altitude": MAX_ALT, "min_altitude": 8.0, "no_fly_zones": lay["no_fly_zones"], "obstacles": lay["obstacles"],
            "arrays": [], "panels": [], "landmarks": lay["landmarks"], "location": location, "features": features,
            "terrain": twin["terrain"] if twin else None, "twin": twin["summary"] if twin else None,
            "ground": {"source": source, "extent_m": extent,
                       "note": "地面为该位置的卫星影像（Esri World Imagery）" if source == "satellite" else "仿真地面为程序生成的草地、步道与湖岸"},
            "people": people, "decoys": decoys, "brief": brief,
            "incident": {"text": brief["incident"]["text"], "minutes": brief["incident"]["minutes"]}}   # 网页可见的只有报案人说的话
    azimuth, elevation = rng.uniform(0, 2 * math.pi), rng.uniform(math.radians(40), math.radians(70))
    sun = (math.cos(elevation) * math.sin(azimuth), math.cos(elevation) * math.cos(azimuth), -math.sin(elevation))
    site["sun"] = [round(v, 4) for v in sun]          # 太阳方向（指向地面），用于排除无人机自己的影子
    (out / f"{name}.sdf").write_text(_world_sdf(name, site, lay, str(ground), extent, sun), encoding="utf-8")
    crowd = [{"model": p["model"], "path": p["path"], "speed": p["speed"], "z": p.get("z", 0.0)} for p in people if p["speed"] > 0]
    (out / "crowd.json").write_text(json.dumps(crowd, ensure_ascii=False), encoding="utf-8")
    (out / "site.json").write_text(json.dumps(site, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _world_sdf(name: str, site: dict, lay: dict, ground: str, extent: float, sun: tuple) -> str:
    parts = [person_sdf(p) for p in site["people"]] + [decoy_sdf(d) for d in site["decoys"]]
    parts += [water_sdf(i, w) for i, w in enumerate(lay["water"])]
    parts += [obstacle_sdf(o) for o in site["obstacles"]]
    for l in site["landmarks"]:
        if l["id"] == "pier":
            parts.append(pier_sdf(l["center"], l.get("yaw", 0.0)))
        elif l["id"] == "pavilion":
            parts.append(pavilion_sdf(l["center"]))
    parts.append('<include><uri>model://x500_inspect</uri><name>x500_inspect_0</name><pose>0 0 0.25 0 0 0</pose></include>')
    o = site["origin"]
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!-- 由 droneagent.rescue.scene 生成：救援场景 {name}，种子 {site["seed"]}。不要手改，重新生成即可。 -->
<sdf version="1.9">
  <world name="{name}">
    <physics type="ode"><max_step_size>0.004</max_step_size><real_time_factor>1.0</real_time_factor><real_time_update_rate>250</real_time_update_rate></physics>
    <gravity>0 0 -9.8</gravity>
    <magnetic_field>6e-06 2.3e-05 -4.2e-05</magnetic_field>
    <atmosphere type="adiabatic"/>
    <scene><grid>false</grid><ambient>0.6 0.6 0.6 1</ambient><background>0.62 0.75 0.9 1</background><shadows>true</shadows></scene>
    <model name="ground_plane"><static>true</static><link name="link">
      <collision name="collision"><geometry><plane><normal>0 0 1</normal><size>1 1</size></plane></geometry></collision>
      <visual name="visual"><geometry><plane><normal>0 0 1</normal><size>{extent:.0f} {extent:.0f}</size></plane></geometry>
        <material><diffuse>1 1 1 1</diffuse><ambient>0.8 0.8 0.8 1</ambient><pbr><metal><albedo_map>{ground}</albedo_map><roughness>1</roughness></metal></pbr></material></visual>
    </link></model>
    <light name="sun" type="directional"><pose>0 0 500 0 0 0</pose><cast_shadows>true</cast_shadows><intensity>1</intensity>
      <direction>{sun[0]:.3f} {sun[1]:.3f} {sun[2]:.3f}</direction><diffuse>0.95 0.95 0.92 1</diffuse><specular>0.3 0.3 0.3 1</specular></light>
    {chr(10).join("    " + p for p in parts)}
    <spherical_coordinates><surface_model>EARTH_WGS84</surface_model><world_frame_orientation>ENU</world_frame_orientation>
      <latitude_deg>{o["lat"]}</latitude_deg><longitude_deg>{o["lon"]}</longitude_deg><elevation>{o.get("alt", 0)}</elevation></spherical_coordinates>
  </world>
</sdf>
'''


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="生成应急救援场景")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--name")
    parser.add_argument("--lat", type=float)
    parser.add_argument("--lon", type=float)
    parser.add_argument("--label", default="自定义地点")
    args = parser.parse_args()
    config.ensure_dirs()
    loc = {"name": args.label, "address": "", "city": "", "lat": args.lat, "lon": args.lon, "source": "命令行指定（WGS-84）"} if args.lat is not None else None
    out = generate(args.seed, args.name, loc)
    site = json.loads((out / "site.json").read_text(encoding="utf-8"))
    t = site["people"][0]
    print(f"已生成 {out}：目标 {site['brief']['description']}（{STATE_LABELS[t['state']]}），最后出现在{site['brief']['last_seen_label']}；"
          f"路人 {len(site['people']) - 1} 人，干扰衣物 {len(site['decoys'])} 件；地面：{site['ground']['note']}")


if __name__ == "__main__":
    main()
