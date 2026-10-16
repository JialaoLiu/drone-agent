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
    pts = [p for p in pts if _inside(site["geofence"], *p)
           and all(math.hypot(p[0] - z["center"][0], p[1] - z["center"][1]) > z["radius"] + 3 for z in site["no_fly_zones"])]
    if pattern == "parallel":            # 平行扫描：按列来回
        pts.sort(key=lambda p: (round(p[0], 1), p[1] if int((p[0] - center[0] + radius) / dx) % 2 == 0 else -p[1]))
    else:                                # 扩展方形：按到中心的“圈”（切比雪夫距离）排序，同一圈内按角度
        pts.sort(key=lambda p: (max(abs(p[0] - center[0]) / dx, abs(p[1] - center[1]) / dy) // 1,
                                math.atan2(p[1] - center[1], p[0] - center[0])))
    return [Step("search", f"S{k + 1}", (round(x, 2), round(y, 2), altitude), 0.0, -90.0, "wide", "搜索") for k, (x, y) in enumerate(pts)]
