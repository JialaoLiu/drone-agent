"""飞行前检查与飞行中的位置一致性检查（确定性规则，属于安全员）。

检查项：
  飞控自检：传感器校准、位置估计、可否解锁（真机同样适用）；
  传感器超时：最近 60 秒内飞控是否报过传感器超时或故障（真机同样适用）；
  仿真速度：仿真时间与真实时间之比，过低时飞控的传感器会超时、高度估计会漂移（仅仿真）；
  位置估计：飞控估计的位置与仿真中的真实位置之差（仅仿真；真机上对应 RTK 或视觉定位的交叉校验）；
  电脑负载：只提示，不拦截；
  天气：风速、阵风、降水、雷暴、能见度（weather.py；仿真中默认只提示）。
任何一项“不通过”都拒绝起飞，并给出原因和处理建议。
"""
from __future__ import annotations

import asyncio
import math
import os
import re
import time

from . import simctl, weather
from .sim import camera
from .sim.scene import load_site

SENSOR_FAULT = re.compile(r"timeout|(accel|gyro|baro|mag|sensor|imu|ekf|estimator|height|gps)\S*.*\b(fail|lost|stale|inconsistent)", re.I)
IGNORED = re.compile(r"no connection to the gcs", re.I)   # 地面站尚未连上时的提示，与传感器无关
RECENT_SECONDS = 60
MIN_REAL_TIME = 0.6          # 仿真速度低于真实时间的 60% 拒绝起飞
WARN_REAL_TIME = 0.85
MAX_DRIFT_Z = 1.5            # 米
MAX_DRIFT_XY = 2.5
LOAD_PER_CORE_WARN = 1.5


def _item(key: str, label: str, ok: bool | None, detail: str) -> dict:
    """ok 为 True 通过、False 不通过（拒绝起飞）、None 提示。"""
    return {"key": key, "label": label, "ok": ok, "detail": detail}


def recent_faults(status_log: list[tuple[float, str]], now: float | None = None) -> list[str]:
    now = now or time.time()
    return [t for ts, t in status_log if now - ts <= RECENT_SECONDS and SENSOR_FAULT.search(t) and not IGNORED.search(t)]


def drift(estimated: tuple[float, float, float], actual: tuple[float, float, float]) -> tuple[float, float]:
    """飞控估计位置与真实位置之差：水平、垂直（米）。"""
    return math.dist(estimated[:2], actual[:2]), abs(estimated[2] - actual[2])


def drift_item(estimated: tuple[float, float, float], actual: tuple[float, float, float] | None) -> dict:
    if actual is None:
        return _item("drift", "位置估计", None, "拿不到仿真中的真实位置，跳过")
    xy, z = drift(estimated, actual)
    ok = xy <= MAX_DRIFT_XY and z <= MAX_DRIFT_Z
    return _item("drift", "位置估计", ok, f"与真实位置相差水平 {xy:.1f} 米、高度 {z:.1f} 米"
                 + ("" if ok else f"（上限 {MAX_DRIFT_XY} / {MAX_DRIFT_Z} 米），近照会对不准"))


def real_time_item(factor: float | None) -> dict:
    if factor is None:
        return _item("real_time", "仿真速度", None, "没有拿到仿真时间，跳过")
    ok = None if MIN_REAL_TIME <= factor < WARN_REAL_TIME else factor >= WARN_REAL_TIME
    detail = f"为真实时间的 {factor * 100:.0f}%"
    if factor < MIN_REAL_TIME:
        detail += "，电脑太忙，飞控传感器会超时、高度估计会漂移"
    elif ok is None:
        detail += "，偏慢"
    return _item("real_time", "仿真速度", ok, detail)


def load_item() -> dict:
    try:
        load = os.getloadavg()[0]
    except OSError:
        return _item("load", "电脑负载", None, "无法读取")
    cores = os.cpu_count() or 1
    busy = load / cores > LOAD_PER_CORE_WARN
    return _item("load", "电脑负载", None if busy else True,
                 f"1 分钟平均负载 {load:.1f}（{cores} 核）" + ("，建议关闭其他占用资源的程序" if busy else ""))


def _current_site() -> dict | None:
    scene = simctl.status().get("scene")
    try:
        return load_site(scene) if scene else None
    except (FileNotFoundError, OSError):
        return None


async def preflight(f, sample_seconds: float = 2.0, site: dict | None = None) -> dict:
    """飞行前检查。f 为 flight.Flight。返回 {"ok": bool, "items": [...], "advice": str}。"""
    items = []
    h = await f.health()
    if h is None:
        items.append(_item("autopilot", "飞控自检", False, "5 秒内没有收到飞控的自检结果"))
    else:
        bad = [name for key, name in (("gyro", "陀螺仪"), ("accel", "加速度计"), ("mag", "磁罗盘"),
                                      ("local_position", "本地位置估计"), ("global_position", "全球位置估计"), ("home", "起飞点"))
               if not h[key]]
        items.append(_item("autopilot", "飞控自检", not bad, "传感器校准与位置估计正常" if not bad else "未通过：" + "、".join(bad)))
    faults = recent_faults(f.status_log)
    items.append(_item("sensors", "传感器超时", not faults,
                       "最近 60 秒没有传感器超时或故障" if not faults else f"最近 60 秒有 {len(faults)} 条：{faults[-1].strip()}"))
    try:
        first = await asyncio.to_thread(camera.sim_state)
        started = time.time()
        await asyncio.sleep(sample_seconds)
        second = await asyncio.to_thread(camera.sim_state)
        wall = time.time() - started
        factor = (second["sim_time"] - first["sim_time"]) / wall if first["sim_time"] is not None and second["sim_time"] is not None and wall > 0 else None
        items.append(real_time_item(factor))
        items.append(drift_item((f.state.x, f.state.y, f.state.z), _relative(second["drone"])))
    except camera.CameraUnavailable as error:
        items.append(_item("real_time", "仿真速度", None, f"跳过：{error}"))
    items.append(load_item())
    items.append(await asyncio.to_thread(weather.item, site or _current_site()))
    ok = all(i["ok"] is not False for i in items)
    failed = [i["label"] for i in items if i["ok"] is False]
    advice = "" if ok else ("不通过：" + "、".join(failed) + "。请关闭占用资源的程序，在场景库重启当前场景后再试。")
    return {"ok": ok, "items": items, "advice": advice, "checked_at": time.time()}


# 飞机停在起飞点时模型原点的高度（实测约 -0.01 米）；位置估计的高度相对起飞点
MODEL_GROUND_OFFSET = 0.0


def _relative(model: tuple[float, float, float] | None) -> tuple[float, float, float] | None:
    return None if model is None else (model[0], model[1], model[2] - MODEL_GROUND_OFFSET)


def frame_drift(estimated: tuple[float, float, float], model: tuple[float, float, float] | None) -> dict:
    """拍摄瞬间的位置一致性（飞行中使用）。"""
    return drift_item(estimated, _relative(model))
