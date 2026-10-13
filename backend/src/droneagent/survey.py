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
SHOT_SECONDS = {"overview": 30.0, "closeup": 3.0}   # 近距复核实测约 2.3 秒 / 块
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


def normal() -> tuple[float, float, float]:
    return 0.0, -math.sin(TILT), math.cos(TILT)


def overview_step(array: dict, distance: float) -> Step:
    n = normal()
    cx, cy = array["center"]
    pos = (cx + n[0] * distance, cy + n[1] * distance, PANEL_HEIGHT + n[2] * distance)
    yaw, pitch = look_angles(pos, (cx, cy, PANEL_HEIGHT))
    return Step("overview", array["id"], pos, yaw, pitch, "wide")


def closeup_step(panel: dict, viewpoint: tuple[float, float, float], reason: str = "") -> Step:
    """从 viewpoint（通常是该列的整列拍摄点）用变焦相机对准某块板。"""
    yaw, pitch = look_angles(viewpoint, tuple(panel["center"]))
    return Step("closeup", panel["id"], viewpoint, yaw, pitch, "zoom", reason)


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
