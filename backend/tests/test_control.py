"""2026-10-16 深度审查问题的回归测试：异常返航、取消接管、订阅超时、重复执行、初筛规范化、报告事实、排障安全与参数恢复。
全部使用替身飞控与替身模型，不连接仿真、不调用模型。"""
import asyncio
import json
import types

import pytest

from droneagent import flight as flight_mod
from droneagent import health, store, vision
from droneagent.agents import mission, report, roles, runtime, troubleshoot
from droneagent.flight import Flight, FlightError, Preempted
from droneagent.geo import Origin
from droneagent.llm import ModelUnavailable, Usage
from droneagent.sim import scene

USAGE = Usage("m", 1, 1, 0, 1, 0.0)


class FakeAction:
    def __init__(self, log):
        self.log = log

    async def goto_location(self, *a):
        self.log.append(("goto", a))

    async def return_to_launch(self):
        self.log.append(("rtl",))

    async def land(self):
        self.log.append(("land",))

    async def set_takeoff_altitude(self, *a):
        pass

    async def set_current_speed(self, *a):
        pass

    async def arm(self):
        self.log.append(("arm",))

    async def takeoff(self):
        self.log.append(("takeoff",))


def fake_flight(log):
    f = Flight(Origin(47.397971, 8.546163, 488.0), "udp://:0")
    f.action = FakeAction(log)
    f._home_amsl = 488.0
    f._health = (1e18, {"armable": True, "global_position": True, "home": True})
    return f


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------- 飞行控制 ----------

def test_goto_refused_after_rtl_takeover():
    log = []
    f = fake_flight(log)
    run(f.failsafe_return())
    with pytest.raises(Preempted):
        run(f.goto(10, 0, 20, 0))
    assert ("rtl",) in log and not any(e[0] == "goto" for e in log)


def test_cancel_stops_wait_and_next_command():
    log = []
    f = fake_flight(log)
    cancelled = {"v": False}
    f.begin_control(lambda: "任务已取消" if cancelled["v"] else None)

    async def scenario():
        async def cancel_soon():
            await asyncio.sleep(0.3)
            cancelled["v"] = True
        asyncio.ensure_future(cancel_soon())
        with pytest.raises(Preempted):
            await f.goto(40, 0, 20, 0, timeout=10)          # 永远到不了，取消后应立即结束等待
        with pytest.raises(Preempted):
            await f.goto(50, 0, 20, 0)                      # 下一条普通指令不再发送
    run(scenario())
    assert sum(1 for e in log if e[0] == "goto") == 1
    # 取消后返航与降落仍可执行（land 不检查取消）
    f.end_control()
    f.state.in_air = False
    run(f.land(timeout=0.5))
    assert ("land",) in log


def test_wait_ready_times_out_without_health():
    f = fake_flight([])
    f._health = None
    with pytest.raises(FlightError):
        run(asyncio.wait_for(f.wait_ready(timeout=0.3), 2))


def test_gimbal_list_never_arrives(monkeypatch):
    f = fake_flight([])

    class SilentGimbal:
        async def subscribe_gimbal_list(self):
            await asyncio.sleep(3600)
            yield None
    f.gimbal = SilentGimbal()
    monkeypatch.setattr(flight_mod, "GIMBAL_LIST_TIMEOUT", 0.2)
    with pytest.raises(FlightError):
        run(asyncio.wait_for(f.point_gimbal(-60, settle=0), 3))


def test_return_home_when_takeoff_half_succeeded(monkeypatch, site):
    """起飞已离地但没到高度就报错：返航节点要以遥测为准执行返航（H01）。"""
    log = []
    f = fake_flight(log)

    async def failing_takeoff(altitude, timeout=60.0):
        f.state.in_air, f.state.armed, f.state.z = True, True, 4.0
        raise FlightError("起飞未在规定时间内到达高度")

    async def landed_return(timeout=240.0):
        log.append(("return_home",))
        f.state.in_air = f.state.armed = False
    f.takeoff = failing_takeoff
    f.return_home = landed_return

    async def get_flight():
        return f

    async def ok_preflight(_f, sample_seconds=2.0):
        return {"ok": True, "items": [], "advice": ""}

    async def no_diag(*a, **k):
        return {"text": "测试"}
    monkeypatch.setattr(runtime, "flight", get_flight)
    monkeypatch.setattr(health, "preflight", ok_preflight)
    monkeypatch.setattr(mission.diagnose, "explain_failure", no_diag)
    monkeypatch.setattr(mission, "_site", lambda s: site)
    mid = store.create_mission("测试", "solar_7")
    store.update_mission(mid, status="running")
    state = {"mission_id": mid, "plan": {"standoff_m": 20, "sorties": [["A"]]}, "sortie": 0,
             "steps": [{"kind": "overview", "position": [0, -20, 18]}]}
    out = run(mission.takeoff(state))
    assert out["outcome"] == "failed" and out["airborne"]
    state.update(out)
    run(mission.return_home(state))
    assert ("return_home",) in log


def test_takeoff_skipped_when_cancelled(monkeypatch):
    log = []
    f = fake_flight(log)

    async def get_flight():
        return f
    monkeypatch.setattr(runtime, "flight", get_flight)
    mid = store.create_mission("测试", "solar_7")
    store.update_mission(mid, status="cancelled")
    out = run(mission.takeoff({"mission_id": mid, "plan": {"standoff_m": 20, "sorties": [["A"]]}, "steps": []}))
    assert out["outcome"] == "cancelled" and not log


def test_same_mission_runs_once():
    assert runtime.begin_run("m1") and not runtime.begin_run("m1")
    runtime.end_run("m1")
    assert runtime.begin_run("m1")
    runtime.end_run("m1")
    mid = store.create_mission("测试", "solar_7")
    store.update_mission(mid, status="awaiting_approval")
    assert store.update_mission(mid, status="planning", only_from=("awaiting_approval",))
    assert not store.update_mission(mid, status="planning", only_from=("awaiting_approval",))   # 第二个确认请求不会再恢复


# ---------- 判断可信度 ----------

def _tiles(ids):
    return [types.SimpleNamespace(panel_id=i, image=None) for i in ids]


@pytest.mark.parametrize("panels, expected", [
    ([{"id": "A-R1-C01", "status": "maybe", "type": "crack", "confidence": 0.9}], "defect"),
    ([{"id": "A-R1-C01", "status": "缺陷", "type": "crack"}], "defect"),
    ([{"id": "A-R1-C01", "status": "maybe", "type": None, "confidence": 0.2}], "unsure"),
])
def test_screen_normalizes_status(monkeypatch, panels, expected):
    monkeypatch.setattr(vision, "contact_sheet", lambda t: b"")
    monkeypatch.setattr(vision, "chat_json", lambda *a, **k: ({"panels": panels}, USAGE))
    out, _ = vision.screen(_tiles(["A-R1-C01", "A-R1-C02"]))
    assert [c["status"] for c in out] == [expected]


def test_screen_bad_structure_marks_sheet_unsure(monkeypatch):
    monkeypatch.setattr(vision, "contact_sheet", lambda t: b"")
    monkeypatch.setattr(vision, "chat_json", lambda *a, **k: ({"result": "全部正常"}, USAGE))
    out, _ = vision.screen(_tiles(["A-R1-C01", "A-R1-C02"]))
    assert {c["id"] for c in out} == {"A-R1-C01", "A-R1-C02"} and all(c["status"] == "unsure" for c in out)


def test_screen_failure_is_not_reported_as_clean(site, monkeypatch):
    mid = store.create_mission("测试", "solar_7")
    state = {"mission_id": mid, "current": "A", "screen_failed": True, "candidates": [], "inspected": [], "queue": [],
             "plan": {"closeup_policy": "flagged_and_unsure", "standoff_m": 20, "sorties": [["A"]]}, "brief": {"reserve_pct": 25}}
    monkeypatch.setattr(mission, "_site", lambda s: site)
    out = run(mission.closeups(state))
    assert out == {"unjudged": ["A"]}
    result = report.collect({**state, **out}, site, 80.0)
    assert result["panels_inspected"] == 0 and result["unjudged_arrays"] == ["A"]
    assert "未完成判断" in report.facts_sentence(result)


def test_reporter_advice_cannot_carry_numbers():
    valid = {"A-R2-C06", "B-R1-C01"}
    assert roles.check_advice("建议先更换 A-R2-C06，再清洗 B-R1-C01。", valid)
    assert not roles.check_advice("确认999个缺陷，耗电99%，全部正常。", valid)
    assert not roles.check_advice("建议先处理 C-R9-C09。", valid)               # 不存在的编号
    assert not roles.check_advice("共有两块板需要更换。", valid)


def test_report_facts_come_from_program(site):
    mid = store.create_mission("测试", "solar_7")
    store.upsert_finding(mid, "A-R1-C01", status="confirmed", type="crack", confidence=0.9)
    result = report.collect({"mission_id": mid, "inspected": ["A"], "plan": {"closeup_policy": "all"}}, site, 70.0)
    text = report.facts_sentence(result)
    assert "确认缺陷 1 块" in text and str(result["panels_inspected"]) in text


# ---------- 排障 ----------

@pytest.mark.parametrize("seed", [25, 41, 57, 94])
def test_troubleshoot_route_passes_safety(seed, tmp_path):
    from droneagent import safety
    out = scene.generate(seed, f"t{seed}", out_dir=tmp_path, textures=False)
    site = json.loads((out / "site.json").read_text())
    pts = troubleshoot.test_route(site)
    assert not [v for i, (a, b) in enumerate(zip(pts, pts[1:])) for v in safety.check_segment(site, a, b, i)]


def test_troubleshoot_resets_params_when_model_down(monkeypatch):
    calls = {"apply": [], "reset": 0}
    f = fake_flight([])

    async def get_flight():
        return f

    async def fake_test_flight(_f, route=None):
        return {"metrics": {}, "observations": [], "messages": []}

    async def fake_apply(_f, params):
        calls["apply"].append(params)

    async def fake_read(_f):
        return {"MC_ROLL_P": 1.2}

    async def fake_reset(_f):
        calls["reset"] += 1

    def down(*a, **k):
        raise ModelUnavailable("断网")
    monkeypatch.setattr(runtime, "flight", get_flight)
    monkeypatch.setattr(troubleshoot, "test_flight", fake_test_flight)
    monkeypatch.setattr(troubleshoot, "apply", fake_apply)
    monkeypatch.setattr(troubleshoot, "read_params", fake_read)
    monkeypatch.setattr(troubleshoot, "reset_to_reference", fake_reset)
    monkeypatch.setattr(troubleshoot, "chat_json", down)
    monkeypatch.setattr(troubleshoot, "_current_site", lambda: None)
    monkeypatch.setattr(troubleshoot, "reference", lambda: {"MC_ROLL_P": 0.15})
    mid = store.create_mission("测试", "eval")
    result = run(troubleshoot.run(mid, "晃动", "attitude_gain_low"))
    assert result["status"] == "failed" and calls["apply"] and calls["reset"] == 1
