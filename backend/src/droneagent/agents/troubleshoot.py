"""诊断员的排障：针对用户描述的飞行问题，提出假设并在仿真中做对照实验，用数据确认原因、验证修复。

步骤：
  1. 基线试飞：按固定的测试动作（悬停、横移、转向、爬升）飞一次，记录日志指标；
  2. 读取当前参数，与“已知良好配置”（参考参数快照）对比，列出差异；
  3. 诊断员（模型）根据现象、指标和参数差异提出假设，并选择一个实验：把某些参数恢复为参考值后重飞；
  4. 对比实验前后的指标，更新假设；最多做 MAX_EXPERIMENTS 次实验；
  5. 给出结论：根因、证据（指标前后对比）、建议的参数修改（已在仿真中验证）。

参数差异里会有无害的自定义修改（例如巡航速度），只看差异无法确定原因，必须用实验区分。
所有实验只在仿真中进行；对真机只输出建议。
"""
from __future__ import annotations

import asyncio
import json
import time

from .. import config, health, logs, safety, simctl, store
from ..flight import Flight, FlightError, Preempted
from ..sim.scene import load_site
from ..llm import ModelUnavailable, chat_json
from . import runtime

MAX_EXPERIMENTS = 3
# 关注的参数：控制增益、速度与高度控制、电池与失效保护等
TRACKED = ["MC_ROLLRATE_P", "MC_ROLLRATE_I", "MC_ROLLRATE_D", "MC_PITCHRATE_P", "MC_PITCHRATE_I", "MC_PITCHRATE_D",
           "MC_YAWRATE_P", "MC_YAWRATE_I", "MC_ROLL_P", "MC_PITCH_P", "MC_YAW_P", "MPC_XY_P", "MPC_XY_VEL_P_ACC",
           "MPC_Z_P", "MPC_Z_VEL_P_ACC", "MPC_Z_VEL_I_ACC", "MPC_XY_CRUISE", "MPC_ACC_HOR", "MPC_TILTMAX_AIR",
           "MPC_LAND_SPEED", "COM_DISARM_LAND", "IMU_GYRO_CUTOFF", "IMU_DGYRO_CUTOFF", "MPC_XY_VEL_I_ACC"]
# 不纳入比较：本系统自己会写的参数（起飞高度由 set_takeoff_altitude 写入 MIS_TAKEOFF_ALT、返航高度、仿真电池）
REFERENCE = config.DATA_DIR / "reference_params.json"           # 可选：实际机队的“已知良好配置”
PX4_PARAMS = config.PX4_DIR / "build" / "px4_sitl_default" / "parameters.json"

# 用于评测的故障场景：一个真正的原因 + 若干无害的自定义修改（干扰项）。
# 故障强度经过试飞标定，保证在日志指标上可见。角速度环增益、陀螺滤波这类故障在仿真中要么完全看不出，
# 要么直接失稳坠机，没有稳定可复现的中间状态，因此不放进评测；改为一个“与参数无关”的场景：
# 现象存在但参数差异都是无害修改，正确答案是“不是参数问题”，检验诊断员不会把原因硬安到差异最大的参数上。
# 干扰项的相对改动比真正原因还大，“哪个参数改得最多就是哪个”的简单规则会判错。
FAULTS = {
    "no_param_cause": {"label": "与参数无关（只有无害的自定义修改）", "symptom": "悬停时机身有些抖动，拍出来的照片偶尔糊",
                       "cause": {}, "decoys": {"MPC_XY_CRUISE": 12.0, "COM_DISARM_LAND": 10.0}},
    "attitude_gain_low": {"label": "姿态增益过小", "symptom": "飞起来软绵绵的，横移到拍摄点时会晃好一阵才稳住",
                          "cause": {"MC_ROLL_P": 1.2, "MC_PITCH_P": 1.2}, "decoys": {"COM_DISARM_LAND": 10.0, "MPC_LAND_SPEED": 1.5}},
    "yaw_slow": {"label": "航向控制增益过小", "symptom": "转机头特别慢，近距复核时相机总是对不准",
                 "cause": {"MC_YAW_P": 0.7, "MC_YAWRATE_P": 0.06}, "decoys": {"MPC_XY_CRUISE": 12.0, "COM_DISARM_LAND": 9.0}},
    "altitude_weak": {"label": "高度控制增益过小", "symptom": "高度保持不稳，爬升慢，拍摄高度总有偏差",
                      "cause": {"MPC_Z_P": 0.12, "MPC_Z_VEL_P_ACC": 2.0, "MPC_Z_VEL_I_ACC": 0.2},
                      "decoys": {"MPC_XY_CRUISE": 11.0, "COM_DISARM_LAND": 8.0}},
}

DIAGNOSE = """你是无人机团队的诊断员。用户描述了一个飞行问题，你可以在仿真中做对照实验来确定原因。
你会得到：现象描述、基线试飞的日志指标（含判定阈值）、当前参数与已知良好配置的差异、已经做过的实验及其结果。
参数差异中可能有无害的自定义修改，不要只凭差异下结论，要用实验确认：把怀疑的参数恢复为参考值后重飞，看相关指标是否回到正常。
只输出 JSON，二选一：
{"action": "experiment", "hypothesis": "要验证的假设", "restore": ["要恢复为参考值的参数名"], "expect": "如果假设成立，哪些指标会怎样变化"}
{"action": "conclude", "root_cause": "根因（一句话）", "params": ["导致问题的参数名"], "evidence": "引用实验前后的指标数值说明依据",
 "recommendation": "对真机的处理建议"}
最多做 3 次实验；已有实验确认了原因就直接结论。只能引用给定的数据，不要编造数值。"""


def _emit(mid: str, kind: str, message: str, payload: dict | None = None) -> None:
    store.event(mid, "diagnostician", kind, message, payload)


async def read_params(f: Flight) -> dict:
    out = {}
    for name in TRACKED:
        try:
            out[name] = round(await f.get_param(name), 5)
        except Exception:   # 参数不存在（固件版本差异）时跳过
            continue
    return out


def reference() -> dict:
    """已知良好配置：有机队配置文件就用它，否则用 PX4 固件的默认值（本机型的机架配置没有改动这些参数）。"""
    if REFERENCE.exists():
        return json.loads(REFERENCE.read_text(encoding="utf-8"))
    meta = json.loads(PX4_PARAMS.read_text(encoding="utf-8"))["parameters"]
    return {p["name"]: p["default"] for p in meta if p["name"] in TRACKED and "default" in p}


TEST_ALTITUDE = 8.0
TEST_SIDESTEP = 6.0
TEST_CLIMB = 4.0


def test_route(site: dict | None) -> list[tuple[float, float, float]]:
    """固定测试动作的航点（起飞点上空 → 横移 → 爬升 → 返回）。按东、北、西、南依次尝试横移方向，
    取第一个通过安全员校验的；起飞点上空本身不安全（例如紧挨着高树）时抛出 FlightError（深度审查 H03）。"""
    z, top = TEST_ALTITUDE, TEST_ALTITUDE + TEST_CLIMB
    for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
        side = (dx * TEST_SIDESTEP, dy * TEST_SIDESTEP)
        points = [(0.0, 0.0, 0.0), (0.0, 0.0, z), (*side, z), (*side, top), (0.0, 0.0, top), (0.0, 0.0, 0.0)]
        if site is None:
            return points
        bad = [v for i, (a, b) in enumerate(zip(points, points[1:])) for v in safety.check_segment(site, a, b, i)]
        bad += [v for i, p in enumerate(points[1:-1]) for v in safety.check_point(site, p, i, airborne_ok=True)]
        if not bad:
            return points
    raise FlightError("当前场地的起飞点附近没有满足安全余量的试飞空间（障碍物或禁飞区太近），排障不执行")


def _current_site() -> dict | None:
    scene = simctl.status().get("scene")
    try:
        return load_site(scene) if scene else None
    except FileNotFoundError:
        return None


async def test_flight(f: Flight, route: list[tuple[float, float, float]] | None = None) -> dict:
    """固定的测试动作：起飞 8 米、悬停、向东横移 6 米、转向 90°、爬升 4 米，返回降落。返回日志指标。

    某个动作超时不会中断试飞：超时本身就是现象（例如爬升太慢），记为观察结果后继续，最后一定降落。
    """
    route = route or test_route(None)
    side, top = route[2], route[3]
    started = time.time()
    observations: list[str] = []
    check = await health.preflight(f)
    if not check["ok"]:
        raise FlightError("飞行前检查未通过：" + check["advice"])
    await f.wait_ready()
    f.guard()
    await f.action.set_takeoff_altitude(TEST_ALTITUDE)
    try:
        await f.action.arm()
        await f.action.takeoff()
    except Exception as error:
        raise FlightError(f"起飞被拒绝：{error}") from error
    t0 = time.time()
    try:
        await f._wait(lambda: f.state.z >= 7.2, 45, "")
        climb = time.time() - t0
    except FlightError:
        climb = None
        observations.append(f"起飞 45 秒后高度只有 {f.state.z:.1f} 米（目标 8 米）")
    await asyncio.sleep(5)
    z = max(f.state.z, 2.0)
    for label, target in ((f"横移 {TEST_SIDESTEP:.0f} 米", (side[0], side[1], z, 0)), ("原地转向 90°", (side[0], side[1], z, 90)),
                          (f"爬升 {TEST_CLIMB:.0f} 米", (side[0], side[1], z + TEST_CLIMB, 90)), ("返回起飞点上空", (0, 0, z + TEST_CLIMB, 0))):
        t = time.time()
        try:
            await f.goto(*target, tolerance=1.0, timeout=30)
        except Preempted:
            raise
        except FlightError:
            observations.append(f"{label}：30 秒内没有到位（位置 {f.state.x:.1f}, {f.state.y:.1f}, 高 {f.state.z:.1f}，航向 {f.state.yaw:.0f}°）")
            continue
        if time.time() - t > 15:
            observations.append(f"{label}用时 {time.time() - t:.0f} 秒，偏慢")
        await asyncio.sleep(3)
    try:
        await f.land(timeout=60)
    except FlightError:
        observations.append("降落 60 秒仍未完成（落地时可能在弹跳或振荡）")
        await f.land(timeout=90)    # 再给一次机会；仍失败则由上层中止并返航
    await asyncio.sleep(3)          # 等日志文件关闭
    path = await asyncio.to_thread(logs.latest_log, started - 5)
    if path is None:
        raise FlightError("没有找到本次试飞的日志")
    result = await asyncio.to_thread(logs.analyze, path)
    result["metrics"] = {m["name"]: m for m in result["metrics"]}
    result["metrics"]["climb_time"] = {"name": "climb_time", "label": "起飞爬升到 8 米用时", "value": round(climb, 1) if climb else None,
                                       "unit": "s", "threshold": 15.0,
                                       "status": "bad" if climb is None else "warn" if climb > 15 else "ok",
                                       "detail": "爬升慢常见于高度 / 垂直速度控制增益过小或推力不足"}
    result["observations"] = observations
    return result


def _metric_table(result: dict) -> dict:
    return {k: {"value": m["value"], "unit": m["unit"], "threshold": m["threshold"], "status": m["status"]}
            for k, m in result["metrics"].items() if k != "battery_used"}


async def apply(f: Flight, params: dict) -> None:
    for name, value in params.items():
        await f.set_param(name, value)


async def job(mid: str, symptom: str, fault: str | None) -> None:
    """后台执行排障并把结果写入任务记录；同一时间只允许一个飞行任务。"""
    if not runtime.try_claim(mid):
        _emit(mid, "busy", "另一个任务正在飞行，请稍后再试")
        store.update_mission(mid, status="failed")
        return
    store.update_mission(mid, status="running")
    f = await runtime.flight()
    # 取消与返航接管：试飞中的每条指令都会检查（深度审查 H02）
    f.begin_control(lambda: "排障已取消" if store.mission_status(mid) == "cancelled" else None)
    try:
        result = await run(mid, symptom, fault)
        store.update_mission(mid, status=result["status"], result=result, only_from=("running",))
    except Exception as error:   # 任何错误都要落库并让飞机回到地面
        cancelled = isinstance(error, Preempted)
        _emit(mid, "error", f"排障{'已停止' if cancelled else '中断'}：{error}" if cancelled else f"排障中断：{type(error).__name__}：{error}")
        store.update_mission(mid, status="cancelled" if cancelled else "failed", only_from=("running", "cancelled"))
    finally:
        # 无论成功、失败、取消还是模型不可用的早退，都先让飞机落地，再恢复良好配置（深度审查 H09）
        f.end_control()
        try:
            if f.state.in_air:
                if not f.preempted:
                    await f.failsafe_return()
                await f._wait(lambda: not f.state.in_air, 240, "返航超时")
            await reset_to_reference(f)
            _emit(mid, "reset", "仿真中的参数已恢复为良好配置")
        except Exception as error:
            _emit(mid, "error", f"恢复参数或返航失败：{error}；请重启仿真后再使用")
        runtime.release(mid)


async def run(mid: str, symptom: str, fault: str | None = None) -> dict:
    """执行一次排障。fault 不为空时先注入故障（仅评测与演示）。"""
    f = await runtime.flight()
    ref = reference()
    route = test_route(_current_site())     # 先确认试飞空间安全，再注入故障
    if fault:
        spec = FAULTS[fault]
        await apply(f, {**spec["cause"], **spec["decoys"]})
        _emit(mid, "inject", "（演示）已在仿真中注入一个故障，诊断员不知道是哪一个", {})
    _emit(mid, "baseline", f"收到问题：{symptom}。先按固定测试动作试飞一次，记录基线指标")
    baseline = await test_flight(f, route)
    current = await read_params(f)
    diff = {k: {"current": v, "reference": ref.get(k)} for k, v in current.items() if ref.get(k) is not None and abs(v - ref[k]) > 1e-6}
    bad = [f"{m['label']} {m['value']}{m['unit']}" for m in baseline["metrics"].values() if m["status"] in ("warn", "bad")]
    seen = "；".join(baseline["observations"])
    _emit(mid, "baseline_result", f"基线试飞完成：异常指标 {('、'.join(bad)) or '无'}{('；观察到：' + seen) if seen else ''}；"
          f"与良好配置相比有 {len(diff)} 个参数不同（{'、'.join(diff) or '无'}）",
          {"metrics": _metric_table(baseline), "diff": diff, "observations": baseline["observations"]})

    experiments, flights, decision, usage_total = [], 1, None, []
    for _ in range(MAX_EXPERIMENTS + 1):
        evidence = {"symptom": symptom, "baseline_metrics": _metric_table(baseline), "baseline_observations": baseline["observations"],
                    "param_diff": diff, "experiments": experiments, "messages": baseline["messages"][-8:]}
        try:
            decision, usage = await asyncio.to_thread(chat_json, DIAGNOSE, json.dumps(evidence, ensure_ascii=False))
        except ModelUnavailable as error:
            _emit(mid, "error", f"诊断员不可用：{error}")
            await reset_to_reference(f)       # 飞机已在地面（试飞结束会降落），故障参数不能留到下一个任务（深度审查 H09）
            return {"status": "failed", "error": str(error)}
        store.record_call(mid, "diagnostician", usage.as_dict())
        usage_total.append(usage.as_dict())
        if decision.get("action") != "experiment" or len(experiments) >= MAX_EXPERIMENTS:
            break
        restore = [p for p in decision.get("restore") or [] if p in diff]
        if not restore:
            break
        _emit(mid, "hypothesis", f"假设：{decision.get('hypothesis', '')}。实验：把 {'、'.join(restore)} 恢复为参考值后重飞。预期：{decision.get('expect', '')}",
              {"restore": restore})
        await apply(f, {p: ref[p] for p in restore})
        result = await test_flight(f, route)
        flights += 1
        changes = {k: {"before": baseline["metrics"][k]["value"], "after": m["value"], "status_after": m["status"]}
                   for k, m in result["metrics"].items() if k in baseline["metrics"] and k != "battery_used"}
        experiments.append({"restored": restore, "metrics_after": changes, "observations_after": result["observations"]})
        improved = [f"{result['metrics'][k]['label']} {v['before']}→{v['after']}" for k, v in changes.items()
                    if baseline["metrics"][k]["status"] != "ok" and v["status_after"] == "ok"]
        if baseline["observations"] and not result["observations"]:
            improved.append("基线中的动作超时不再出现")
        _emit(mid, "experiment_result", f"实验完成：{('恢复正常的指标：' + '、'.join(improved)) if improved else '异常指标没有恢复'}",
              {"restored": restore, "changes": changes})
        # 恢复后的参数保留在仿真中；若该实验没有改善，下一次实验前把参数改回故障值，保持变量单一
        if not improved:
            await apply(f, {p: diff[p]["current"] for p in restore})

    decision = decision or {}
    params = [p for p in decision.get("params") or [] if p in diff]
    conclusion = {"root_cause": str(decision.get("root_cause") or "未能确定原因")[:200], "params": params,
                  "evidence": str(decision.get("evidence") or "")[:400], "recommendation": str(decision.get("recommendation") or "")[:300],
                  "flights": flights, "experiments": experiments,
                  "fix": {p: {"from": diff[p]["current"], "to": diff[p]["reference"]} for p in params}}
    _emit(mid, "conclusion", f"结论：{conclusion['root_cause']}。依据：{conclusion['evidence']} 建议：{conclusion['recommendation']}", conclusion)
    await reset_to_reference(f)
    if fault:
        truth = set(FAULTS[fault]["cause"])
        conclusion["correct"] = (not params) if not truth else (bool(params) and set(params) <= truth)
    return {"status": "done", **conclusion}


async def reset_to_reference(f: Flight) -> None:
    ref = reference()
    if ref:
        await apply(f, ref)
