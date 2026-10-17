"""搜索航线：以最后出现的位置为中心，在半径 R 的方形内按相机覆盖范围（重叠 20%）铺拍摄点，
从中心向外按圈排序（扩展方形），越靠近最后出现位置的地方越先搜。航点全部由程序计算。"""
from __future__ import annotations

import math

from ..survey import Step
from ..sim.camera import HFOV

IMAGE = (1280, 720)
OVERLAP = 0.2


def footprint(altitude: float) -> tuple[float, float]:
    """俯视时广角相机在地面上的覆盖宽度、高度（米）。"""
    w = 2 * altitude * math.tan(HFOV["wide"] / 2)
    vfov = 2 * math.atan(math.tan(HFOV["wide"] / 2) * IMAGE[1] / IMAGE[0])
    return w, 2 * altitude * math.tan(vfov / 2)


def cover_circles(polygon: list[list[float]]) -> list[tuple[list[float], float]]:
    """用几个圆盖住建筑轮廓：取主方向上的外接矩形，沿长边均分成接近正方形的几段，每段一个外接圆。
    比整栋楼一个外接圆紧得多（30×14 米的楼：3 个约 8.6 米的圆，而不是 1 个 16.5 米的圆），绕行与选点都更贴近真实轮廓。"""
    pts = polygon[:-1] if len(polygon) > 3 and polygon[0] == polygon[-1] else polygon
    cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    sxx = sum((p[0] - cx) ** 2 for p in pts)
    syy = sum((p[1] - cy) ** 2 for p in pts)
    sxy = sum((p[0] - cx) * (p[1] - cy) for p in pts)
    a = 0.5 * math.atan2(2 * sxy, sxx - syy)                      # 主方向
    ux, uy, vx, vy = math.cos(a), math.sin(a), -math.sin(a), math.cos(a)
    us = [(p[0] - cx) * ux + (p[1] - cy) * uy for p in pts]
    vs = [(p[0] - cx) * vx + (p[1] - cy) * vy for p in pts]
    u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
    length, width = u1 - u0, max(v1 - v0, 0.5)
    if length < width:                                             # 让 u 是长边
        u0, u1, v0, v1, ux, uy, vx, vy = v0, v1, u0, u1, vx, vy, ux, uy
        length, width = width, length
    n = max(1, math.ceil(length / width))
    seg = length / n
    r = math.hypot(seg / 2, width / 2)
    vm = (v0 + v1) / 2
    return [([round(cx + (u0 + seg * (k + 0.5)) * ux + vm * vx, 2), round(cy + (u0 + seg * (k + 0.5)) * uy + vm * vy, 2)], round(r, 2))
            for k in range(n)]


def route_site(site: dict, altitude: float) -> dict:
    """排航线用的场地：高于航线高度（含余量）的建筑当绕行区（用几个圆盖住真实轮廓），拍摄点不放在楼上、航段绕开。
    只用于排航线；安全员仍按建筑真实轮廓校验整条航线。树不在此列：树矮，规划员按安全员的提示升高即可。"""
    from ..safety import OBSTACLE_VERTICAL_MARGIN
    tall = [{"id": f"N{o['id']}_{k}", "label": f"{o['label']} {o['id']}（高 {o['height']:.0f} 米）", "center": c, "radius": r}
            for o in site.get("obstacles", []) if o.get("polygon") and o["height"] + OBSTACLE_VERTICAL_MARGIN > altitude
            for k, (c, r) in enumerate(cover_circles(o["polygon"]))]
    return {**site, "no_fly_zones": list(site.get("no_fly_zones", [])) + tall} if tall else site


def search_steps(site: dict, center: list[float], radius: float, altitude: float, pattern: str = "expanding") -> list[Step]:
    """返回拍摄点（kind=search，俯视）。不在围栏内或落在禁飞区里的点跳过；安全员再对整条航线做校验。"""
    fw, fh = footprint(altitude)
    dx, dy = fw * (1 - OVERLAP), fh * (1 - OVERLAP)
    nx, ny = max(1, math.ceil(2 * radius / dx)), max(1, math.ceil(2 * radius / dy))
    pts = []
    for i in range(nx):
        for j in range(ny):
            x = center[0] - radius + dx * (i + 0.5)
            y = center[1] - radius + dy * (j + 0.5)
            pts.append((x, y))
    from ..sim.scene import _inside

    def free(x: float, y: float) -> bool:
        return _inside(site["geofence"], x, y) and all(math.hypot(x - z["center"][0], y - z["center"][1]) > z["radius"] + 3 for z in site["no_fly_zones"])

    def nudge(x: float, y: float) -> tuple[float, float] | None:
        """落在楼上、禁飞区里或围栏外的拍摄点：在半个拍摄间距内找最近的空位（楼旁边的地面仍要拍到），找不到才放弃。"""
        if free(x, y):
            return x, y
        for r in range(2, int(min(dx, dy) / 2) + 1, 2):
            for k in range(12):
                a = k * math.pi / 6
                if free(x + r * math.cos(a), y + r * math.sin(a)):
                    return x + r * math.cos(a), y + r * math.sin(a)
        return None
    pts = [q for q in (nudge(*p) for p in pts) if q]
    if pattern == "parallel":            # 平行扫描：按列来回
        pts.sort(key=lambda p: (round(p[0], 1), p[1] if int((p[0] - center[0] + radius) / dx) % 2 == 0 else -p[1]))
    else:                                # 扩展方形：按到中心的“圈”（切比雪夫距离）排序，同一圈内按角度
        pts.sort(key=lambda p: (max(abs(p[0] - center[0]) / dx, abs(p[1] - center[1]) / dy) // 1,
                                math.atan2(p[1] - center[1], p[0] - center[0])))
    return [Step("search", f"S{k + 1}", (round(x, 2), round(y, 2), altitude), 0.0, -90.0, "wide", "搜索") for k, (x, y) in enumerate(pts)]
