"""占用飞机与释放：任何失败路径都不能让飞机一直处于“有任务在执行”（DR-058）。"""
import asyncio

from droneagent import store
from droneagent.agents import runtime, troubleshoot
from droneagent.flight import FlightError


def _new(task="排障"):
    return store.create_mission(task, "solar_7")


def test_troubleshoot_releases_when_flight_connection_fails(monkeypatch):
    mid = _new()
    async def boom():
        raise FlightError("飞控连接超时")
    monkeypatch.setattr(runtime, "flight", boom)
    runtime.active_mission = None
    asyncio.run(troubleshoot.job(mid, "抖动", None))
    assert runtime.active_mission is None
    assert store.mission_status(mid) == "failed"
    assert any("飞控连接超时" in e["message"] for e in store.events(mid, 0))


def test_reconcile_releases_finished_mission():
    mid = _new()
    runtime.active_mission = mid
    store.update_mission(mid, status="cancelled")
    assert runtime.reconcile(store.mission_status) == mid and runtime.active_mission is None
    live = _new()
    runtime.active_mission = live
    store.update_mission(live, status="running")
    assert runtime.reconcile(store.mission_status) is None and runtime.active_mission == live   # 还在执行的不动
    runtime.active_mission = None
