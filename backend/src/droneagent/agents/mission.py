"""巡检任务的多 Agent 协同流程（LangGraph）。

  调度员 理解任务 ─► 规划员 选策略 ─► 安全员 校验 ─┬─ 拒绝 ─► 规划员 修改（最多 3 次）
                                                 └─ 通过 ─► 等待人工确认（可设自动）
  ─► 飞手 起飞 ─► [ 逐列：飞手 整列拍摄 ─► 巡检员 初筛 ─► 调度员 按电量决定复核哪些 ─► 飞手 近距拍摄 + 巡检员 复核 ]
  ─► 飞手 返航 ─► 报告员 汇总

角色之间只通过状态里的结构化记录交流；安全员的结论由确定性代码给出，不能被其他角色推翻。
每一步都写入事件表（先落库再推送），飞行中出错时由诊断员分析原因并返航。
"""
from __future__ import annotations

import asyncio
import math
import time
from pathlib import Path
from typing import TypedDict

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .. import config, health, inspection, safety, store, survey, vision
from ..flight import FlightError, Preempted
from ..llm import ModelUnavailable, Usage
from ..sim import camera
from ..sim.scene import load_site
from . import diagnose, report, roles, runtime

MAX_REVISIONS = 3
EXPECTED_CLOSEUP_SHARE = {"none": 0.0, "flagged": 0.15, "flagged_and_unsure": 0.25, "all": 1.0}
ROLE_LABELS = {"coordinator": "调度员", "planner": "规划员", "safety": "安全员", "pilot": "飞手",
               "inspector": "巡检员", "reporter": "报告员", "diagnostician": "诊断员", "system": "系统"}


class State(TypedDict, total=False):
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
    sorties: list[list[str]]          # 每个架次巡检的列；架次之间降落更换电池
    sortie: int                       # 当前架次（从 0 开始）
    current: str
    overview_photo: int
    candidates: list[dict]
    screen_failed: bool               # 本列初筛因模型不可用没有完成
    unjudged: list[str]               # 只拍了照片、没有完成判断的列
    inspected: list[str]
    skipped: list[str]
    airborne: bool
    started_at: float
    battery_start: float
    sortie_battery: float
    battery_used: float
    error: str
    outcome: str                      # done / rejected / clarify / unsupported / cancelled / failed


# ---------- 工具 ----------

def _emit(state: State, role: str, kind: str, message: str, payload: dict | None = None) -> None:
    store.event(state["mission_id"], role, kind, message, payload)


def _usage(state: State, role: str, usage: Usage) -> None:
    store.record_call(state["mission_id"], role, usage.as_dict())


def _cancelled(state: State) -> bool:
    return store.mission_status(state["mission_id"]) == "cancelled"


def _parallel(fn, items: list) -> list:
    """在线程池中并行调用（初筛的多张拼图同时判断）。"""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max(1, len(items))) as pool:
        return list(pool.map(fn, items))


def _site(state: State) -> dict:
    return load_site(state["scene"])


def _arrays(site: dict) -> dict:
    return {a["id"]: a for a in site["arrays"]}


def _panels(site: dict, array_id: str) -> list[dict]:
    return [p for p in site["panels"] if p["array"] == array_id]


def build_steps(site: dict, plan: dict, with_estimated_closeups: bool = True, targets: list[str] | None = None) -> list[survey.Step]:
    """按规划参数展开拍摄步骤。用于安全校验时，按策略估计近距复核的数量一并计入。"""
    arrays = _arrays(site)
    steps = []
    for a in survey.order_arrays([arrays[t] for t in (targets or plan["targets"])]):
        ov = survey.overview_step(a, plan["standoff_m"])
        steps.append(ov)
        if with_estimated_closeups:
            panels = _panels(site, a["id"])
            n = math.ceil(len(panels) * EXPECTED_CLOSEUP_SHARE[plan["closeup_policy"]])
            steps += [survey.closeup_step(p, ov.position, "预估") for p in panels[:n]]
    return survey.with_detours(steps, site)


# ---------- 节点 ----------

async def coordinator(state: State) -> dict:
    site = _site(state)
    f = await runtime.flight()
    battery = f.state.battery
    try:
        brief, usage = await asyncio.to_thread(roles.coordinate, state["task"], site, battery)
    except ModelUnavailable as error:
        _emit(state, "coordinator", "error", f"模型不可用：{error}")
        return {"outcome": "failed", "error": str(error)}
    _usage(state, "coordinator", usage)
    if brief["type"] != "task":
        outcome = brief["type"]
        text = brief.get("question") or brief.get("reason") or ""
        _emit(state, "coordinator", outcome, text, brief)
        return {"brief": brief, "outcome": outcome}
    _emit(state, "coordinator", "brief",
          f"任务理解：巡检 {'、'.join(brief['targets'])} 列，关注{'、'.join(vision.DEFECT_TYPES[t] for t in brief['focus'])}，"
          f"细致程度 {brief['thoroughness']}，返航后保留电量 {brief['reserve_pct']:.0f}%", brief)
    store.update_mission(state["mission_id"], brief=brief)
    return {"brief": brief, "revisions": 0, "violations": [], "battery_start": battery}


async def planner(state: State) -> dict:
    site = _site(state)
    f = await runtime.flight()
    try:
        params, usage = await asyncio.to_thread(roles.plan, state["brief"], site, f.state.battery,
                                                state.get("violations"), state.get("plan"))
    except ModelUnavailable as error:
        _emit(state, "planner", "error", f"模型不可用：{error}")
        return {"outcome": "failed", "error": str(error)}
    _usage(state, "planner", usage)
    params["sorties"] = split_sorties(site, params, f.state.battery, state["brief"]["reserve_pct"])
    steps = build_steps(site, params)
    params["estimated_minutes"] = round(sum(survey.route_seconds(build_steps(site, params, targets=s)) for s in params["sorties"]) / 60, 1)
    params["estimated_battery"] = [round(safety.battery_needed(build_steps(site, params, targets=s)), 1) for s in params["sorties"]]
    # 每个架次的航线（整列拍摄点与绕行点），供地图显示
    params["routes"] = [[s.as_dict() for s in build_steps(site, params, with_estimated_closeups=False, targets=t)] for t in params["sorties"]]
    revision = state.get("revisions", 0)
    flights = "；".join(f"第 {i + 1} 架次 {'、'.join(s)} 列（耗电约 {b:.0f}%）" for i, (s, b) in enumerate(zip(params["sorties"], params["estimated_battery"], strict=True)))
    _emit(state, "planner", "plan",
          f"{'第 ' + str(revision + 1) + ' 版计划' if revision else '计划'}：{'、'.join(params['targets'])} 列，"
          f"复核策略「{roles.POLICIES[params['closeup_policy']]}」，拍摄距离 {params['standoff_m']:.0f} 米，"
          f"共 {len(params['sorties'])} 个架次（{flights}），预计 {params['estimated_minutes']} 分钟。{params['rationale']}",
          {**params, "route": [s.as_dict() for s in steps if s.kind == "overview"]})
    return {"plan": params, "steps": [s.as_dict() for s in steps]}


def split_sorties(site: dict, plan: dict, battery: float, reserve: float) -> list[list[str]]:
    """按电量把各列依次装进架次：第一个架次按当前电量，之后的架次按换上的满电电池。
    装不下的列放进最后一个架次，由安全员给出拒绝。"""
    order = [a["id"] for a in survey.order_arrays([_arrays(site)[t] for t in plan["targets"]])]
    sorties: list[list[str]] = [[]]
    available = battery
    for t in order:
        trial = sorties[-1] + [t]
        steps = build_steps(site, plan, targets=trial)
        fits = available - safety.battery_needed(steps) >= reserve and len(steps) <= safety.MAX_STEPS
        if fits or not sorties[-1] or len(sorties) >= plan.get("max_sorties", 1):
            sorties[-1] = trial
        else:
            sorties.append([t])
            available = 100.0
    return sorties


async def safety_check(state: State) -> dict:
    site = _site(state)
    f = await runtime.flight()
    violations = []
    for i, targets in enumerate(state["plan"]["sorties"]):
        steps = build_steps(site, state["plan"], targets=targets)
        for v in safety.as_dicts(safety.check_plan(site, steps, battery=f.state.battery if i == 0 else 100.0,
                                                   reserve=state["brief"]["reserve_pct"])):
            if len(state["plan"]["sorties"]) > 1:
                v["message"] = f"第 {i + 1} 架次：{v['message']}"
            violations.append(v)
    plan = {**state["plan"], "violations": violations}
    out = {"violations": violations, "revisions": state.get("revisions", 0) + (1 if violations else 0), "plan": plan}
    if violations:
        _emit(state, "safety", "violation", "拒绝：" + "；".join(v["message"] for v in violations), {"violations": violations})
    else:
        _emit(state, "safety", "approved", "计划通过安全校验：围栏、禁飞区、障碍物净空、高度、电量余量均满足", {})
        # 飞行前检查：飞机和仿真本身的状态。不通过时改计划没有用，直接拒绝并说明怎么处理
        check = await health.preflight(f)
        plan["preflight"] = check
        _emit(state, "safety", "preflight", _preflight_text(check), check)
        if not check["ok"]:
            out["outcome"] = "rejected"
    store.update_mission(state["mission_id"], plan={**plan, "route": [s for s in state["steps"] if s["kind"] == "overview"]})
    return out


def _preflight_text(check: dict) -> str:
    if check["ok"]:
        notes = [f"{i['label']}{i['detail']}" for i in check["items"] if i["ok"] is None]
        return "飞行前检查通过" + (f"（提示：{'；'.join(notes)}）" if notes else "")
    return "飞行前检查" + check["advice"] + " " + "；".join(f"{i['label']}：{i['detail']}" for i in check["items"] if i["ok"] is False)


def after_safety(state: State) -> str:
    if state.get("outcome") == "rejected":
        return "rejected"
    if not state["violations"]:
        return "request_approval"
    return "planner" if state["revisions"] < MAX_REVISIONS else "rejected"


async def rejected(state: State) -> dict:
    if state.get("outcome") == "rejected":
        _emit(state, "coordinator", "rejected", "飞行前检查未通过，任务不执行。处理后重新下达即可。")
    else:
        _emit(state, "coordinator", "rejected", f"规划员修改 {MAX_REVISIONS} 次仍未通过安全校验，任务不执行。")
    return {"outcome": "rejected"}


async def request_approval(state: State) -> dict:
    """进入人工确认前的状态更新放在单独的节点：LangGraph 恢复中断时会重新执行被中断的节点，副作用不能放在那里。"""
    if not state.get("auto_approve"):
        store.update_mission(state["mission_id"], status="awaiting_approval")
        _emit(state, "system", "awaiting_approval", "等待人工确认计划")
    return {}


async def approval(state: State) -> dict:
    if not state.get("auto_approve"):
        decision = interrupt({"plan": state["plan"]})
        if not decision.get("approved"):
            _emit(state, "system", "declined", "计划被拒绝，任务取消")
            return {"outcome": "cancelled"}
    if not runtime.try_claim(state["mission_id"]):
        _emit(state, "system", "busy", "另一个任务正在飞行，请稍后再试")
        return {"outcome": "failed", "error": "飞机忙"}
    store.update_mission(state["mission_id"], status="running", only_from=("awaiting_approval", "planning"))
    _emit(state, "system", "approved", "计划已确认，开始执行")
    return {"queue": list(state["plan"]["sorties"][0]), "sortie": 0, "inspected": [], "skipped": []}


async def takeoff(state: State) -> dict:
    f = await runtime.flight()
    # 本任务取得控制权：之后每条普通指令发送前、等待中都会检查是否已取消（深度审查 H02）
    f.begin_control(lambda: "任务已取消" if _cancelled(state) else None)
    if _cancelled(state):
        return {"outcome": "cancelled", "queue": [], "current": ""}
    first = next(s for s in state["steps"] if s["kind"] == "overview")
    altitude = max(state.get("plan", {}).get("standoff_m", 20) * 0.9, 10.0)
    sortie = state.get("sortie", 0)
    total = len(state["plan"]["sorties"])
    # 人工确认可能隔了一段时间，起飞前再检查一次（每个架次都查）
    check = await health.preflight(f)
    if not check["ok"]:
        _emit(state, "safety", "preflight", _preflight_text(check), check)
        return {"error": check["advice"], "outcome": "failed", "queue": [], "current": ""}
    if _cancelled(state):       # 检查期间被取消
        return {"outcome": "cancelled", "queue": [], "current": ""}
    _emit(state, "pilot", "takeoff", (f"第 {sortie + 1}/{total} 架次，" if total > 1 else "")
          + f"起飞，爬升到 {altitude:.0f} 米（当前电量 {f.state.battery:.0f}%）")
    try:
        await f.takeoff(min(altitude, first["position"][2]))
    except FlightError as error:
        return await _flight_failure(state, error)
    out = {"airborne": True}
    if sortie == 0:
        out.update(started_at=time.time(), battery_start=f.state.battery, battery_used=0.0)
    return out


async def next_array(state: State) -> dict:
    """调度员：决定下一列，或因电量、取消而结束巡检。"""
    queue = list(state["queue"])
    if not queue or _cancelled(state):
        return {"current": ""}
    site = _site(state)
    f = await runtime.flight()
    remaining = [survey.overview_step(_arrays(site)[t], state["plan"]["standoff_m"]) for t in queue]
    need = safety.battery_needed(remaining, start=(f.state.x, f.state.y, f.state.z))
    if f.state.battery - need < state["brief"]["reserve_pct"]:
        sorties = [list(s) for s in state["plan"]["sorties"]]
        sortie = state.get("sortie", 0)
        if sortie + 1 < len(sorties):
            # 还有后续架次：把剩下的列挪到下一个架次，本架次先返航换电池
            sorties[sortie + 1] = queue + [t for t in sorties[sortie + 1] if t not in queue]
            _emit(state, "coordinator", "defer_arrays",
                  f"电量 {f.state.battery:.0f}%，剩余 {'、'.join(queue)} 列需要约 {need:.0f}%，挪到下一架次", {"deferred": queue})
            return {"current": "", "queue": [], "plan": {**state["plan"], "sorties": sorties}}
        _emit(state, "coordinator", "skip_arrays",
              f"电量 {f.state.battery:.0f}%，完成剩余 {'、'.join(queue)} 列需要约 {need:.0f}%，会低于保留电量，放弃这些列", {"skipped": queue})
        return {"current": "", "queue": [], "skipped": state.get("skipped", []) + queue}
    return {"current": queue[0], "queue": queue[1:]}


def after_next(state: State) -> str:
    return "overview" if state.get("current") else "return_home"


async def _shoot(state: State, step: survey.Step, index: int) -> tuple[camera.Frame, int]:
    f = await runtime.flight()
    site = _site(state)
    via = survey.detour((f.state.x, f.state.y, f.state.z), step.position, site)   # 航段穿过禁飞区时先绕行
    if via:
        _emit(state, "pilot", "detour", f"直飞会穿过禁飞区，经 {len(via)} 个绕行点飞往 {step.target}", {"via": [list(w) for w in via]})
    for w in via:
        await f.goto(*w, step.yaw)      # goto 内部在发送前和等待中检查取消 / 返航接管
    await f.goto(*step.position, step.yaw)
    await f.point_gimbal(step.pitch, settle=2.5 if step.kind == "overview" else 1.5)
    frame = await asyncio.to_thread(camera.capture, step.camera)
    drift = health.frame_drift((f.state.x, f.state.y, f.state.z), frame.drone)
    if drift["ok"] is False:
        # 飞控估计的位置已经偏离实际，继续拍只会得到对不准的照片：中止并返航
        raise FlightError(f"位置估计漂移：{drift['detail']}")
    path = config.PHOTOS_DIR / state["mission_id"] / f"{index:03d}_{step.kind}_{step.target}"
    await asyncio.to_thread(frame.save, path)
    meta = {**frame.meta(), "battery": round(f.state.battery, 1), "drone": [round(f.state.x, 2), round(f.state.y, 2), round(f.state.z, 2)]}
    pid = store.add_photo(state["mission_id"], index, step.kind, step.target, step.camera, str(path.with_suffix(".png")), meta)
    return frame, pid


async def overview(state: State) -> dict:
    site = _site(state)
    array = _arrays(site)[state["current"]]
    step = survey.overview_step(array, state["plan"]["standoff_m"])
    _emit(state, "pilot", "goto", f"飞往 {array['id']} 列整列拍摄点（{step.position[0]:.0f}, {step.position[1]:.0f}, 高 {step.position[2]:.0f} 米）")
    try:
        frame, pid = await _shoot(state, step, len(store.photos(state["mission_id"])))
    except (FlightError, camera.CameraUnavailable) as error:
        return await _flight_failure(state, error)
    _emit(state, "pilot", "photo", f"{array['id']} 列整列照片已拍摄（广角）", {"photo": pid, "kind": "overview", "target": array["id"]})

    # 巡检员初筛：裁切、校正、拼图，多张拼图并行判断
    try:
        result = await asyncio.to_thread(inspection.screen_frame, frame, _panels(site, array["id"]), None, _parallel)
    except ModelUnavailable as error:
        _emit(state, "inspector", "error", f"视觉模型不可用：{error}，本列只保留照片，未做判断")
        return {"overview_photo": pid, "candidates": [], "screen_failed": True}
    for usage in result.usages:
        _usage(state, "inspector", usage)
    candidates = []
    for c in result.candidates:
        c["screen_photo"] = pid
        candidates.append(c)
        if not c.get("unseen"):
            store.upsert_finding(state["mission_id"], c["id"], status="suspect", type=c["type"], confidence=c["confidence"],
                                 evidence=c["evidence"], screen_photo=pid)
    flagged = [c for c in candidates if c["status"] == "defect"]
    _emit(state, "inspector", "screen",
          f"{array['id']} 列初筛 {result.tiles} 块板：疑似缺陷 {len(flagged)} 块"
          + (f"（{'、'.join(c['id'] for c in flagged)}）" if flagged else "")
          + f"，看不清 {len(candidates) - len(flagged)} 块", {"candidates": candidates, "photo": pid})
    return {"overview_photo": pid, "candidates": candidates, "screen_failed": False}


async def closeups(state: State) -> dict:
    """调度员按策略和电量决定复核哪些板；飞手在整列拍摄点转向、用变焦相机拍摄；巡检员并行复核。"""
    site = _site(state)
    panels = {p["id"]: p for p in site["panels"]}
    array = _arrays(site)[state["current"]]
    if state.get("screen_failed") and state["plan"]["closeup_policy"] != "all":
        # 初筛没做成：不能从“没有候选”推断整列正常，记为未完成判断（深度审查 H07）
        _emit(state, "inspector", "unjudged", f"{array['id']} 列初筛未完成，只保留照片，报告中标为未完成判断")
        return {"unjudged": state.get("unjudged", []) + [array["id"]]}
    cands = {c["id"]: c for c in state.get("candidates", [])}
    targets, confirmed_now = inspection.select_targets(state["plan"]["closeup_policy"], state.get("candidates", []),
                                                       _panels(site, array["id"]))
    for k in confirmed_now:
        store.upsert_finding(state["mission_id"], k, status="confirmed", verify_photo=None)

    f = await runtime.flight()
    viewpoint = survey.overview_step(array, state["plan"]["standoff_m"]).position
    rest = [survey.overview_step(_arrays(site)[t], state["plan"]["standoff_m"]) for t in state["queue"]]
    budget = f.state.battery - state["brief"]["reserve_pct"] - safety.battery_needed(rest, start=viewpoint)
    per_shot = survey.SHOT_SECONDS["closeup"] / safety.DRAIN_SECONDS * 100 * safety.SAFETY_FACTOR
    allowed = max(0, int(budget / per_shot)) if per_shot else len(targets)
    dropped = targets[allowed:]
    targets = targets[:allowed]
    if dropped:
        for k in dropped:
            if k in cands:
                store.upsert_finding(state["mission_id"], k, status="unverified", reason="no_budget")
        _emit(state, "coordinator", "trim_closeups",
              f"电量有限，{array['id']} 列只复核 {len(targets)} 块，另有 {len(dropped)} 块未复核（报告中会标出）", {"dropped": dropped})
    if not targets:
        _emit(state, "inspector", "no_closeup", f"{array['id']} 列无需近距复核" if not dropped else f"{array['id']} 列跳过近距复核")
        return {"inspected": state["inspected"] + [array["id"]]}

    _emit(state, "pilot", "closeups", f"{array['id']} 列开始近距复核 {len(targets)} 块（变焦相机）", {"targets": targets})
    pending = []
    for k in targets:
        if _cancelled(state):
            break
        step = survey.closeup_step(panels[k], viewpoint, "复核")
        try:
            frame, pid = await _shoot(state, step, len(store.photos(state["mission_id"])))
        except (FlightError, camera.CameraUnavailable) as error:
            return await _flight_failure(state, error)
        tile = vision.rectify(frame, panels[k], (vision.CLOSE_W, vision.CLOSE_H))
        if tile is None:
            # 板不在画面里：悬停稳定后重新对准再拍一次
            _emit(state, "pilot", "retake", f"{k} 不在近照画面内，悬停稳定后重新对准")
            await asyncio.sleep(3)
            try:
                frame, pid = await _shoot(state, step, len(store.photos(state["mission_id"])))
            except (FlightError, camera.CameraUnavailable) as error:
                return await _flight_failure(state, error)
            tile = vision.rectify(frame, panels[k], (vision.CLOSE_W, vision.CLOSE_H))
        if tile is None:
            store.upsert_finding(state["mission_id"], k, status="unverified", reason="misaimed",
                                 evidence="两次近照都没有拍到这块板（未能对准）", verify_photo=pid)
            continue
        pending.append((k, pid, asyncio.create_task(asyncio.to_thread(inspection.adjudicate, tile, cands.get(k)))))
    confirmed, cleared, rechecked = [], [], []
    for k, pid, task in pending:
        try:
            verdict, usages, again = await task
        except ModelUnavailable:
            store.upsert_finding(state["mission_id"], k, status="unverified", reason="model_down", verify_photo=pid)
            continue
        for usage in usages:
            _usage(state, "inspector", usage)
        if again:
            rechecked.append(k)
        if verdict["status"] == "unsure":
            store.upsert_finding(state["mission_id"], k, status="unverified", reason="unclear", evidence=verdict["evidence"], verify_photo=pid)
        elif verdict["status"] == "defect":
            confirmed.append(k)
            store.upsert_finding(state["mission_id"], k, status="confirmed", type=verdict["type"], confidence=verdict["confidence"],
                                 evidence=verdict["evidence"], verify_photo=pid)
        else:
            cleared.append(k)
            if k in cands:
                store.upsert_finding(state["mission_id"], k, status="cleared", confidence=verdict["confidence"],
                                     evidence=verdict["evidence"], verify_photo=pid)
    _emit(state, "inspector", "verify",
          f"{array['id']} 列复核完成：确认缺陷 {len(confirmed)} 块" + (f"（{'、'.join(confirmed)}）" if confirmed else "")
          + f"，排除 {len(cleared)} 块" + (f"；{'、'.join(rechecked)} 初筛与复核结论不一致，已再看一次" if rechecked else ""),
          {"confirmed": confirmed, "cleared": cleared, "rechecked": rechecked})
    return {"inspected": state["inspected"] + [array["id"]]}


async def return_home(state: State) -> dict:
    f = await runtime.flight()
    # 以飞控遥测为准：起飞只成功一半（已离地但没到高度）时任务标记可能没写上（深度审查 H01）
    if not (state.get("airborne") or f.state.in_air or f.state.armed):
        return {}
    f.end_control()            # 取消后仍允许返航指令
    if f.preempted:
        # 飞控返航已接管（一键返航）：不再发送任何指令，只等落地
        _emit(state, "pilot", "return", f"{f.preempted}，等待降落（电量 {f.state.battery:.0f}%）")
        try:
            await f._wait(lambda: not f.state.in_air or not f.state.armed, 240, "飞控返航超时")
        except FlightError as err:
            _emit(state, "pilot", "error", str(err))
        _emit(state, "pilot", "landed", f"已降落，电量 {f.state.battery:.0f}%")
        used = (state.get("battery_used") or 0.0) + max(0.0, (state.get("sortie_battery") or state.get("battery_start") or 100) - f.state.battery)
        return {"airborne": False, "battery_used": used}
    via = survey.detour((f.state.x, f.state.y, f.state.z), (0.0, 0.0, f.state.z), _site(state))
    _emit(state, "pilot", "return", f"返航降落（电量 {f.state.battery:.0f}%）" + (f"，绕开禁飞区经 {len(via)} 个绕行点" if via else ""))
    try:
        for w in via:
            await f.goto(*w, f.state.yaw)
        await f.return_home()
    except FlightError as error:
        _emit(state, "pilot", "error", f"返航异常：{error}，改用飞控自带返航")
        await f.failsafe_return()
        try:
            await f._wait(lambda: not f.state.in_air, 240, "飞控返航超时")
        except FlightError as err:
            _emit(state, "pilot", "error", str(err))
    _emit(state, "pilot", "landed", f"已降落，电量 {f.state.battery:.0f}%")
    used = (state.get("battery_used") or 0.0) + max(0.0, (state.get("sortie_battery") or state.get("battery_start") or 100) - f.state.battery)
    return {"airborne": False, "battery_used": used}


def after_return(state: State) -> str:
    more = state.get("sortie", 0) + 1 < len(state.get("plan", {}).get("sorties", []))
    if more and not state.get("outcome") and not _cancelled(state):
        return "swap_battery"
    return "report"


async def swap_battery(state: State) -> dict:
    """架次之间：飞机已降落上锁，更换电池（仿真中上锁即恢复满电），进入下一架次。"""
    f = await runtime.flight()
    sortie = state["sortie"] + 1
    _emit(state, "pilot", "swap_battery", f"更换电池，准备第 {sortie + 1} 架次")
    try:
        await f._wait(lambda: f.state.battery > 95 and not f.state.armed, 60, "换电后电量未恢复")
    except FlightError as error:
        return await _flight_failure(state, error)
    return {"sortie": sortie, "queue": list(state["plan"]["sorties"][sortie]), "sortie_battery": f.state.battery}


async def write_report(state: State) -> dict:
    f = await runtime.flight()
    site = _site(state)
    result = report.collect(state, site, f.state.battery)
    # 结论中的事实与数字全部由程序生成；报告员只给处理建议，且建议里不允许出现数字（深度审查 H08）
    facts = report.facts_sentence(result)
    try:
        advice, usage = await asyncio.to_thread(roles.report_summary, report.for_reporter(result))
        _usage(state, "reporter", usage)
    except ModelUnavailable as error:
        advice = {"advice": "", "priorities": [], "advice_rejected": False}
        _emit(state, "reporter", "error", f"报告员不可用：{error}，结论只包含程序统计")
    if advice.get("advice_rejected"):
        _emit(state, "reporter", "advice_rejected", "报告员的建议含有数字或不存在的编号，已丢弃，改用程序生成的建议",
              {"rejected": advice.get("rejected_text", "")})
    text = advice.get("advice") or report.default_advice(result, advice.get("priorities") or [])
    result.update(summary=(facts + " " + text).strip(), priorities=advice.get("priorities") or [], advice_source="reporter" if advice.get("advice") else "program")
    result["usage"] = store.usage_summary(state["mission_id"])["total"]
    path = await asyncio.to_thread(report.render, state["mission_id"], state["task"], site, result)
    result["report_path"] = str(path)
    outcome = state.get("outcome") or ("cancelled" if _cancelled(state) else "done")
    store.update_mission(state["mission_id"], result=result, status=outcome if outcome != "cancelled" else "cancelled")
    _emit(state, "reporter", "report", result["summary"] or "报告已生成", {"report": f"/missions/{state['mission_id']}/report"})
    runtime.release(state["mission_id"])
    return {"outcome": outcome}


async def _flight_failure(state: State, error: Exception) -> dict:
    """飞行出错：诊断员分析、飞手返航，任务标记失败但仍生成报告。"""
    f = await runtime.flight()
    airborne = bool(state.get("airborne") or f.state.in_air or f.state.armed)
    if isinstance(error, Preempted):
        # 取消或一键返航：不是故障，不做诊断，直接进入返航
        _emit(state, "pilot", "preempted", f"停止执行：{error}")
        return {"outcome": "cancelled", "queue": [], "current": "", "airborne": airborne}
    _emit(state, "pilot", "error", f"飞行异常：{error}")
    analysis = await diagnose.explain_failure(state["mission_id"], str(error), f.state.as_dict(), state.get("started_at"))
    _emit(state, "diagnostician", "diagnosis", analysis["text"], analysis)
    return {"error": str(error), "outcome": "failed", "queue": [], "current": "", "airborne": airborne}


def after_step(state: State) -> str:
    if state.get("outcome") == "failed":
        return "return_home"
    if _cancelled(state):
        return "return_home"
    return "continue"


def build() -> StateGraph:
    g = StateGraph(State)
    for name, fn in [("coordinator", coordinator), ("planner", planner), ("safety", safety_check), ("rejected", rejected),
                     ("approval", approval), ("takeoff", takeoff), ("next_array", next_array), ("overview", overview),
                     ("closeups", closeups), ("return_home", return_home), ("report", write_report)]:
        g.add_node(name, fn)
    g.add_edge(START, "coordinator")
    g.add_conditional_edges("coordinator", lambda s: "planner" if s.get("brief", {}).get("type") == "task" and not s.get("outcome") else END,
                            ["planner", END])
    g.add_conditional_edges("planner", lambda s: END if s.get("outcome") else "safety", ["safety", END])
    g.add_conditional_edges("safety", after_safety, ["request_approval", "planner", "rejected"])
    g.add_node("request_approval", request_approval)
    g.add_edge("request_approval", "approval")
    g.add_edge("rejected", END)
    g.add_conditional_edges("approval", lambda s: END if s.get("outcome") else "takeoff", ["takeoff", END])
    g.add_conditional_edges("takeoff", lambda s: "return_home" if s.get("outcome") else "next_array", ["next_array", "return_home"])
    g.add_conditional_edges("next_array", after_next, ["overview", "return_home"])
    g.add_conditional_edges("overview", lambda s: "return_home" if after_step(s) == "return_home" else "closeups",
                            ["closeups", "return_home"])
    g.add_conditional_edges("closeups", lambda s: "return_home" if after_step(s) == "return_home" else "next_array",
                            ["next_array", "return_home"])
    g.add_conditional_edges("return_home", after_return, ["swap_battery", "report"])
    g.add_node("swap_battery", swap_battery)
    g.add_conditional_edges("swap_battery", lambda s: "return_home" if s.get("outcome") else "takeoff", ["takeoff", "return_home"])
    g.add_edge("report", END)
    return g


_graph = None


async def graph():
    global _graph
    if _graph is None:
        config.ensure_dirs()
        conn = await aiosqlite.connect(config.CHECKPOINT_DB)
        _graph = build().compile(checkpointer=AsyncSqliteSaver(conn))
    return _graph


async def _run(mission_id: str, payload) -> None:
    # 同一任务只允许一个执行实例（例如两个窗口同时点确认，深度审查 H05）
    if not runtime.begin_run(mission_id):
        store.event(mission_id, "system", "duplicate", "该任务已有执行实例，忽略重复的启动或确认")
        return
    try:
        await _run_once(mission_id, payload)
    finally:
        runtime.end_run(mission_id)
        if runtime.current_flight() is not None:
            runtime.current_flight().end_control()


async def _run_once(mission_id: str, payload) -> None:
    g = await graph()
    cfg = {"configurable": {"thread_id": mission_id}, "recursion_limit": 200}
    try:
        result = await g.ainvoke(payload, cfg)
    except Exception as error:   # 任何未预期的错误都要落库，前端才能看到；飞机在空中时立即返航
        store.event(mission_id, "system", "error", f"任务异常终止：{type(error).__name__}：{error}")
        store.update_mission(mission_id, status="failed")
        await emergency_return(mission_id)
        runtime.release(mission_id)
        return
    if "__interrupt__" in result:
        return   # 等待人工确认
    outcome = result.get("outcome") or "done"
    status = {"clarify": "clarify", "unsupported": "unsupported", "rejected": "rejected"}.get(outcome, outcome)
    if status in ("clarify", "unsupported", "rejected", "failed", "cancelled") and store.mission_status(mission_id) in ("planning", "awaiting_approval"):
        store.update_mission(mission_id, status=status)
    runtime.release(mission_id)


async def emergency_return(mission_id: str | None = None) -> str:
    """一键返航：飞机在空中时交给飞控自带返航。"""
    f = await runtime.flight()
    if not f.state.in_air:
        return "飞机在地面"
    try:
        await f.failsafe_return()
    except FlightError as error:
        return str(error)
    if mission_id:
        store.event(mission_id, "pilot", "return", "已执行飞控返航")
    return "已执行返航"


def start(mission_id: str, task: str, scene: str, auto_approve: bool = False):
    return runtime.submit(_run(mission_id, {"mission_id": mission_id, "task": task, "scene": scene, "auto_approve": auto_approve}))


def resume(mission_id: str, approved: bool):
    return runtime.submit(_run(mission_id, Command(resume={"approved": approved})))


def photo_dir(mission_id: str) -> Path:
    return config.PHOTOS_DIR / mission_id
