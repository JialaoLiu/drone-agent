"""应急救援任务（LangGraph）：调度员 → 规划员 → 安全员 → 人工确认 → 起飞 → 搜索（发现与确认） → 返航 → 报告。

与光伏巡检共用：人工确认、返航、飞行失败处理、事件与用量记录、飞行前检查、安全规则（围栏、禁飞区、障碍物、高度、电量）。
救援自己的部分：任务理解（目标特征、最后出现的地标）、搜索航线、发现与确认、报告。
"""
from __future__ import annotations

import asyncio
import math
import time
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .. import health, safety, store, survey
from ..flight import FlightError
from ..llm import ModelUnavailable
from ..rescue import detect, plan as search_plan, roles
from ..sim import camera
from ..sim.camera import look_angles
from . import mission, runtime
from .mission import _cancelled, _emit, _flight_failure, _preflight_text, _shoot, _usage, approval, request_approval, return_home

MAX_REVISIONS = 2
CONFIRM_MATCH = ("yes",)            # 变焦确认时必须明确相符
SCREEN_FOLLOW = ("yes", "partial")  # 初筛相符或部分相符就去确认


class RescueState(TypedDict, total=False):
    mission_id: str
    task: str
    scene: str
    auto_approve: bool
    brief: dict
    plan: dict
    steps: list[dict]
    violations: list[dict]
    revisions: int
    queue: list[str]
    sorties: list[list[str]]
    sortie: int
    inspected: list[str]
    skipped: list[str]
    airborne: bool
    started_at: float
    battery_start: float
    sortie_battery: float
    battery_used: float
    detections: list[dict]
    found: dict
    searched: int
    error: str
    outcome: str


def _site(state: RescueState) -> dict:
    return mission._site(state)


async def coordinator(state: RescueState) -> dict:
    site = _site(state)
    try:
        brief, usage = await asyncio.to_thread(roles.understand, state["task"], site)
    except ModelUnavailable as error:
        _emit(state, "coordinator", "error", f"调度员不可用：{error}")
        return {"outcome": "failed", "error": str(error)}
    _usage(state, "coordinator", usage)
    store.update_mission(state["mission_id"], brief={**brief, "scenario": "rescue"})
    if brief["type"] == "clarify":
        _emit(state, "coordinator", "clarify", brief["question"])
        return {"brief": brief, "outcome": "clarify"}
    if brief["type"] == "unsupported":
        _emit(state, "coordinator", "unsupported", brief["reason"])
        return {"brief": brief, "outcome": "unsupported"}
    label = {l["id"]: l["label"] for l in site["landmarks"]}[brief["last_seen"]]
    _emit(state, "coordinator", "brief", f"任务理解：寻找{brief['description']}，最后出现在{label}"
          + ("；情况紧急" if brief["urgency"] == "high" else ""), brief)
    return {"brief": brief, "revisions": 0, "detections": [], "searched": 0}


async def planner(state: RescueState) -> dict:
    site = _site(state)
    f = await runtime.flight()
    try:
        params, usage = await asyncio.to_thread(roles.plan, state["brief"], site, f.state.battery, state.get("violations"), state.get("plan"))
    except ModelUnavailable as error:
        _emit(state, "planner", "error", f"规划员不可用：{error}")
        return {"outcome": "failed", "error": str(error)}
    _usage(state, "planner", usage)
    if state.get("violations") and any(v["code"] == "battery" for v in state["violations"]) and params["radius_m"] >= (state.get("plan") or {}).get("radius_m", 99):
        params["radius_m"] = round(state["plan"]["radius_m"] * 0.75, 1)        # 规划员没有缩小时，程序按电量缩小半径
    steps = search_plan.search_steps(site, params["center"], params["radius_m"], params["altitude_m"], params["pattern"])
    route = survey.with_detours(steps, site)
    minutes = round(survey.route_seconds(route) / 60, 1)
    params.update(sorties=[["搜索"]], targets=["搜索"], closeup_policy="search", standoff_m=params["altitude_m"], max_sorties=1,
                  estimated_minutes=minutes, estimated_battery=[round(safety.battery_needed(route), 1)], photos=len(steps),
                  routes=[[s.as_dict() for s in route]], route=[s.as_dict() for s in route])
    pattern = "扩展方形" if params["pattern"] == "expanding" else "平行扫描"
    _emit(state, "planner", "plan", f"计划：以{params['last_seen_label']}为中心，{pattern}搜索半径 {params['radius_m']:.0f} 米，高度 {params['altitude_m']:.0f} 米，"
          f"{len(steps)} 个拍摄点，预计 {minutes} 分钟。{params['rationale']}", {**params, "route": [s.as_dict() for s in route]})
    return {"plan": params, "steps": [s.as_dict() for s in route]}


async def safety_check(state: RescueState) -> dict:
    site = _site(state)
    f = await runtime.flight()
    route = [survey.Step(**s) for s in state["steps"]]
    violations = safety.as_dicts(safety.check_plan(site, route, battery=f.state.battery, reserve=25.0))
    if violations:
        _emit(state, "safety", "violation", "安全员拒绝：" + "；".join(v["message"] for v in violations), {"violations": violations})
        store.update_mission(state["mission_id"], plan={**state["plan"], "violations": violations})
        return {"violations": violations, "revisions": state.get("revisions", 0) + 1}
    check = await health.preflight(f)
    plan = {**state["plan"], "violations": [], "preflight": check}
    store.update_mission(state["mission_id"], plan=plan)
    if not check["ok"]:
        _emit(state, "safety", "preflight", _preflight_text(check), check)
        return {"plan": plan, "outcome": "failed", "error": check["advice"]}
    _emit(state, "safety", "approved", "计划通过安全校验：围栏、禁飞区、障碍物净空、高度、电量余量均满足")
    return {"plan": plan, "violations": []}


def after_safety(state: RescueState) -> str:
    if state.get("outcome"):
        return "end"
    if state.get("violations"):
        return "planner" if state.get("revisions", 0) <= MAX_REVISIONS else "rejected"
    return "request_approval"


async def rejected(state: RescueState) -> dict:
    store.update_mission(state["mission_id"], status="rejected")
    return {"outcome": "rejected"}


async def takeoff(state: RescueState) -> dict:
    f = await runtime.flight()
    f.begin_control(lambda: "任务已取消" if _cancelled(state) else None)
    if _cancelled(state):
        return {"outcome": "cancelled"}
    check = await health.preflight(f)
    if not check["ok"]:
        _emit(state, "safety", "preflight", _preflight_text(check), check)
        return {"error": check["advice"], "outcome": "failed"}
    altitude = state["plan"]["altitude_m"]
    _emit(state, "pilot", "takeoff", f"起飞，爬升到 {altitude:.0f} 米（当前电量 {f.state.battery:.0f}%）")
    out = {"started_at": time.time(), "battery_start": f.state.battery, "sortie_battery": f.state.battery}
    try:
        await f.takeoff(altitude)
    except FlightError as error:
        return {**out, **(await _flight_failure(state, error))}
    return {**out, "airborne": True}


def _own_shadow(site: dict, frame, c: dict, radius: float = 2.5) -> bool:
    """候选是否就是无人机自己的影子：影子位置由太阳方向和飞机高度算出（真实系统按日期时间计算太阳方位）。"""
    sun = site.get("sun")
    if not sun or sun[2] >= -0.05:
        return False
    x, y, z = frame.pose.position
    sx, sy = x - z * sun[0] / sun[2], y - z * sun[1] / sun[2]
    return math.hypot(c["x"] - sx, c["y"] - sy) < radius


def _classify(item: dict) -> str:
    if item["person"] == "no":
        return "object"
    if item["person"] == "unsure":
        return "unsure"
    if item.get("in_water") == "yes":
        return "candidate"          # 水里有人本身就是紧急情况，衣服在水下看不清颜色，不能按衣着排除（工程日志 43）
    return "candidate" if item["match"] in SCREEN_FOLLOW else "bystander"


async def _confirm(state: RescueState, item: dict, index: int) -> tuple[dict | None, dict]:
    """在原地用变焦相机对准候选确认。返回（找到时的目标信息, 确认结果）。目标走开时在原位置上方补拍一次。"""
    f = await runtime.flight()
    site = _site(state)
    desc = state["brief"]["description"]
    for attempt in range(2):
        here = (f.state.x, f.state.y, f.state.z)
        yaw, pitch = look_angles(here, (item["x"], item["y"], 0.8))
        step = survey.Step("confirm", item["id"], here, yaw, pitch, "zoom", "确认")
        frame, pid = await _shoot(state, step, index)
        verdict, usage = await asyncio.to_thread(detect.verify, frame, desc)
        _usage(state, "searcher", usage)
        verdict["photo"] = pid
        in_water = verdict["person"] == "yes" and verdict["in_water"] == "yes"
        # 模型报告看到的属性，程序比对：衣着对上、年龄不矛盾即确认（模型常因“看不出年龄”只给部分相符，工程日志 43）
        attrs = verdict["person"] == "yes" and roles.attributes_match(state["brief"], verdict)
        verdict["attributes_match"] = attrs
        if verdict["person"] == "yes" and (verdict["match"] in CONFIRM_MATCH or attrs or in_water):
            pos = await asyncio.to_thread(detect.locate_in_zoom, frame)
            x, y = pos if pos else (item["x"], item["y"])
            return {"x": round(x, 2), "y": round(y, 2), "in_water": verdict["in_water"] == "yes", "photo": pid,
                    "clothing_unverified": in_water and verdict["match"] not in CONFIRM_MATCH and not attrs,
                    "evidence": verdict["evidence"], "confidence": verdict["confidence"], "found_at": time.time()}, verdict
        # 初筛明明是人、变焦里却没看到人（或看不清）：多半已经走开了（从发现到对准要十来秒，变焦画面只有约 4 米宽）。
        # 飞到候选位置正上方，用广角重新找一次附近的人（工程日志 43）
        if verdict["person"] == "yes" or attempt or item.get("person") != "yes":
            return None, verdict
        _emit(state, "pilot", "retake", f"{item['id']} 的变焦画面里没有看到人，可能已经走开，飞到上方重新寻找")
        await f.goto(item["x"], item["y"], state["plan"]["altitude_m"], f.state.yaw)
        wide, _ = await _shoot(state, survey.Step("search", item["id"] + "R", (item["x"], item["y"], state["plan"]["altitude_m"]), 0, -90, "wide", "补搜"), index + 1)
        near = [c for c in await asyncio.to_thread(detect.candidates, wide)
                if math.hypot(c["x"] - item["x"], c["y"] - item["y"]) < 10 and not _own_shadow(site, wide, c)]
        if not near:
            return None, verdict
        best = min(near, key=lambda c: math.hypot(c["x"] - item["x"], c["y"] - item["y"]))     # 离上次位置最近的那个
        item = {**item, "x": best["x"], "y": best["y"]}
        await f.point_gimbal(-90, settle=1.0)
    return None, verdict


async def search(state: RescueState) -> dict:
    """按航线逐点搜索；初筛相符就地确认；找到目标或电量不够时结束。"""
    f = await runtime.flight()
    site = _site(state)
    desc = state["brief"]["description"]
    detections: list[dict] = list(state.get("detections") or [])
    points = [survey.Step(**s) for s in state["steps"] if s["kind"] == "search"]
    reserve = 25.0
    searched = 0
    for i, step in enumerate(points):
        if _cancelled(state):
            return {"outcome": "cancelled", "detections": detections, "searched": searched}
        back = safety.battery_needed([survey.Step("transit", "", (0, 0, step.position[2]), 0, 0, "")], start=(f.state.x, f.state.y, f.state.z))
        if f.state.battery - back < reserve + 3:
            _emit(state, "coordinator", "stop_search", f"电量 {f.state.battery:.0f}%，只够返航，停止搜索（已搜 {searched}/{len(points)} 个点）")
            break
        try:
            frame, pid = await _shoot(state, step, len(store.photos(state["mission_id"])))
        except (FlightError, camera.CameraUnavailable) as error:
            return {**(await _flight_failure(state, error)), "detections": detections, "searched": searched}
        searched += 1
        cands = [c for c in await asyncio.to_thread(detect.candidates, frame) if not _own_shadow(site, frame, c)]
        try:
            items, usage = await asyncio.to_thread(detect.screen, frame, cands, desc)
        except ModelUnavailable as error:
            _emit(state, "searcher", "error", f"视觉模型不可用：{error}，本点只保留照片")
            continue
        if usage:
            _usage(state, "searcher", usage)
        found = None
        new = []
        for k, it in enumerate(items):
            # 同一个人在相邻照片里会重复出现：3 米内已有记录的不再重复确认
            if any(math.hypot(it["x"] - d["x"], it["y"] - d["y"]) < 3 and d["kind"] in ("bystander", "rejected") for d in detections):
                continue
            det = {"id": f"P{len(detections) + 1}", "x": it["x"], "y": it["y"], "kind": _classify(it), "photo": pid, "person": it["person"], "top": it.get("top"),
                   "bottom": it.get("bottom"), "child": it.get("child"), "in_water": it.get("in_water") == "yes", "evidence": it["evidence"],
                   "confidence": it["confidence"], "step": step.target}
            detections.append(det)
            new.append(det)
            if det["kind"] == "candidate":
                _emit(state, "searcher", "candidate", f"{step.target}：发现疑似目标 {det['id']}（{det['evidence']}），用变焦确认", det)
                try:
                    hit, verdict = await _confirm(state, det, len(store.photos(state["mission_id"])))
                except (FlightError, camera.CameraUnavailable) as error:
                    return {**(await _flight_failure(state, error)), "detections": detections, "searched": searched}
                except ModelUnavailable as error:
                    _emit(state, "searcher", "error", f"确认时视觉模型不可用：{error}")
                    det["kind"] = "unsure"
                    continue
                det["confirm"] = verdict
                if hit:
                    det["kind"] = "target"
                    found = {**hit, "id": det["id"], "elapsed_s": round(hit["found_at"] - state["started_at"])}
                    head = "在水中发现人员（衣着在水下无法确认）" if hit.get("clothing_unverified") else f"确认找到目标 {det['id']}"
                    _emit(state, "searcher", "found", f"{head}：{hit['evidence']}" + ("；需立即救援" if hit["in_water"] else ""), found)
                    break
                det["kind"] = "rejected"
                _emit(state, "searcher", "rejected", f"{det['id']} 不是要找的人：{verdict['evidence']}")
        people = sum(1 for d in new if d["kind"] in ("bystander", "rejected", "candidate", "target", "unsure"))
        _emit(state, "searcher", "screen", f"{step.target}（{i + 1}/{len(points)}）：照片里 {len(cands)} 处可疑，判断为人 {people} 处", {"photo": pid, "detections": new})
        if found:
            return {"detections": detections, "found": found, "searched": searched}
    return {"detections": detections, "searched": searched}


async def report(state: RescueState) -> dict:
    from ..rescue import report as rescue_report
    f = await runtime.flight()
    result = rescue_report.collect(state, _site(state), f.state.battery)
    result["usage"] = store.usage_summary(state["mission_id"])["total"]
    result["report_path"] = await asyncio.to_thread(rescue_report.render, state["mission_id"], state["task"], _site(state), result)
    status = state.get("outcome") or "done"
    store.update_mission(state["mission_id"], result=result, status=status if status in ("failed", "cancelled") else "done")
    _emit(state, "reporter", "report", result["summary"])
    return {}


def build() -> StateGraph:
    g = StateGraph(RescueState)
    for name, fn in [("coordinator", coordinator), ("planner", planner), ("safety", safety_check), ("rejected", rejected),
                     ("request_approval", request_approval), ("approval", approval), ("takeoff", takeoff), ("search", search),
                     ("return_home", return_home), ("report", report)]:
        g.add_node(name, fn)
    g.add_edge(START, "coordinator")
    g.add_conditional_edges("coordinator", lambda s: END if s.get("outcome") else "planner", ["planner", END])
    g.add_conditional_edges("planner", lambda s: END if s.get("outcome") else "safety", ["safety", END])
    g.add_conditional_edges("safety", after_safety, {"end": END, "planner": "planner", "rejected": "rejected", "request_approval": "request_approval"})
    g.add_edge("rejected", END)
    g.add_edge("request_approval", "approval")
    g.add_conditional_edges("approval", lambda s: END if s.get("outcome") else "takeoff", ["takeoff", END])
    g.add_conditional_edges("takeoff", lambda s: "return_home" if s.get("outcome") else "search", ["search", "return_home"])
    g.add_edge("search", "return_home")
    g.add_edge("return_home", "report")
    g.add_edge("report", END)
    return g
