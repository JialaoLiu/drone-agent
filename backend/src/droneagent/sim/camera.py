"""从相机桥取图，并算出拍摄瞬间相机在世界中的位姿（用于把光伏板投影到照片上）。"""
from __future__ import annotations

import io
import json
import math
from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image

from .. import config
from ..geo import CameraPose, mat_vec, quat_to_matrix, rpy_to_matrix

HFOV = {"wide": 1.1, "zoom": 0.3}
# 相机传感器相对 camera_link 的位姿（见 sim/models/inspect_gimbal/model.sdf）
SENSOR_OFFSET = (-0.0412, 0.0, -0.162)
SENSOR_YAW = 3.14


class CameraUnavailable(RuntimeError):
    pass


@dataclass
class Frame:
    camera: str
    image: Image.Image
    pose: CameraPose
    sim_time: float
    drone: tuple[float, float, float] | None = None   # 拍摄瞬间飞机在仿真中的真实位置（仅仿真可得）

    def jpeg(self, quality: int = 90) -> bytes:
        buf = io.BytesIO()
        self.image.save(buf, "JPEG", quality=quality)
        return buf.getvalue()

    def meta(self) -> dict:
        return {"camera": self.camera, "sim_time": self.sim_time, "position": list(self.pose.position),
                "rotation": self.pose.rotation, "hfov": self.pose.hfov, "width": self.pose.width, "height": self.pose.height}

    def save(self, path: Path) -> None:
        """保存为 PNG（无损，便于复核）和位姿 JSON。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        self.image.save(path.with_suffix(".png"))
        path.with_suffix(".json").write_text(json.dumps(self.meta()), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Frame":
        m = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        pose = CameraPose(position=tuple(m["position"]), rotation=m["rotation"], hfov=m["hfov"], width=m["width"], height=m["height"])
        return cls(m["camera"], Image.open(path.with_suffix(".png")).convert("RGB"), pose, m["sim_time"])


def _matmul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def camera_pose(poses: dict, camera: str, width: int, height: int) -> CameraPose:
    """模型位姿（世界）× camera_link 位姿（相对模型，含云台转角）× 传感器偏移 = 相机在世界中的位姿。"""
    model, link = poses["x500_inspect_0"], poses["camera_link"]
    r_model = quat_to_matrix(*model["orientation"])
    r_link = quat_to_matrix(*link["orientation"])
    link_pos = mat_vec(r_model, tuple(link["position"]))
    link_world = [model["position"][i] + link_pos[i] for i in range(3)]
    r_link_world = _matmul(r_model, r_link)
    offset = mat_vec(r_link_world, SENSOR_OFFSET)
    position = tuple(link_world[i] + offset[i] for i in range(3))
    rotation = _matmul(r_link_world, rpy_to_matrix(0, 0, SENSOR_YAW))
    return CameraPose(position=position, rotation=rotation, hfov=HFOV[camera], width=width, height=height)


def capture(camera: str = "wide", timeout: float = 5.0) -> Frame:
    try:
        r = httpx.get(f"{config.CAMERA_BRIDGE_URL}/frame", params={"camera": camera}, timeout=timeout)
    except httpx.HTTPError as error:
        raise CameraUnavailable(f"相机桥无法连接：{error}") from error
    if r.status_code != 200:
        raise CameraUnavailable(f"相机暂时没有画面（{r.status_code}）")
    w, h = int(r.headers["X-Width"]), int(r.headers["X-Height"])
    poses = json.loads(r.headers["X-Poses"])
    if "x500_inspect_0" not in poses or "camera_link" not in poses:
        raise CameraUnavailable("还没有收到飞机位姿")
    image = Image.frombytes("RGB", (w, h), r.content)
    return Frame(camera=camera, image=image, pose=camera_pose(poses, camera, w, h), sim_time=float(r.headers["X-Sim-Time"]),
                 drone=tuple(poses["x500_inspect_0"]["position"]))


def sim_state(timeout: float = 3.0) -> dict:
    """相机桥的状态：最新画面的仿真时间、飞机真实位置。用于飞行前检查（仿真速度、位置估计是否漂移）。"""
    try:
        r = httpx.get(f"{config.CAMERA_BRIDGE_URL}/health", timeout=timeout)
        r.raise_for_status()
    except httpx.HTTPError as error:
        raise CameraUnavailable(f"相机桥无法连接：{error}") from error
    data = r.json()
    times = list(data.get("cameras", {}).values())
    model = data.get("poses", {}).get("x500_inspect_0")
    return {"sim_time": max(times) if times else None, "drone": tuple(model["position"]) if model else None}


def look_angles(position: tuple[float, float, float], target: tuple[float, float, float]) -> tuple[float, float]:
    """从 position 看向 target 所需的机头朝向（度，北为 0、顺时针）与云台俯仰（度，向下为负）。"""
    dx, dy, dz = (target[i] - position[i] for i in range(3))
    yaw = math.degrees(math.atan2(dx, dy)) % 360
    pitch = math.degrees(math.atan2(dz, math.hypot(dx, dy)))
    return yaw, pitch
