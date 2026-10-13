"""飞行日志（PX4 .ulg）读取与指标计算。全部是确定性计算，供诊断员引用，模型不直接读原始数值。

每个指标给出数值和判定阈值；阈值参考 PX4 官方日志分析文档的经验值（振动、跟踪误差等）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pyulog import ULog

from . import config

LOG_ROOT = config.PX4_DIR / "build" / "px4_sitl_default" / "rootfs" / "fs" / "log"


def latest_log(after: float = 0.0) -> Path | None:
    """最近一次飞行的日志（修改时间晚于 after）。"""
    logs = [p for p in LOG_ROOT.glob("*/*.ulg") if p.stat().st_mtime >= after]
    return max(logs, key=lambda p: p.stat().st_mtime) if logs else None


@dataclass
class Metric:
    name: str
    label: str
    value: float | None
    unit: str
    threshold: float | None
    status: str          # ok | warn | bad | n/a
    detail: str = ""


def _topic(log: ULog, name: str, instance: int = 0):
    for d in log.data_list:
        if d.name == name and d.multi_id == instance:
            return d.data
    return None


def _airborne_mask(log: ULog, t: np.ndarray) -> np.ndarray:
    land = _topic(log, "vehicle_land_detected")
    if land is None:
        return np.ones_like(t, dtype=bool)
    idx = np.searchsorted(land["timestamp"], t, side="right") - 1
    idx = np.clip(idx, 0, len(land["timestamp"]) - 1)
    return land["landed"][idx] == 0


def _quat_to_euler(q0, q1, q2, q3):
    roll = np.arctan2(2 * (q0 * q1 + q2 * q3), 1 - 2 * (q1 ** 2 + q2 ** 2))
    pitch = np.arcsin(np.clip(2 * (q0 * q2 - q3 * q1), -1, 1))
    return np.degrees(roll), np.degrees(pitch)


def _grade(value: float | None, warn: float, bad: float) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return "bad" if value >= bad else "warn" if value >= warn else "ok"


def analyze(path: Path) -> dict:
    log = ULog(str(path))
    metrics: list[Metric] = []

    att, sp = _topic(log, "vehicle_attitude"), _topic(log, "vehicle_attitude_setpoint")
    if att is not None and sp is not None:
        t = att["timestamp"]
        air = _airborne_mask(log, t)
        roll, pitch = _quat_to_euler(att["q[0]"], att["q[1]"], att["q[2]"], att["q[3]"])
        idx = np.clip(np.searchsorted(sp["timestamp"], t) - 1, 0, len(sp["timestamp"]) - 1)
        sroll, spitch = _quat_to_euler(sp["q_d[0]"][idx], sp["q_d[1]"][idx], sp["q_d[2]"][idx], sp["q_d[3]"][idx])
        err = np.sqrt(((roll - sroll) ** 2 + (pitch - spitch) ** 2) / 2)[air] if air.any() else np.array([np.nan])
        rms = float(np.sqrt(np.nanmean(err ** 2)))
        metrics.append(Metric("attitude_tracking", "姿态跟踪误差（均方根）", round(rms, 2), "°", 3.0, _grade(rms, 3, 8),
                              "期望姿态与实际姿态之差；过大说明控制跟不上（参数、动力不足或故障）"))
        yaw = np.degrees(np.arctan2(2 * (att["q[0]"] * att["q[3]"] + att["q[1]"] * att["q[2]"]), 1 - 2 * (att["q[2]"] ** 2 + att["q[3]"] ** 2)))
        q = [sp[f"q_d[{i}]"][idx] for i in range(4)]
        syaw = np.degrees(np.arctan2(2 * (q[0] * q[3] + q[1] * q[2]), 1 - 2 * (q[2] ** 2 + q[3] ** 2)))
        dyaw = ((yaw - syaw + 180) % 360 - 180)[air] if air.any() else np.array([np.nan])
        yaw_rms = float(np.sqrt(np.nanmean(dyaw ** 2)))
        metrics.append(Metric("yaw_tracking", "航向跟踪误差（均方根）", round(yaw_rms, 2), "°", 5.0, _grade(yaw_rms, 5, 15),
                              "转向时期望航向与实际航向之差；过大说明转向迟缓（航向增益过小）或电机反扭不足"))

    rates = _topic(log, "vehicle_angular_velocity")
    if rates is not None and len(rates["timestamp"]) > 256:
        t = rates["timestamp"]
        air = _airborne_mask(log, t)
        x = rates["xyz[0]"][air] if air.sum() > 256 else rates["xyz[0]"]
        dt = float(np.median(np.diff(t))) / 1e6
        x = x - np.mean(x)
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        freqs = np.fft.rfftfreq(len(x), dt)
        band = (freqs > 2) & (freqs < 0.45 / dt)
        if band.any():
            k = np.argmax(spec * band)
            amp = float(np.std(x) * 180 / math.pi)
            metrics.append(Metric("rate_oscillation", "横滚角速度振荡", round(amp, 2), "°/s", 10.0, _grade(amp, 10, 25),
                                  f"主频 {freqs[k]:.1f} Hz；持续的高频振荡多见于角速度环增益过大"))

    acc = _topic(log, "sensor_combined")
    if acc is not None:
        t = acc["timestamp"]
        air = _airborne_mask(log, t)
        az = acc["accelerometer_m_s2[2]"][air] if air.any() else acc["accelerometer_m_s2[2]"]
        vib = float(np.std(np.diff(az))) if len(az) > 10 else None
        metrics.append(Metric("vibration", "振动水平（Z 轴加速度高频分量）", round(vib, 3) if vib else None, "m/s²", 1.5,
                              _grade(vib, 1.5, 4.0), "过大会影响姿态估计，常见原因是桨叶损伤、电机或机架松动"))

    motors = _topic(log, "actuator_motors")
    if motors is not None:
        t = motors["timestamp"]
        air = _airborne_mask(log, t)
        cols = [c for c in motors if c.startswith("control[")][:4]
        m = np.vstack([motors[c] for c in cols]).T
        m = m[air] if air.any() else m
        m = m[~np.isnan(m).any(axis=1)]
        if len(m):
            sat = float(np.mean((m > 0.95).any(axis=1)) * 100)
            means = m.mean(axis=0)
            imbalance = float((means.max() - means.min()) * 100)
            metrics.append(Metric("motor_saturation", "电机输出饱和时间占比", round(sat, 1), "%", 5.0, _grade(sat, 5, 20),
                                  "电机长时间满输出说明动力不足或某个电机失效、其他电机在补偿"))
            metrics.append(Metric("motor_imbalance", "电机平均输出差异", round(imbalance, 1), "%", 8.0, _grade(imbalance, 8, 20),
                                  "各电机 " + "、".join(f"{v * 100:.0f}%" for v in means) + "；差异大提示单个电机或螺旋桨异常"))

    pos, psp = _topic(log, "vehicle_local_position"), _topic(log, "vehicle_local_position_setpoint")
    if pos is not None and psp is not None:
        t = pos["timestamp"]
        air = _airborne_mask(log, t)
        idx = np.clip(np.searchsorted(psp["timestamp"], t) - 1, 0, len(psp["timestamp"]) - 1)
        dz = (pos["z"] - psp["z"][idx])[air]
        dz = dz[~np.isnan(dz)]
        if len(dz):
            alt = float(np.sqrt(np.mean(dz ** 2)))
            metrics.append(Metric("altitude_tracking", "高度跟踪误差（均方根）", round(alt, 2), "m", 0.5, _grade(alt, 0.5, 1.5),
                                  "高度保持不住常见于推力不足、气压计或估计器问题"))

    bat = _topic(log, "battery_status")
    if bat is not None and len(bat["remaining"]):
        used = float((bat["remaining"][0] - bat["remaining"][-1]) * 100)
        metrics.append(Metric("battery_used", "本次耗电", round(used, 1), "%", None, "ok"))

    gps = _topic(log, "sensor_gps") or _topic(log, "vehicle_gps_position")
    if gps is not None:
        sats = int(np.min(gps["satellites_used"]))
        metrics.append(Metric("gps_satellites", "最少可用卫星数", sats, "颗", 8, "bad" if sats < 6 else "warn" if sats < 8 else "ok"))

    messages = [{"t": round((m.timestamp - log.start_timestamp) / 1e6, 1), "level": m.log_level_str(), "text": m.message}
                for m in log.logged_messages if m.log_level <= 4]   # WARNING 及以上
    duration = (log.last_timestamp - log.start_timestamp) / 1e6
    return {"path": str(path), "duration_s": round(duration, 1),
            "metrics": [m.__dict__ for m in metrics], "messages": messages[-30:],
            "abnormal": [m.name for m in metrics if m.status in ("warn", "bad")]}
