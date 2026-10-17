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
RESERVE = 25.0                      # 返航后至少保留的电量（%）


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
    label = {l["id"]: l["label"] for l in site["landmarks"]}.get(brief["last_seen"], "未知位置")
    _emit(state, "coordinator", "brief", f"任务理解：寻找{brief['description']}，最后出现在{label}"
          + ("；特征不明，将找出所有人交给人工辨认" if brief.get("search_all") else "")
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
    f.begin_control(lambda: "任务已取消" if _cancelled(state) else None, owner=state["mission_id"])
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


def _own_shadow(site: dict, frame, c: dict) -> bool:
    """候选是否就是无人机自己的影子：影子位置由太阳方向和飞机高度算出（真实系统按日期时间计算太阳方位）。
    半径取影子本身的大小（机身约 1 米，加定位误差），不用固定 2.5 米，减少误杀站在影子附近的人（独立审查 L2）；
    影子候选的面积也要小于一个人加影子的大小。"""
    sun = site.get("sun")
    if not sun or sun[2] >= -0.05:
        return False
    x, y, z = frame.pose.position
    sx, sy = x - z * sun[0] / sun[2], y - z * sun[1] / sun[2]
    radius = 1.2 + 0.03 * z
    return math.hypot(c["x"] - sx, c["y"] - sy) < radius and c.get("area_m2", 0) < 1.0


def _low_pass(site: dict, here: tuple, item: dict, search_alt: float, battery: float) -> tuple[float, list] | None:
    """低空补拍的完整动作（从当前位置平移到候选上方 → 下降 → 爬回搜索高度 → 返航）先交给安全员；
    选能通过的最低高度，都不通过时返回 None，不执行补拍（审查二 H01）。"""
    for alt in (max(site["min_altitude"], 9.0), 12.0):
        if alt >= search_alt:
            continue
        above = (item["x"], item["y"], here[2])
        low = (item["x"], item["y"], alt)
        up = (item["x"], item["y"], search_alt)
        legs = [(here, above), (above, low), (low, up), (up, (0.0, 0.0, search_alt))]
        bad = [v for a, b in legs for v in safety.check_segment(site, a, b, 0)]
        bad += [v for pt in (above, low, up) for v in safety.check_point(site, pt)]
        steps = [survey.Step("transit", "", above, 0, 0, ""), survey.Step("confirm", item["id"], low, 0, -90, "wide"),
                 survey.Step("transit", "", up, 0, 0, "")]
        if not bad and battery - safety.battery_needed(steps, start=here) >= RESERVE:
            return alt, [above, low, up]
    return None


def _budget_ok(f, extra_seconds: float = 25.0) -> bool:
    """再做一次确认动作（约 extra_seconds 秒）之后，是否仍够返航并保留余量（审查二 H05）。"""
    back = safety.battery_needed([survey.Step("transit", "", (0, 0, f.state.z), 0, 0, "")], start=(f.state.x, f.state.y, f.state.z))
    return f.state.battery - back - extra_seconds / safety.DRAIN_SECONDS * 100 * safety.SAFETY_FACTOR >= RESERVE


MAX_PEOPLE = 8                     # 特征不明时最多逐个拍近照的人数（电量与时间）


def _classify(item: dict, search_all: bool = False) -> str:
    """初筛结果的分类。看得出是人、但是否相符不确定的，也要去确认（不能当成路人，审查二 H04）。
    特征不明时，每个人都要拍清楚交给人工辨认。"""
    if search_all and item["person"] == "yes":
        return "candidate"
    if item["person"] == "no":
        return "object"
    if item["person"] == "unsure":
        return "unsure"
    if item.get("in_water") == "yes":
        return "candidate"          # 水里有人本身就是紧急情况，衣服在水下看不清颜色，不能按衣着排除（工程日志 43）
    return "bystander" if item["match"] == "no" else "candidate"


def _hit(verdict: dict, decision: str, x: float, y: float, pid: int) -> dict:
    return {"x": round(x, 2), "y": round(y, 2), "in_water": decision == "water", "photo": pid, "clothing_unverified": decision == "water",
            "evidence": verdict["evidence"], "confidence": verdict["confidence"], "found_at": time.time()}


async def _confirm(state: RescueState, item: dict, index: int) -> tuple[dict | None, dict, str]:
    """确认一个候选。返回（找到时的目标信息, 最后一次观察, 裁决）。裁决见 roles.identity。
    先在原地用变焦对准；变焦里没看到人（多半已经走开）时，经安全员校验后飞到上方降低高度用广角重找。"""
    f = await runtime.flight()
    site = _site(state)
    brief = state["brief"]
    desc = brief["description"]
    here = (f.state.x, f.state.y, f.state.z)
    yaw, pitch = look_angles(here, (item["x"], item["y"], 0.8))
    frame, pid = await _shoot(state, survey.Step("confirm", item["id"], here, yaw, pitch, "zoom", "确认"), index)
    verdict, usage = await asyncio.to_thread(detect.verify, frame, desc)
    _usage(state, "searcher", usage)
    verdict["photo"] = pid
    decision = roles.identity(brief, verdict)
    verdict["decision"] = decision
    if decision in ("confirmed", "water"):
        pos = await asyncio.to_thread(detect.locate_in_zoom, frame)
        x, y = pos if pos else (item["x"], item["y"])
        return _hit(verdict, decision, x, y, pid), verdict, decision
    # 初筛是人、变焦里没有人：多半走开了（从发现到对准十来秒，变焦画面只有约 4 米宽）。其他裁决直接返回
    if decision != "no_person" or item.get("person") != "yes":
        return None, verdict, decision
    search_alt = state["plan"]["altitude_m"]
    plan = _low_pass(site, (f.state.x, f.state.y, f.state.z), item, search_alt, f.state.battery)
    if plan is None:
        _emit(state, "safety", "skip_low_pass", f"{item['id']} 可能已经走开，但低空补拍的航段或电量未通过安全员校验，不补拍")
        return None, verdict, "unsure"
    low, (above, lowpt, up) = plan
    _emit(state, "pilot", "retake", f"{item['id']} 的变焦画面里没有看到人，可能已经走开，飞到上方降到 {low:.0f} 米用广角重新寻找（已通过安全员校验）")
    await f.goto(*above, f.state.yaw)                     # 先在当前高度平移到候选上方，再垂直下降
    wide, pid = await _shoot(state, survey.Step("confirm", item["id"] + "L", lowpt, 0.0, -90.0, "wide", "低空确认"), index + 1)
    await f.goto(*up, f.state.yaw, tolerance=1.0)          # 垂直爬回搜索高度后才允许水平飞行（树高可达 12 米）
    near = [c for c in await asyncio.to_thread(detect.candidates, wide)
            if math.hypot(c["x"] - item["x"], c["y"] - item["y"]) < 12 and not _own_shadow(site, wide, c)]
    if not near:
        return None, {**verdict, "photo": pid, "evidence": "低空补拍附近没有找到人"}, "unsure"
    best = min(near, key=lambda c: math.hypot(c["x"] - item["x"], c["y"] - item["y"]))     # 离上次位置最近的那个
    crop = detect.crop(wide, best, metres=3.0, size=480)
    verdict, usage = await asyncio.to_thread(detect.verify, crop, desc, None, "无人机低空俯视照片里以可疑位置为中心裁出的局部")
    _usage(state, "searcher", usage)
    verdict["photo"] = pid
    decision = roles.identity(brief, verdict)
    verdict["decision"] = decision
    if decision in ("confirmed", "water"):
        return _hit(verdict, decision, best["x"], best["y"], pid), verdict, decision
    return None, verdict, "unsure" if decision == "no_person" else decision


async def search(state: RescueState) -> dict:
    """按航线逐点搜索；初筛相符或看不出是否相符的就地确认；找到目标、电量不够或取消时结束。"""
    f = await runtime.flight()
    site = _site(state)
    desc = state["brief"]["description"]
    detections: list[dict] = list(state.get("detections") or [])
    points = [survey.Step(**s) for s in state["steps"] if s["kind"] == "search"]
    searched = 0
    for i, step in enumerate(points):
        if _cancelled(state):
            return {"outcome": "cancelled", "detections": detections, "searched": searched}
        if not _budget_ok(f, extra_seconds=10.0):
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
        found, new, stop = None, [], False
        for it in items:
            # 同一个人在相邻照片里会重复出现：3 米内已明确排除的不再确认；看不清的允许再确认（最多 2 次）
            near = [d for d in detections if math.hypot(it["x"] - d["x"], it["y"] - d["y"]) < 3]
            if any(d["kind"] in ("bystander", "rejected") for d in near) or sum(1 for d in near if d.get("confirm")) >= 2:
                continue
            det = {"id": f"P{len(detections) + 1}", "x": it["x"], "y": it["y"], "kind": _classify(it, state["brief"].get("search_all", False)), "photo": pid, "person": it["person"],
                   "top": it.get("top"), "bottom": it.get("bottom"), "child": it.get("child"), "in_water": it.get("in_water") == "yes",
                   "evidence": it["evidence"], "confidence": it["confidence"], "step": step.target, "seen_at": time.time()}
            detections.append(det)
            new.append(det)
            if det["kind"] != "candidate":
                continue
            if _cancelled(state):
                return {"outcome": "cancelled", "detections": detections, "searched": searched}
            if not _budget_ok(f):                         # 每个候选确认前都核算返航电量（审查二 H05）
                det["kind"], det["reason"] = "unsure", "电量不够，未确认"
                _emit(state, "coordinator", "stop_search", f"电量 {f.state.battery:.0f}%，不够再确认 {det['id']}，停止搜索并返航")
                stop = True
                break
            _emit(state, "searcher", "candidate", f"{step.target}：发现疑似目标 {det['id']}（{det['evidence']}），用变焦确认", det)
            try:
                hit, verdict, decision = await _confirm(state, det, len(store.photos(state["mission_id"])))
            except (FlightError, camera.CameraUnavailable) as error:
                return {**(await _flight_failure(state, error)), "detections": detections, "searched": searched}
            except ModelUnavailable as error:
                _emit(state, "searcher", "error", f"确认时视觉模型不可用：{error}")
                det["kind"] = "unsure"
                continue
            det["confirm"] = verdict
            if hit:
                det.update(kind="target", x=hit["x"], y=hit["y"], first_seen=[it["x"], it["y"]])   # 地图用最新定位（审查二 M03）
                found = {**hit, "id": det["id"], "elapsed_s": round(hit["found_at"] - state["started_at"])}
                head = "在水中发现人员（衣着在水下无法确认）" if hit.get("clothing_unverified") else f"确认找到目标 {det['id']}"
                _emit(state, "searcher", "found", f"{head}：{hit['evidence']}" + ("；需立即救援" if hit["in_water"] else ""), found)
                break
            if decision == "person":              # 特征不明：记为待人工辨认的人员，继续搜索
                det.update(kind="person", x=verdict.get("x", det["x"]), y=verdict.get("y", det["y"]))
                _emit(state, "searcher", "person", f"{det['id']}：拍到一个人（{verdict['evidence']}），待人工辨认", det)
                if sum(1 for d in detections if d["kind"] == "person") >= MAX_PEOPLE:
                    _emit(state, "coordinator", "stop_search", f"已拍到 {MAX_PEOPLE} 个人，停止搜索，交给人工辨认")
                    stop = True
                    break
                continue
            if decision == "rejected":
                det["kind"] = "rejected"
                _emit(state, "searcher", "rejected", f"{det['id']} 不是要找的人：{verdict['evidence']}")
            else:
                det["kind"] = "unsure"             # 看不清或补拍失败：保留为待确认，不当成排除（审查二 H04）
                _emit(state, "searcher", "unsure", f"{det['id']} 没能确认（{verdict['evidence']}），记为待确认")
        people = sum(1 for d in new if d["kind"] in ("bystander", "rejected", "candidate", "target", "unsure"))
        _emit(state, "searcher", "screen", f"{step.target}（{i + 1}/{len(points)}）：照片里 {len(cands)} 处可疑，判断为人 {people} 处", {"photo": pid, "detections": new})
        if found:
            return {"detections": detections, "found": found, "searched": searched}
        if stop:
            break
    return {"detections": detections, "searched": searched}


async def report(state: RescueState) -> dict:
    from ..rescue import report as rescue_report
    f = await runtime.flight()
    result = rescue_report.collect(state, _site(state), f.state.battery)
    result["usage"] = store.usage_summary(state["mission_id"])["total"]
    result["report_path"] = await asyncio.to_thread(rescue_report.render, state["mission_id"], state["task"], _site(state), result)
    status = state.get("outcome") or "done"
    if _cancelled(state):                       # 返航或报告期间被取消：保持取消，不能改回完成（审查二 H06）
        status = "cancelled"
    store.update_mission(state["mission_id"], result=result)
    store.update_mission(state["mission_id"], status=status if status in ("failed", "cancelled") else "done", only_from=("running", "planning"))
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
