"""坐标换算与相机投影。

本地坐标为 ENU（x 东、y 北、z 上，单位米），原点是起飞点；与经纬度之间用局部平面近似换算，
场地范围在几百米以内，误差远小于 GPS 精度。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS = 6378137.0


@dataclass(frozen=True)
class Origin:
    lat: float
    lon: float
    alt: float   # 起飞点海拔（米）

    def to_global(self, x: float, y: float, z: float = 0.0) -> tuple[float, float, float]:
        lat = self.lat + math.degrees(y / EARTH_RADIUS)
        lon = self.lon + math.degrees(x / (EARTH_RADIUS * math.cos(math.radians(self.lat))))
        return lat, lon, self.alt + z

    def to_local(self, lat: float, lon: float, alt: float | None = None) -> tuple[float, float, float]:
        y = math.radians(lat - self.lat) * EARTH_RADIUS
        x = math.radians(lon - self.lon) * EARTH_RADIUS * math.cos(math.radians(self.lat))
        return x, y, (alt - self.alt) if alt is not None else 0.0


def quat_to_matrix(w: float, x: float, y: float, z: float) -> list[list[float]]:
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def rpy_to_matrix(roll: float, pitch: float, yaw: float) -> list[list[float]]:
    cr, sr, cp, sp, cy, sy = (math.cos(roll), math.sin(roll), math.cos(pitch), math.sin(pitch),
                              math.cos(yaw), math.sin(yaw))
    return [
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ]


def mat_vec(m: list[list[float]], v: tuple[float, float, float]) -> tuple[float, float, float]:
    return (m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
            m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
            m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2])


def transpose(m: list[list[float]]) -> list[list[float]]:
    return [[m[j][i] for j in range(3)] for i in range(3)]


@dataclass(frozen=True)
class CameraPose:
    """相机在世界中的位姿：位置与旋转矩阵（相机坐标系沿用 Gazebo 约定：x 向前、y 向左、z 向上）。"""
    position: tuple[float, float, float]
    rotation: list[list[float]]
    hfov: float
    width: int
    height: int

    def project(self, point: tuple[float, float, float]) -> tuple[float, float] | None:
        """世界坐标点投影到像素坐标（u 向右、v 向下）；在相机后方时返回 None。"""
        d = (point[0] - self.position[0], point[1] - self.position[1], point[2] - self.position[2])
        x, y, z = mat_vec(transpose(self.rotation), d)
        if x <= 0.05:
            return None
        f = self.width / (2 * math.tan(self.hfov / 2))
        return self.width / 2 - f * y / x, self.height / 2 - f * z / x

    def ground_footprint(self) -> float:
        """相机光轴与地面交点处，一个像素对应的地面尺寸（米），用于判断能否看清细小缺陷。"""
        forward = mat_vec(self.rotation, (1.0, 0.0, 0.0))
        if forward[2] >= -1e-3:
            return float("inf")
        distance = self.position[2] / -forward[2]
        return distance * 2 * math.tan(self.hfov / 2) / self.width
