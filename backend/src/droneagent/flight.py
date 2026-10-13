"""飞行控制：对 MAVSDK（v4 原生绑定）的封装。只做确定性的动作执行与遥测，不做任何决策。

所有坐标用本地 ENU（米），与场景文件一致；内部换算为经纬度后下发。
每个动作都有超时；失败抛出 FlightError，由上层（飞手 Agent）决定如何处理。
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field

from mavsdk.asyncio import ComponentType, Configuration, Mavsdk
from mavsdk.asyncio.plugins.action import ActionAsync
from mavsdk.asyncio.plugins.failure import FailureAsync
from mavsdk.asyncio.plugins.gimbal import GimbalAsync
from mavsdk.asyncio.plugins.param import ParamAsync
from mavsdk.asyncio.plugins.telemetry import TelemetryAsync
from mavsdk.plugins.failure import FailureType, FailureUnit
from mavsdk.plugins.gimbal import ControlMode, GimbalMode, SendMode

from . import config
from .geo import Origin

CRUISE_SPEED = 5.0        # m/s
# 连接时设置的参数：
#   仿真电池满电 600 秒耗尽、最低到 5%（PX4 默认 60 秒、最低 50%，不符合实际）；
#   失联保护的返航高度 30 米（PX4 默认 60 米，会超出场地限高）。正常返航由本程序沿安全员校验过的航线执行。
DEFAULT_PARAMS = {"SIM_BAT_DRAIN": 600.0, "SIM_BAT_MIN_PCT": 5.0, "RTL_RETURN_ALT": 30.0}


class FlightError(RuntimeError):
    pass


@dataclass
class Telemetry:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0            # 相对起飞点高度
    yaw: float = 0.0          # 机头朝向，度，北为 0
    battery: float = 100.0    # 剩余电量，%
    armed: bool = False
    in_air: bool = False
    flight_mode: str = "UNKNOWN"
    gimbal_pitch: float = 0.0
    status: list[str] = field(default_factory=list)   # 飞控状态消息（最近 20 条）
    updated: float = 0.0

    def as_dict(self) -> dict:
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


class Flight:
    """一架飞机的连接。用法：async with Flight(origin) as f: await f.takeoff(15) ..."""

    def __init__(self, origin: Origin, url: str | None = None):
        self.origin = origin
        self.url = url or config.MAVLINK_URL
        self.state = Telemetry()
        self._tasks: list[asyncio.Task] = []
        self._home_amsl: float | None = None
        self._gimbal_id: int | None = None
        self.listeners: list = []      # 遥测回调（用于推送到前端）

    async def __aenter__(self) -> "Flight":
        await self.connect()
        return self

    async def __aexit__(self, *_):
        for t in self._tasks:
            t.cancel()

    _mavsdk: dict[str, Mavsdk] = {}     # 每个连接地址只建一个实例：重复监听同一 UDP 端口会冲突

    async def connect(self, timeout: float = 30.0) -> None:
        for t in self._tasks:
            t.cancel()
        if self.url not in Flight._mavsdk:
            m = Mavsdk(Configuration.create_with_component_type(ComponentType.GROUND_STATION))
            await m.add_any_connection(self.url)
            Flight._mavsdk[self.url] = m
        self.mavsdk = Flight._mavsdk[self.url]
        self._home_amsl = None
        self._gimbal_id = None
        system = await self.mavsdk.first_autopilot(timeout)
        if system is None:
            raise FlightError("没有连接到飞控（仿真是否已启动？）")
        self.system = system
        self.telemetry = TelemetryAsync(system)
        self.action = ActionAsync(system)
        self.gimbal = GimbalAsync(system)
        self.param = ParamAsync(system)
        self.failure = FailureAsync(system)
        self._tasks = [asyncio.create_task(c) for c in (self._watch_position(), self._watch_battery(), self._watch_flags(),
                                                         self._watch_attitude(), self._watch_status())]
        for name, value in DEFAULT_PARAMS.items():
            try:
                await self.param.set_param_float(name, value)
            except Exception:  # 真机或其他飞控没有这些参数，忽略
                pass

    # ---------- 遥测 ----------

    def _notify(self) -> None:
        self.state.updated = time.time()
        for fn in self.listeners:
            fn(self.state)

    async def _watch_position(self):
        async for p in self.telemetry.subscribe_position():
            if self._home_amsl is None:
                self._home_amsl = p.absolute_altitude_m - p.relative_altitude_m
            self.state.x, self.state.y, _ = self.origin.to_local(p.latitude_deg, p.longitude_deg)
            self.state.z = p.relative_altitude_m
            self._notify()

    async def _watch_battery(self):
        async for b in self.telemetry.subscribe_battery():
            if b.remaining_percent == b.remaining_percent:   # 跳过 NaN
                self.state.battery = b.remaining_percent if b.remaining_percent > 1 else b.remaining_percent * 100

    async def _watch_attitude(self):
        async for a in self.telemetry.subscribe_attitude_euler():
            self.state.yaw = a.yaw_deg % 360

    async def _watch_flags(self):
        async def armed():
            async for v in self.telemetry.subscribe_armed():
                self.state.armed = v

        async def in_air():
            async for v in self.telemetry.subscribe_in_air():
                self.state.in_air = v

        async def mode():
            async for m in self.telemetry.subscribe_flight_mode():
                self.state.flight_mode = m.name if hasattr(m, "name") else str(m)

        await asyncio.gather(armed(), in_air(), mode())

    async def _watch_status(self):
        async for s in self.telemetry.subscribe_status_text():
            self.state.status = (self.state.status + [s.text])[-20:]

    # ---------- 动作 ----------

    async def wait_ready(self, timeout: float = 90.0) -> None:
        """等待飞控可解锁（定位、航向、起飞点都就绪）。"""
        deadline = time.time() + timeout
        async for h in self.telemetry.subscribe_health():
            if h.is_armable and h.is_global_position_ok and h.is_home_position_ok:
                return
            if time.time() > deadline:
                raise FlightError("飞控在规定时间内没有就绪（定位或传感器未通过检查）")

    async def takeoff(self, altitude: float, timeout: float = 60.0) -> None:
        await self.wait_ready()
        await self.action.set_takeoff_altitude(altitude)
        await self.action.set_current_speed(CRUISE_SPEED)
        try:
            await self.action.arm()
            await self.action.takeoff()
        except Exception as error:
            raise FlightError(f"起飞被拒绝：{error}") from error
        await self._wait(lambda: self.state.z >= altitude - 0.8, timeout, "起飞未在规定时间内到达高度")

    async def goto(self, x: float, y: float, z: float, yaw: float, tolerance: float = 0.8, timeout: float | None = None) -> float:
        """飞到本地坐标点，返回实际耗时。超时说明没能到达（风、故障或被拦截）。"""
        if self._home_amsl is None:
            raise FlightError("还没有收到位置信息")
        lat, lon, _ = self.origin.to_global(x, y)
        started = time.time()
        try:
            await self.action.goto_location(lat, lon, self._home_amsl + z, yaw)
        except Exception as error:
            raise FlightError(f"飞往目标点的指令被拒绝：{error}") from error
        dist = math.dist((self.state.x, self.state.y, self.state.z), (x, y, z))
        limit = timeout or (dist / CRUISE_SPEED * 2.5 + 15)
        await self._wait(lambda: math.dist((self.state.x, self.state.y, self.state.z), (x, y, z)) < tolerance
                         and abs(((self.state.yaw - yaw + 180) % 360) - 180) < 8, limit, f"未能在 {limit:.0f} 秒内到达 ({x:.1f}, {y:.1f}, {z:.1f})")
        return time.time() - started

    async def point_gimbal(self, pitch: float, settle: float = 2.0) -> None:
        """设置云台俯仰。飞控重新上锁后云台控制权会丢失，失败时重新获取控制权再试一次。"""
        pitch = max(-90.0, min(20.0, pitch))
        for attempt in range(2):
            try:
                if self._gimbal_id is None or attempt:
                    async for gl in self.gimbal.subscribe_gimbal_list():
                        if gl.gimbals:
                            self._gimbal_id = gl.gimbals[0].gimbal_id
                            break
                    await self.gimbal.take_control(self._gimbal_id, ControlMode.PRIMARY)
                await self.gimbal.set_angles(self._gimbal_id, 0.0, pitch, 0.0, GimbalMode.YAW_FOLLOW, SendMode.ONCE)
                break
            except Exception as error:
                if attempt:
                    raise FlightError(f"云台控制失败：{error}") from error
                await asyncio.sleep(1)
        self.state.gimbal_pitch = pitch
        await asyncio.sleep(settle)   # 等云台转到位、画面稳定

    async def return_home(self, timeout: float = 240.0) -> None:
        """沿当前高度水平飞回起飞点上空，再垂直降落（航段已由安全员校验）。"""
        await self.goto(0.0, 0.0, max(self.state.z, 5.0), self.state.yaw, tolerance=1.0)
        await self.land(timeout)

    async def failsafe_return(self) -> None:
        """飞控自带的返航（失联保护同款），用于本程序航线执行失败时。"""
        try:
            await self.action.return_to_launch()
        except Exception as error:
            raise FlightError(f"返航指令被拒绝：{error}") from error

    async def land(self, timeout: float = 120.0) -> None:
        try:
            await self.action.land()
        except Exception as error:
            raise FlightError(f"降落指令被拒绝：{error}") from error
        # 落地后飞控会自动上锁；以“不在空中”或“已上锁”任一为准，避免某个遥测流延迟导致误判超时
        await self._wait(lambda: not self.state.in_air or not self.state.armed, timeout, "降落超时")

    async def hold(self) -> None:
        await self.action.hold()

    async def set_param(self, name: str, value: float) -> None:
        if isinstance(value, int) or float(value).is_integer() and name.endswith(("_EN", "_MODE", "_N_CELLS")):
            await self.param.set_param_int(name, int(value))
        else:
            await self.param.set_param_float(name, float(value))

    async def get_param(self, name: str) -> float:
        try:
            return await self.param.get_param_float(name)
        except Exception:
            return float(await self.param.get_param_int(name))

    async def inject_failure(self, unit: str, kind: str = "OFF", instance: int = 0) -> None:
        """故障注入（仅仿真，需 SYS_FAILURE_EN=1）。unit 如 SYSTEM_MOTOR、SENSOR_GPS。"""
        await self.param.set_param_int("SYS_FAILURE_EN", 1)
        await self.failure.inject(getattr(FailureUnit, unit), getattr(FailureType, kind), instance)

    async def _wait(self, condition, timeout: float, message: str) -> None:
        deadline = time.time() + timeout
        while not condition():
            if time.time() > deadline:
                raise FlightError(message)
            await asyncio.sleep(0.2)
