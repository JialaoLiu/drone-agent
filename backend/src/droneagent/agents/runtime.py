"""运行时：一条专用的 asyncio 事件循环线程，持有唯一的飞控连接，执行所有任务。

为什么只有一条连接：MAVSDK 监听 UDP 14540，同一进程里重复监听会冲突；一架飞机同一时间也只执行一个任务。
网页接口线程通过 submit() 把协程交给这条循环执行。
"""
from __future__ import annotations

import asyncio
import threading
import time

from .. import config
from ..flight import Flight
from ..geo import Origin
from ..sim.scene import ORIGIN, load_site

_loop: asyncio.AbstractEventLoop | None = None
_flight: Flight | None = None
_flight_lock: asyncio.Lock | None = None
_mission_lock = threading.Lock()
active_mission: str | None = None
telemetry: dict = {}             # 最近一次遥测快照，供网页轮询 / WebSocket 推送


def loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None:
        _loop = asyncio.new_event_loop()
        threading.Thread(target=_loop.run_forever, daemon=True, name="mission-loop").start()
    return _loop


_jobs: dict = {}                 # 任务编号 -> 后台执行的 Future（取消卡住的任务时用）


def submit(coro, mission_id: str | None = None):
    """在任务循环中执行协程，返回 concurrent.futures.Future。给出 mission_id 时登记，便于取消。"""
    future = asyncio.run_coroutine_threadsafe(coro, loop())
    if mission_id:
        _jobs[mission_id] = future
        future.add_done_callback(lambda _f, m=mission_id: _jobs.pop(m, None) if _jobs.get(m) is _f else None)
    return future


def job_running(mission_id: str) -> bool:
    f = _jobs.get(mission_id)
    return f is not None and not f.done()


def force_stop(mission_id: str) -> bool:
    """强制结束一个卡住的后台任务（只在飞机不在空中时使用），并释放占用。返回是否确实结束了什么。"""
    f = _jobs.get(mission_id)
    stopped = bool(f and not f.done() and f.cancel())
    end_run(mission_id)
    release(mission_id)
    return stopped


LIVE_STATUSES = ("planning", "awaiting_approval", "running")


def reconcile(status_of) -> str | None:
    """自愈：被占用的任务在数据库里已经结束，且后台没有它的执行实例时，释放占用（DR-058）。
    status_of：任务编号 -> 数据库中的状态。返回被释放的任务编号。"""
    mid = active_mission
    if not mid:
        return None
    with _mission_lock:
        running = mid in _running
    if status_of(mid) not in LIVE_STATUSES and not running and not job_running(mid):
        release(mid)
        return mid
    return None


_origin_cache: dict = {"at": 0.0, "scene": None, "origin": ORIGIN}


def scene_origin() -> dict:
    """当前仿真场景的经纬度原点（真实场地不是 PX4 默认坐标）。按 2 秒缓存，避免频繁查询进程。"""
    if time.time() - _origin_cache["at"] > 2:
        from .. import simctl
        scene = simctl.current_scene()
        origin = ORIGIN
        if scene:
            try:
                origin = load_site(scene)["origin"]
            except (FileNotFoundError, OSError, KeyError):
                origin = ORIGIN
        _origin_cache.update(at=time.time(), scene=scene, origin=origin)
    return _origin_cache["origin"]


def _sync_origin(f: Flight) -> None:
    """本地坐标的原点必须与仿真场景一致：切换到真实场地后，原点从 PX4 默认坐标变为该场地的经纬度。"""
    o = scene_origin()
    if (f.origin.lat, f.origin.lon) != (o["lat"], o["lon"]):
        f.origin = Origin(o["lat"], o["lon"], o.get("alt", 0.0))
        f._home_amsl = None          # 起飞点海拔随场地变化，重新从下一帧位置取得


async def flight() -> Flight:
    """取得飞控连接；首次调用或连接失效时重新连接。"""
    global _flight, _flight_lock
    if _flight_lock is None:
        _flight_lock = asyncio.Lock()
    async with _flight_lock:
        if _flight is not None and time.time() - _flight.state.updated < 5:
            _sync_origin(_flight)
            return _flight
        # 首次连接，或仿真重启后遥测中断：在同一个 MAVSDK 实例上重新发现飞控
        o = scene_origin()
        f = _flight or Flight(Origin(o["lat"], o["lon"], o.get("alt", 0.0)), config.MAVLINK_URL)
        _sync_origin(f)
        await f.connect(timeout=20)
        if not f.listeners:
            f.listeners.append(lambda s: telemetry.update(s.as_dict()))
        _flight = f
        for _ in range(50):        # 等第一帧位置
            if f.state.updated:
                break
            await asyncio.sleep(0.1)
        return f


def try_claim(mission_id: str) -> bool:
    """同一时间只允许一个任务飞行。"""
    global active_mission
    with _mission_lock:
        if active_mission and active_mission != mission_id:
            return False
        active_mission = mission_id
        return True


_running: set[str] = set()


def begin_run(mission_id: str) -> bool:
    """登记一个任务执行实例；同一任务已有实例在执行时返回 False。"""
    with _mission_lock:
        if mission_id in _running:
            return False
        _running.add(mission_id)
        return True


def end_run(mission_id: str) -> None:
    with _mission_lock:
        _running.discard(mission_id)


def current_flight():
    """已建立的飞控连接（没有时返回 None，不触发连接）。"""
    return _flight


def release(mission_id: str) -> None:
    global active_mission
    with _mission_lock:
        if active_mission == mission_id:
            active_mission = None
