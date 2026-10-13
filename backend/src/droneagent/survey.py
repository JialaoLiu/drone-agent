"""巡检几何：拍摄点计算、航线与耗时估计。全部是确定性计算，模型只选择策略参数。

光伏板朝南倾斜 25°。整列拍摄（overview）：沿板面法线方向退后 D 米，广角相机正对板面，
一张照片覆盖整列 20 块板；近距补拍（closeup）：在同一位置转动机头与云台，用变焦相机对准单块板。
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .sim.camera import look_angles
from .sim.scene import PANEL_HEIGHT, TILT

CRUISE_SPEED = 5.0          # m/s，与 flight.CRUISE_SPEED 一致
# 每个拍摄点的停留时间（秒），按实测标定：整列拍摄含云台稳定与等待初筛结果（视觉模型约 20–50 秒，取 30），
# 近距复核含转向、云台稳定、取图，复核结果在飞往下一块时并行返回。
SHOT_SECONDS = {"overview": 30.0, "closeup": 3.0, "transit": 0.0}   # 近距复核实测约 2.3 秒 / 块；绕行点不停留
DETOUR_MARGIN = 5.0         # 绕行时离禁飞区边界的距离（大于安全员要求的 3 米）
CLIMB_SPEED = 1.0           # 起飞爬升实测约 1 m/s
DESCENT_SPEED = 0.7         # PX4 降落速度 MPC_LAND_SPEED 默认 0.7 m/s


@dataclass
class Step:
    kind: str                       # overview | closeup
    target: str                     # 阵列编号或光伏板编号
    position: tuple[float, float, float]
    yaw: float
    pitch: float
    camera: str                     # wide | zoom
    reason: str = ""

    def as_dict(self) -> dict:
        d = asdict(self)
        d["position"] = [round(v, 2) for v in self.position]
        d["yaw"], d["pitch"] = round(self.yaw, 1), round(self.pitch, 1)
        return d


def normal(yaw_deg: float = 0.0) -> tuple[float, float, float]:
    """板面法线：朝南倾斜 TILT，再按阵列朝向旋转 yaw。"""
    yaw = math.radians(yaw_deg)
    s = math.sin(TILT)
    return s * math.sin(yaw), -s * math.cos(yaw), math.cos(TILT)


def overview_step(array: dict, distance: float) -> Step:
    n = normal(array.get("yaw_deg", 0.0))
    cx, cy = array["center"]
    pos = (cx + n[0] * distance, cy + n[1] * distance, PANEL_HEIGHT + n[2] * distance)
    yaw, pitch = look_angles(pos, (cx, cy, PANEL_HEIGHT))
    return Step("overview", array["id"], pos, yaw, pitch, "wide")


def closeup_step(panel: dict, viewpoint: tuple[float, float, float], reason: str = "") -> Step:
    """从 viewpoint（通常是该列的整列拍摄点）用变焦相机对准某块板。"""
    yaw, pitch = look_angles(viewpoint, tuple(panel["center"]))
    return Step("closeup", panel["id"], viewpoint, yaw, pitch, "zoom", reason)


def _segment_closest(c: tuple, a: tuple, b: tuple) -> tuple[float, float]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((c[0] - a[0]) * dx + (c[1] - a[1]) * dy) / length2))
    return a[0] + t * dx, a[1] + t * dy


def _inside(poly: list[list[float]], x: float, y: float) -> bool:
    inside, j = False, len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
            inside = not inside
        j = i
    return inside


GRID = 2.0                  # 绕行规划的栅格边长（米）
CLEAR = 3.5                 # 判定航段可直飞的最小距离：安全员要求 3 米，留 0.5 米余量


def _clear(a: tuple, b: tuple, zones: list[dict], extra: float = CLEAR) -> bool:
    return all(math.dist(_segment_closest(z["center"], a, b), z["center"]) >= z["radius"] + extra for z in zones)


def detour(a: tuple, b: tuple, site: dict) -> list[tuple[float, float, float]]:
    """从 a 飞到 b 时需要插入的绕行点（不含 a、b）。直飞不穿过禁飞区就返回空。

    否则在围栏内做栅格 A* 搜索（禁飞区按半径 + DETOUR_MARGIN 膨胀），再按视线可达把路径化简成尽量少的拐点。
    多个禁飞区挨得很近、中间过不去时，搜索会自动绕到外侧。找不到路径时返回空，交给安全员拒绝。
    """
    import heapq

    zones = site.get("no_fly_zones", [])
    if not zones or _clear(a, b, zones):
        return []
    fence = site["geofence"]
    xs, ys = [p[0] for p in fence], [p[1] for p in fence]
    x0, y0 = min(xs), min(ys)
    nx, ny = int((max(xs) - x0) / GRID) + 1, int((max(ys) - y0) / GRID) + 1

    def free(i: int, j: int) -> bool:
        x, y = x0 + i * GRID, y0 + j * GRID
        return _inside(fence, x, y) and all(math.dist((x, y), z["center"]) >= z["radius"] + DETOUR_MARGIN for z in zones)

    def cell(p: tuple) -> tuple[int, int]:
        return min(nx - 1, max(0, round((p[0] - x0) / GRID))), min(ny - 1, max(0, round((p[1] - y0) / GRID)))

    start, goal = cell(a), cell(b)
    frontier = [(0.0, start)]
    came: dict = {start: None}
    cost = {start: 0.0}
    while frontier:
        _, cur = heapq.heappop(frontier)
        if cur == goal:
            break
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                nxt = (cur[0] + di, cur[1] + dj)
                if (di or dj) and 0 <= nxt[0] < nx and 0 <= nxt[1] < ny and (nxt == goal or free(*nxt)):
                    c = cost[cur] + math.hypot(di, dj)
                    if c < cost.get(nxt, float("inf")):
                        cost[nxt], came[nxt] = c, cur
                        heapq.heappush(frontier, (c + math.dist(nxt, goal), nxt))
    if goal not in came:
        return []
    path, node = [], goal
    while node is not None:
        path.append((x0 + node[0] * GRID, y0 + node[1] * GRID))
        node = came[node]
    path = [(a[0], a[1])] + path[::-1][1:-1] + [(b[0], b[1])]
    # 化简：从当前点出发，跳到最远的、可以直飞到达的路径点
    z = max(a[2], b[2])
    points, i = [], 0
    while i < len(path) - 1:
        j = len(path) - 1
        while j > i + 1 and not _clear(path[i], path[j], zones):
            j -= 1
        if j < len(path) - 1:
            points.append((round(path[j][0], 2), round(path[j][1], 2), z))
        i = j
    return points


def with_detours(steps: list[Step], site: dict, start: tuple = (0.0, 0.0, 0.0)) -> list[Step]:
    """在拍摄步骤之间（以及返航段）插入绕行点。"""
    out: list[Step] = []
    cur = (start[0], start[1], steps[0].position[2] if steps else 10.0)
    for s in steps:
        for w in detour(cur, s.position, site):
            out.append(Step("transit", "绕行", w, s.yaw, 0.0, "", "绕开禁飞区"))
        out.append(s)
        cur = s.position
    for w in detour(cur, (start[0], start[1], cur[2]), site):
        out.append(Step("transit", "绕行", w, 0.0, 0.0, "", "返航绕开禁飞区"))
    return out


def order_arrays(arrays: list[dict], start: tuple[float, float] = (0.0, 0.0)) -> list[dict]:
    """最近邻顺序访问各阵列，减少航程。"""
    left, route, cur = list(arrays), [], start
    while left:
        nxt = min(left, key=lambda a: math.dist(cur, a["center"]))
        route.append(nxt)
        left.remove(nxt)
        cur = tuple(nxt["center"])
    return route


def route_seconds(steps: list[Step], start: tuple[float, float, float] = (0, 0, 0), cruise_alt: float | None = None) -> float:
    """估计执行这些步骤再返航降落所需的秒数（含起飞爬升、拍摄停留）。"""
    total, cur = 0.0, start
    first_alt = steps[0].position[2] if steps else 10.0
    total += max(0.0, first_alt - start[2]) / CLIMB_SPEED    # 已在空中时不再计爬升
    cur = (start[0], start[1], max(first_alt, start[2]))
    for s in steps:
        total += math.dist(cur, s.position) / CRUISE_SPEED + SHOT_SECONDS[s.kind]
        cur = s.position
    home_alt = max(cur[2], cruise_alt or 0)
    total += math.dist(cur[:2], start[:2]) / CRUISE_SPEED + home_alt / DESCENT_SPEED   # 返航水平段 + 降落
    return total
