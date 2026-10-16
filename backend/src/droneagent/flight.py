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


COMMAND_TIMEOUT = 12.0     # 单条飞控指令等待应答的上限（秒）


async def _cmd(coro, what: str, timeout: float = COMMAND_TIMEOUT):
    """飞控指令都要有上限：仿真重启后飞控偶尔对某类指令一直不应答，没有上限时任务会永远卡住（DR-065）。"""
    try:
        return await asyncio.wait_for(coro, timeout)
    except asyncio.TimeoutError as error:
        raise FlightError(f"{what}没有应答（{timeout:.0f} 秒），请重启仿真后再试") from error


GIMBAL_LIST_TIMEOUT = 5.0      # 等待云台列表的上限（秒）


class Preempted(FlightError):
    """普通飞行指令被拦下：任务已取消，或飞控返航已接管。不是故障，不需要诊断。"""


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
        self.status_log: list[tuple[float, str]] = []   # 带时间的飞控状态消息，供飞行前检查判断“最近”是否有传感器超时
        self._health: tuple[float, dict] | None = None    # 最近一次飞控自检（时间、结果）
        # 指令优先权：飞控返航接管后（preempted 非空），或 cancel_check 返回原因时，普通指令（起飞、航点、云台）一律不再发送。
        # 返航、降落不受限制。
        self.preempted: str | None = None
        self.cancel_check = None          # 无参函数，返回取消原因或 None；由当前执行的任务设置

    def guard(self) -> None:
        reason = self.preempted or (self.cancel_check() if self.cancel_check else None)
        if reason:
            raise Preempted(reason)

    def begin_control(self, cancel_check=None) -> None:
        """新的任务开始控制飞机：飞机在地面时清除上一次的返航接管标记。"""
        if not self.state.in_air:
            self.preempted = None
        self.cancel_check = cancel_check

    def end_control(self) -> None:
        self.cancel_check = None

    async def __aenter__(self) -> "Flight":
        await self.connect()
        return self

    async def __aexit__(self, *_):
        for t in self._tasks:
            t.cancel()

    _mavsdk: dict[str, Mavsdk] = {}     # 每个连接地址只建一个实例：重复监听同一 UDP 端口会冲突

    async def connect(self, timeout: float = 30.0, fresh: bool = False) -> None:
        """fresh 为真时（仿真重启后重连）销毁旧的 MAVSDK 实例再新建：在旧实例上重新发现飞控后，
        参数与部分指令通道会一直不应答（DR-065），遥测却正常，看起来“已就绪”。"""
        for t in self._tasks:
            t.cancel()
        if fresh and self.url in Flight._mavsdk:
            old = Flight._mavsdk.pop(self.url)
            await asyncio.sleep(0.2)          # 让已取消的订阅先退出
            try:
                old.destroy()
            except Exception:                 # noqa: BLE001  销毁失败不影响新建
                pass
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
                                                         self._watch_attitude(), self._watch_status(), self._watch_health())]
        # 默认参数在后台逐个写入、每个最多等 3 秒：仿真重启后个别参数可能一直收不到应答，
        # 写在连接流程里会让连接一直不返回，所有飞行前检查都卡到超时（DR-064）
        self._tasks.append(asyncio.create_task(self._apply_defaults()))

    async def _apply_defaults(self) -> None:
        for name, value in DEFAULT_PARAMS.items():
            try:
                await asyncio.wait_for(self.param.set_param_float(name, value), 3.0)
            except Exception:  # 真机或其他飞控没有这些参数、或没有应答，忽略
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
            self.status_log = (self.status_log + [(time.time(), s.text)])[-50:]

    async def _watch_health(self):
        # 常驻订阅：每次检查时临时新建订阅，在仿真重启后会一直收不到数据（实测），所以持续缓存最新结果
        async for h in self.telemetry.subscribe_health():
            self._health = (time.time(), {"gyro": h.is_gyrometer_calibration_ok, "accel": h.is_accelerometer_calibration_ok,
                                          "mag": h.is_magnetometer_calibration_ok, "local_position": h.is_local_position_ok,
                                          "global_position": h.is_global_position_ok, "home": h.is_home_position_ok,
                                          "armable": h.is_armable})

    async def health(self, timeout: float = 5.0) -> dict | None:
        """飞控自检的最新快照（传感器校准、位置估计、可否解锁）；超过 timeout 秒没有新结果返回 None。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._health and time.time() - self._health[0] < timeout:
                return self._health[1]
            await asyncio.sleep(0.2)
        return None

    # ---------- 动作 ----------

    async def wait_ready(self, timeout: float = 90.0) -> None:
        """等待飞控可解锁（定位、航向、起飞点都就绪）。"""
        # 用缓存的自检结果轮询：订阅本身可能不再产出数据，超时必须独立于数据到达（深度审查 H04）
        deadline = time.time() + timeout
        while time.time() < deadline:
            h = self._health[1] if self._health and time.time() - self._health[0] < 5 else None
            if h and h["armable"] and h["global_position"] and h["home"]:
                return
            await asyncio.sleep(0.5)
        raise FlightError("飞控在规定时间内没有就绪（定位或传感器未通过检查）")

    async def takeoff(self, altitude: float, timeout: float = 60.0) -> None:
        self.guard()
        await self.wait_ready()
        self.guard()
        await _cmd(self.action.set_takeoff_altitude(altitude), "设置起飞高度")
        await _cmd(self.action.set_current_speed(CRUISE_SPEED), "设置飞行速度")
        try:
            await _cmd(self.action.arm(), "解锁")
            await _cmd(self.action.takeoff(), "起飞")
        except Exception as error:
            raise FlightError(f"起飞被拒绝：{error}") from error
        await self._wait(lambda: self.state.z >= altitude - 0.8, timeout, "起飞未在规定时间内到达高度", guarded=True)

    async def goto(self, x: float, y: float, z: float, yaw: float, tolerance: float = 0.8, timeout: float | None = None) -> float:
        """飞到本地坐标点，返回实际耗时。超时说明没能到达（风、故障或被拦截）。"""
        if self._home_amsl is None:
            raise FlightError("还没有收到位置信息")
        lat, lon, _ = self.origin.to_global(x, y)
        started = time.time()
        self.guard()
        try:
            await _cmd(self.action.goto_location(lat, lon, self._home_amsl + z, yaw), "飞往目标点")
        except Exception as error:
            raise FlightError(f"飞往目标点的指令被拒绝：{error}") from error
        dist = math.dist((self.state.x, self.state.y, self.state.z), (x, y, z))
        limit = timeout or (dist / CRUISE_SPEED * 2.5 + 15)
        await self._wait(lambda: math.dist((self.state.x, self.state.y, self.state.z), (x, y, z)) < tolerance
                         and abs(((self.state.yaw - yaw + 180) % 360) - 180) < 8, limit, f"未能在 {limit:.0f} 秒内到达 ({x:.1f}, {y:.1f}, {z:.1f})",
                         guarded=True)
        return time.time() - started

    async def point_gimbal(self, pitch: float, settle: float = 2.0) -> None:
        """设置云台俯仰。飞控重新上锁后云台控制权会丢失，失败时重新获取控制权再试一次。"""
        pitch = max(-90.0, min(20.0, pitch))

        async def first_gimbal() -> int:
            async for gl in self.gimbal.subscribe_gimbal_list():
                if gl.gimbals:
                    return gl.gimbals[0].gimbal_id
            raise FlightError("没有找到云台")

        for attempt in range(2):
            self.guard()
            try:
                if self._gimbal_id is None or attempt:
                    # 云台列表订阅可能一直不产出：设上限，超时按云台失败处理（深度审查 H04）
                    self._gimbal_id = await asyncio.wait_for(first_gimbal(), GIMBAL_LIST_TIMEOUT)
                    await _cmd(self.gimbal.take_control(self._gimbal_id, ControlMode.PRIMARY), "云台控制权")
                await _cmd(self.gimbal.set_angles(self._gimbal_id, 0.0, pitch, 0.0, GimbalMode.YAW_FOLLOW, SendMode.ONCE), "云台转动")
                break
            except Preempted:
                raise
            except Exception as error:
                if attempt:
                    raise FlightError(f"云台控制失败：{error or type(error).__name__}") from error
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
            await _cmd(self.action.return_to_launch(), "返航")
        except Exception as error:
            raise FlightError(f"返航指令被拒绝：{error}") from error
        self.preempted = "飞控返航已接管"

    async def land(self, timeout: float = 120.0) -> None:
        try:
            await _cmd(self.action.land(), "降落")
        except Exception as error:
            raise FlightError(f"降落指令被拒绝：{error}") from error
        # 落地后飞控会自动上锁；以“不在空中”或“已上锁”任一为准，避免某个遥测流延迟导致误判超时
        await self._wait(lambda: not self.state.in_air or not self.state.armed, timeout, "降落超时")

    async def hold(self) -> None:
        await _cmd(self.action.hold(), "悬停")

    async def set_param(self, name: str, value: float) -> None:
        if isinstance(value, int) or float(value).is_integer() and name.endswith(("_EN", "_MODE", "_N_CELLS")):
            await _cmd(self.param.set_param_int(name, int(value)), f"设置参数 {name}")
        else:
            await _cmd(self.param.set_param_float(name, float(value)), f"设置参数 {name}")

    async def get_param(self, name: str) -> float:
        try:
            return await _cmd(self.param.get_param_float(name), f"读取参数 {name}")
        except Exception:
            return float(await _cmd(self.param.get_param_int(name), f"读取参数 {name}"))

    async def inject_failure(self, unit: str, kind: str = "OFF", instance: int = 0) -> None:
        """故障注入（仅仿真，需 SYS_FAILURE_EN=1）。unit 如 SYSTEM_MOTOR、SENSOR_GPS。"""
        await _cmd(self.param.set_param_int("SYS_FAILURE_EN", 1), "设置参数 SYS_FAILURE_EN")
        await _cmd(self.failure.inject(getattr(FailureUnit, unit), getattr(FailureType, kind), instance), "故障注入")

    async def _wait(self, condition, timeout: float, message: str, guarded: bool = False) -> None:
        """guarded 为真时（普通指令的等待），取消或返航接管会立即结束等待。"""
        deadline = time.time() + timeout
        while not condition():
            if guarded:
                self.guard()
            if time.time() > deadline:
                raise FlightError(message)
            await asyncio.sleep(0.2)
