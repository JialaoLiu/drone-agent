"""安全员：对任务计划和每一条飞行指令做确定性校验。结论以这里为准，模型不能推翻。

检查项：高度范围、电子围栏（含航段）、禁飞区（含航段）、障碍物净空、电量余量（含返航）、拍摄点数量。
每条违规给出代码、说明和可执行的修改方向，供规划员调整。
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .survey import Step, route_seconds

MARGIN = 3.0                    # 离禁飞区、障碍物的最小水平距离（米）
OBSTACLE_VERTICAL_MARGIN = 3.0  # 高于障碍物多少米才可以从上方飞越
MAX_STEPS = 80
DRAIN_SECONDS = 600.0           # 满电耗尽时间（与仿真设置一致）
SAFETY_FACTOR = 1.2             # 电量估计的保守系数


@dataclass
class Violation:
    code: str
    message: str
    step: int | None = None
    suggestion: str = ""


def _inside(polygon: list[list[float]], x: float, y: float) -> bool:
    inside, j = False, len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _segment_distance(p: tuple, a: tuple, b: tuple) -> float:
    ax, ay = a[0], a[1]
    bx, by = b[0], b[1]
    px, py = p[0], p[1]
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _seg_seg_distance(a: tuple, b: tuple, c: tuple, d: tuple) -> float:
    """两条平面线段之间的最短距离（相交为 0）。"""
    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
    d1, d2, d3, d4 = cross(c, d, a), cross(c, d, b), cross(a, b, c), cross(a, b, d)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return 0.0
    return min(_segment_distance(a, c, d), _segment_distance(b, c, d), _segment_distance(c, a, b), _segment_distance(d, a, b))


def obstacle_distance(ob: dict, a: tuple, b: tuple) -> float:
    """航段（平面投影）到障碍物的水平距离。有真实轮廓（建筑）时按轮廓计算，否则按圆（树）计算（审查 H02）。"""
    poly = ob.get("polygon")
    if poly:
        if _inside(poly, a[0], a[1]) or _inside(poly, b[0], b[1]):
            return 0.0
        return min(_seg_seg_distance(a, b, poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly)))
    return max(0.0, _segment_distance(ob["center"], a, b) - ob["radius"])


def battery_needed(steps: list[Step], start: tuple = (0, 0, 0)) -> float:
    """执行计划并返航所需电量（%，含保守系数）。"""
    return route_seconds(steps, start) / DRAIN_SECONDS * 100 * SAFETY_FACTOR


def _ground(site: dict, x: float, y: float) -> float:
    """地面相对起飞点的高度（米）。只有真实场地有高程数据，其余场地地面为平面（0）。"""
    t = site.get("terrain")
    if not t:
        return 0.0
    from .twin import relative_height
    return relative_height(t, x, y)


def check_point(site: dict, pos: tuple[float, float, float], index: int | None = None, airborne_ok: bool = False) -> list[Violation]:
    out = []
    x, y, z = pos
    if not airborne_ok and site.get("terrain"):
        agl = z - _ground(site, x, y)
        if agl < site["min_altitude"]:
            out.append(Violation("terrain", f"离地高度只有 {agl:.1f} 米（该处地面比起飞点高 {z - agl:.1f} 米），低于 {site['min_altitude']} 米",
                                 index, "升高拍摄点或换一侧拍摄"))
        elif agl > site["max_altitude"]:
            # 限高按离地高度（真高）计算：地面比起飞点低时，相对起飞点的高度合规，离地高度却可能超限
            out.append(Violation("terrain", f"离地高度 {agl:.1f} 米（该处地面比起飞点低 {agl - z:.1f} 米），超过限高 {site['max_altitude']} 米",
                                 index, "该处地形起伏大；当前仿真地面为平面，暂不支持在这类场地规划"))
    if not airborne_ok and not (site["min_altitude"] <= z <= site["max_altitude"]):
        out.append(Violation("altitude", f"高度 {z:.1f} 米超出允许范围 {site['min_altitude']}–{site['max_altitude']} 米", index,
                             f"把高度调整到 {site['min_altitude']}–{site['max_altitude']} 米之间"))
    if not _inside(site["geofence"], x, y):
        out.append(Violation("geofence", f"位置 ({x:.1f}, {y:.1f}) 在电子围栏外", index, "缩短拍摄距离或换一侧拍摄"))
    for ob in site.get("obstacles", []):
        top = ob["height"] + _ground(site, *ob["center"])
        if not airborne_ok and z < top + OBSTACLE_VERTICAL_MARGIN and obstacle_distance(ob, (x, y), (x, y)) < MARGIN:
            out.append(Violation("obstacle", f"位置 ({x:.1f}, {y:.1f}) 高度 {z:.1f} 米，离{ob['label']}（{ob['id']}，高 {ob['height']} 米）太近",
                                 index, f"升高到 {ob['height'] + OBSTACLE_VERTICAL_MARGIN:.0f} 米以上或换位置"))
    for zone in site["no_fly_zones"]:
        d = math.dist((x, y), zone["center"])
        if d < zone["radius"] + MARGIN:
            out.append(Violation("no_fly_zone", f"位置距{zone['label']}禁飞区中心 {d:.1f} 米，小于 {zone['radius'] + MARGIN:.0f} 米", index,
                                 f"避开{zone['label']}"))
    return out


def check_segment(site: dict, a: tuple, b: tuple, index: int) -> list[Violation]:
    out = []
    for zone in site["no_fly_zones"]:
        if _segment_distance(zone["center"], a, b) < zone["radius"] + MARGIN:
            out.append(Violation("no_fly_zone", f"第 {index + 1} 段航线穿过{zone['label']}禁飞区", index, "绕行或调整访问顺序"))
    low = min(a[2], b[2])
    for ob in site["obstacles"]:
        top = ob["height"] + _ground(site, *ob["center"])      # 障碍物顶部相对起飞点的高度（真实场地含地形）
        if low < top + OBSTACLE_VERTICAL_MARGIN and obstacle_distance(ob, a, b) < MARGIN:
            out.append(Violation("obstacle", f"第 {index + 1} 段航线高度 {low:.1f} 米，离{ob['label']}（{ob['id']}，高 {ob['height']} 米）太近",
                                 index, f"升高到 {ob['height'] + OBSTACLE_VERTICAL_MARGIN:.0f} 米以上或绕行"))
    steps = max(2, int(math.dist(a[:2], b[:2])))   # 航段抽样检查围栏（真实场地同时检查离地高度）
    terrain_hit = False
    for k in range(1, steps):
        x, y = a[0] + (b[0] - a[0]) * k / steps, a[1] + (b[1] - a[1]) * k / steps
        if not _inside(site["geofence"], x, y):
            out.append(Violation("geofence", f"第 {index + 1} 段航线越出电子围栏", index, "调整路线"))
            break
        z = a[2] + (b[2] - a[2]) * k / steps
        if site.get("terrain") and not terrain_hit and min(a[2], b[2]) >= site["min_altitude"]:
            agl = z - _ground(site, x, y)
            if agl < site["min_altitude"]:
                terrain_hit = True
                out.append(Violation("terrain", f"第 {index + 1} 段航线经过的地面较高，离地高度低于 {site['min_altitude']} 米", index, "升高航线"))
            elif agl > site["max_altitude"]:      # 航段中间地面变低时离地高度可能超限（审查 H01）
                terrain_hit = True
                out.append(Violation("terrain", f"第 {index + 1} 段航线经过的地面较低，离地高度超过限高 {site['max_altitude']} 米", index, "降低航线或绕开低洼处"))
    return out


def check_plan(site: dict, steps: list[Step], battery: float = 100.0, reserve: float = 25.0,
               start: tuple = (0.0, 0.0, 0.0)) -> list[Violation]:
    """校验整份计划。battery 为当前电量，reserve 为返航后至少保留的电量。"""
    out: list[Violation] = []
    if not steps:
        return [Violation("empty", "计划中没有拍摄点", None, "至少选择一个巡检对象")]
    if len(steps) > MAX_STEPS:
        out.append(Violation("too_many", f"拍摄点 {len(steps)} 个，超过上限 {MAX_STEPS}", None, "减少近距补拍数量"))
    prev = (start[0], start[1], steps[0].position[2])
    for i, s in enumerate(steps):
        out += check_point(site, s.position, i)
        if math.dist(prev, s.position) > 0.01:
            out += check_segment(site, prev, s.position, i)
        prev = s.position
    out += check_segment(site, prev, (start[0], start[1], prev[2]), len(steps))   # 返航段
    need = battery_needed(steps, start)
    if battery - need < reserve:
        out.append(Violation("battery", f"估计需要 {need:.0f}% 电量，当前 {battery:.0f}%，返航后只剩 {battery - need:.0f}%，低于保留 {reserve:.0f}%",
                             None, "减少巡检对象或近距补拍，或分两次飞行"))
    return out


def as_dicts(violations: list[Violation]) -> list[dict]:
    return [asdict(v) for v in violations]
