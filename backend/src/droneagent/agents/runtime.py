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
from ..sim.scene import ORIGIN

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


def submit(coro):
    """在任务循环中执行协程，返回 concurrent.futures.Future。"""
    return asyncio.run_coroutine_threadsafe(coro, loop())


async def flight() -> Flight:
    """取得飞控连接；首次调用或连接失效时重新连接。"""
    global _flight, _flight_lock
    if _flight_lock is None:
        _flight_lock = asyncio.Lock()
    async with _flight_lock:
        if _flight is not None and time.time() - _flight.state.updated < 5:
            return _flight
        # 首次连接，或仿真重启后遥测中断：在同一个 MAVSDK 实例上重新发现飞控
        f = _flight or Flight(Origin(**ORIGIN), config.MAVLINK_URL)
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


def release(mission_id: str) -> None:
    global active_mission
    with _mission_lock:
        if active_mission == mission_id:
            active_mission = None
