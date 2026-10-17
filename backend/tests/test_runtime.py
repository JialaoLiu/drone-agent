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


def test_takeoff_stops_when_cancelled_between_commands():
    """设置起飞高度期间收到取消：之后的设置速度、解锁、起飞都不能再发（审查 H03）。"""
    import pytest
    from droneagent.flight import Flight, Preempted
    from droneagent.geo import Origin
    f = Flight(Origin(0, 0, 0), "udp://:0")
    sent, cancelled = [], {"v": False}
    async def ready(timeout=90.0):
        return None
    class Action:
        async def set_takeoff_altitude(self, a):
            sent.append("alt"); cancelled["v"] = True          # 这条指令进行中被取消
        async def set_current_speed(self, v): sent.append("speed")
        async def arm(self): sent.append("arm")
        async def takeoff(self): sent.append("takeoff")
    f.action = Action()
    f.wait_ready = ready
    f.begin_control(lambda: "已取消" if cancelled["v"] else None, owner="A")
    with pytest.raises(Preempted):
        asyncio.run(f.takeoff(10))
    assert sent == ["alt"]


def test_end_control_only_by_owner():
    from droneagent.flight import Flight
    from droneagent.geo import Origin
    f = Flight(Origin(0, 0, 0), "udp://:0")
    f.begin_control(lambda: None, owner="A")
    f.end_control(owner="B")                    # 另一个任务收尾不能清掉 A 的控制（审查 H06）
    assert f.cancel_check is not None and f.control_owner == "A"
    f.end_control(owner="A")
    assert f.cancel_check is None


def test_sim_start_cancelled_before_side_effects(monkeypatch):
    import pytest
    from droneagent import simctl
    calls = []
    monkeypatch.setattr(simctl, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(simctl.subprocess, "run", lambda *a, **k: calls.append("script"))
    monkeypatch.setattr(simctl, "missing_textures", lambda name: [])
    monkeypatch.setattr(simctl.config, "WORLDS_DIR", simctl.config.DATA_DIR / "worlds")
    (simctl.config.WORLDS_DIR / "s1").mkdir(parents=True, exist_ok=True)
    (simctl.config.WORLDS_DIR / "s1" / "site.json").write_text("{}")
    with pytest.raises(RuntimeError):
        simctl.start("s1", cancelled=lambda: True)
    assert calls == []                          # 作废的启动不能停进程、不能拉起进程（审查 H10）


def test_release_waits_until_grounded():
    mid = _new()
    runtime.active_mission = mid
    store.update_mission(mid, status="failed")
    runtime.telemetry.update(in_air=True, armed=True)
    assert runtime.reconcile(store.mission_status) is None and runtime.active_mission == mid   # 返航超时、仍在空中：不释放（审查 H04）
    runtime.telemetry.update(in_air=False, armed=False)
    assert runtime.reconcile(store.mission_status) == mid
    runtime.telemetry.clear()
