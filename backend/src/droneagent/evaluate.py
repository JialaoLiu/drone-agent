"""评测。三部分：

  record   在仿真中把一个场景完整拍一遍（每列整列照片 + 每块板的近照），存盘供离线评测反复使用
  vision   用录好的照片离线比较复核策略：只初筛 / 复核疑似 / 复核疑似和看不清 / 全部复核，
           与实时任务共用 inspection 模块的判断规则；报告检出率、精确率、类型准确率、轻微缺陷检出率、
           干扰误报、模型费用与估计飞行时间；可多轮运行看稳定性
  planning 任务理解与规划：只经过调度员、规划员、安全员（不起飞），检查可执行、追问、拒绝是否正确，以及计划是否符合要求

用法：
  python -m droneagent.evaluate record --scene solar_2
  python -m droneagent.evaluate vision --scenes solar_1,solar_2,solar_3 --repeat 2
  python -m droneagent.evaluate planning --repeat 2
报告写入 backend/eval/results/。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import yaml

from . import config, inspection, safety, scoring, survey, vision
from .llm import ModelUnavailable
from .sim.camera import Frame
from .sim.scene import load_site

EVAL_DIR = config.BACKEND_DIR / "eval"
CAPTURES = config.DATA_DIR / "captures"
STRATEGIES = ["none", "flagged", "flagged_and_unsure", "all"]
STRATEGY_LABELS = {"none": "只初筛", "flagged": "复核疑似缺陷", "flagged_and_unsure": "复核疑似与看不清", "all": "全部复核"}


# ---------- record ----------

async def _record(scene: str, standoff: float) -> Path:
    from . import simctl
    from .flight import Flight
    from .geo import Origin
    from .sim import camera

    # 总是重启仿真：场景重新生成后，旧进程里加载的还是旧贴图
    print(f"启动仿真场景 {scene}…", flush=True)
    await asyncio.to_thread(simctl.start, scene)
    site = load_site(scene)
    out = CAPTURES / scene
    async with Flight(Origin(**site["origin"])) as f:
        await f.takeoff(10)
        for a in survey.order_arrays(site["arrays"]):
            ov = survey.overview_step(a, standoff)
            await f.goto(*ov.position, ov.yaw)
            await f.point_gimbal(ov.pitch, settle=2.5)
            camera.capture("wide").save(out / f"overview_{a['id']}")
            for p in [p for p in site["panels"] if p["array"] == a["id"]]:
                cu = survey.closeup_step(p, ov.position)
                await f.goto(*cu.position, cu.yaw)
                await f.point_gimbal(cu.pitch, settle=1.5)
                camera.capture("zoom").save(out / f"closeup_{p['id']}")
            print(f"{a['id']} 列已拍摄，电量 {f.state.battery:.0f}%", flush=True)
        await f.return_home()
    (out / "site.json").write_text(json.dumps(site, ensure_ascii=False), encoding="utf-8")
    (out / "meta.json").write_text(json.dumps({"scene": scene, "standoff_m": standoff, "recorded_at": datetime.now().isoformat()}), encoding="utf-8")
    return out


# ---------- vision ----------

def _captured_site(scene: str) -> dict:
    """录制时保存的场地文件（场景生成器更新后，旧录制仍与当时的场地一致）。"""
    path = CAPTURES / scene / "site.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else load_site(scene)


def _vision_once(scene: str, provider: str | None) -> dict:
    site = _captured_site(scene)
    cap = CAPTURES / scene
    pool = ThreadPoolExecutor(8)
    parallel = lambda fn, items: list(pool.map(fn, items))   # noqa: E731
    screenings, cost, calls = {}, 0.0, 0
    for a in site["arrays"]:
        frame = Frame.load(cap / f"overview_{a['id']}")
        s = inspection.screen_frame(frame, [p for p in site["panels"] if p["array"] == a["id"]], provider, parallel)
        screenings[a["id"]] = s
        cost += sum(u.cost_yuan or 0 for u in s.usages)
        calls += len(s.usages)
    screen_cost = cost

    # 每块板的复核结论只算一次，各策略按自己的复核对象取用（相当于同一份近照，结论一致）
    panels = {p["id"]: p for p in site["panels"]}
    cands = {c["id"]: c for s in screenings.values() for c in s.candidates}

    def verify(pid: str):
        tile = vision.rectify(Frame.load(cap / f"closeup_{pid}"), panels[pid], (vision.CLOSE_W, vision.CLOSE_H))
        if tile is None:
            return pid, {"status": "unsure", "type": None}, [], False
        verdict, usages, again = inspection.adjudicate(tile, cands.get(pid), provider)
        return pid, verdict, usages, again

    verdicts = {pid: (v, u, again) for pid, v, u, again in pool.map(verify, list(panels))}
    out = {}
    for strategy in STRATEGIES:
        reported, closeups, extra_cost, rechecks = {}, 0, 0.0, 0
        for a in site["arrays"]:
            s = screenings[a["id"]]
            targets, confirmed = inspection.select_targets(strategy, s.candidates, [p for p in site["panels"] if p["array"] == a["id"]])
            for pid in confirmed:
                reported[pid] = "missing"
            if strategy == "none":
                reported.update({c["id"]: c["type"] for c in s.candidates if c["status"] == "defect"})
                continue
            for pid in targets:
                verdict, usages, again = verdicts[pid]
                closeups += 1
                extra_cost += sum(u.cost_yuan or 0 for u in usages)
                rechecks += int(again)
                if verdict["status"] == "defect":
                    reported[pid] = verdict["type"]
        result = scoring.score(site, reported)
        # 估计飞行时间：每列一个整列拍摄点，近照在拍摄点原地转向完成
        steps = []
        for a in site["arrays"]:
            ov = survey.overview_step(a, 20)
            steps.append(ov)
        steps += [survey.Step("closeup", "", steps[-1].position, 0, 0, "zoom")] * closeups
        result.update(closeups=closeups, rechecks=rechecks, cost_yuan=round(screen_cost + extra_cost, 4),
                      est_minutes=round(survey.route_seconds(steps) / 60, 1), est_battery=round(safety.battery_needed(steps), 1))
        out[strategy] = result
    pool.shutdown()
    return out


def run_vision(scenes: list[str], repeat: int, provider: str | None) -> dict:
    runs = []
    for r in range(repeat):
        for scene in scenes:
            if not (CAPTURES / scene / "meta.json").exists():
                raise SystemExit(f"没有 {scene} 的录制数据，请先运行：python -m droneagent.evaluate record --scene {scene}")
            started = time.time()
            res = _vision_once(scene, provider)
            runs.append({"round": r + 1, "scene": scene, "seconds": round(time.time() - started), "strategies": res})
            print(f"第 {r + 1} 轮 {scene}：" + "；".join(f"{STRATEGY_LABELS[k]} 检出 {v['recall']} 精确 {v['precision']}" for k, v in res.items()), flush=True)
    summary = {}
    for strategy in STRATEGIES:
        rows = [run["strategies"][strategy] for run in runs]
        tp = sum(round((r["recall"] or 0) * r["defects"]) for r in rows)
        defects = sum(r["defects"] for r in rows)
        reported = sum(r["reported"] for r in rows)
        summary[strategy] = {
            "recall": round(tp / defects, 3) if defects else None,
            "precision": round(tp / reported, 3) if reported else None,
            "type_accuracy": round(sum((r["type_accuracy"] or 0) * round((r["recall"] or 0) * r["defects"]) for r in rows) / tp, 3) if tp else None,
            "minor_recall": round(sum(r["minor_recall"] or 0 for r in rows) / len(rows), 3),
            "distractor_false_alarms": sum(r["distractor_false_alarms"] for r in rows),
            "closeups_per_scene": round(sum(r["closeups"] for r in rows) / len(rows), 1),
            "cost_yuan_per_scene": round(sum(r["cost_yuan"] for r in rows) / len(rows), 4),
            "est_minutes": round(sum(r["est_minutes"] for r in rows) / len(rows), 1),
            "rechecks": sum(r["rechecks"] for r in rows),
            "recall_min": min(r["recall"] or 0 for r in rows), "recall_max": max(r["recall"] or 0 for r in rows),
        }
    return {"suite": "vision", "scenes": scenes, "repeat": repeat, "provider": provider or config.VISION_PROVIDER,
            "model": config.OPENAI_VISION_MODEL if (provider or config.VISION_PROVIDER) == "openai" else config.VISION_MODEL,
            "summary": summary, "runs": runs}


# ---------- planning ----------

def _plan_case(case: dict, site: dict, battery: float) -> dict:
    from .agents import roles
    from .agents.mission import MAX_REVISIONS, build_steps, split_sorties

    brief, _ = roles.coordinate(case["task"], site, battery)
    got = brief["type"]
    record = {"task": case["task"], "expect": case["expect"], "got": got}
    if got != "task":
        record["passed"] = got == case["expect"]
        record["reason"] = "" if record["passed"] else f"应为 {case['expect']}，实际 {got}"
        return record
    plan, violations, revisions = None, [], 0
    while True:
        plan, _ = roles.plan(brief, site, battery, violations or None, plan)
        plan["sorties"] = split_sorties(site, plan, battery, brief["reserve_pct"])
        violations = []
        for i, targets in enumerate(plan["sorties"]):
            violations += safety.as_dicts(safety.check_plan(site, build_steps(site, plan, targets=targets),
                                                            battery=battery if i == 0 else 100.0, reserve=brief["reserve_pct"]))
        if not violations or revisions >= MAX_REVISIONS - 1:
            break
        revisions += 1
    record.update(targets=plan["targets"], policy=plan["closeup_policy"], sorties=plan["sorties"], reserve=brief["reserve_pct"],
                  revisions=revisions, violations=[v["code"] for v in violations])
    problems = []
    if case["expect"] != "task":
        problems.append(f"应为 {case['expect']}，实际可执行")
    if violations:
        problems.append("修改后仍未通过安全校验：" + "、".join(record["violations"]))
    if "targets" in case and sorted(plan["targets"] + plan["deferred"]) != sorted(case["targets"]):
        problems.append(f"巡检对象应为 {case['targets']}，实际 {plan['targets']}（放弃 {plan['deferred']}）")
    if "policy" in case and plan["closeup_policy"] not in case["policy"]:
        problems.append(f"复核策略应为 {case['policy']} 之一，实际 {plan['closeup_policy']}")
    if "min_sorties" in case and len(plan["sorties"]) < case["min_sorties"]:
        problems.append(f"架次应至少 {case['min_sorties']}，实际 {len(plan['sorties'])}")
    if "reserve" in case and abs(brief["reserve_pct"] - case["reserve"]) > 0.5:
        problems.append(f"保留电量应为 {case['reserve']}%，实际 {brief['reserve_pct']}%")
    record["passed"] = not problems
    record["reason"] = "；".join(problems)
    return record


def run_planning(repeat: int) -> dict:
    suite = yaml.safe_load((EVAL_DIR / "planning.yaml").read_text(encoding="utf-8"))
    site = load_site(suite["scene"])
    runs = []
    for r in range(repeat):
        with ThreadPoolExecutor(6) as pool:
            def one(case):
                try:
                    return _plan_case(case, site, suite["battery"])
                except ModelUnavailable as error:
                    return {"task": case["task"], "expect": case["expect"], "got": "error", "passed": False, "reason": str(error)}
            results = list(pool.map(one, suite["cases"]))
        runs.append(results)
        passed = sum(x["passed"] for x in results)
        print(f"第 {r + 1} 轮：{passed}/{len(results)}", flush=True)
        for x in results:
            if not x["passed"]:
                print(f"  失败 {x['task']}：{x['reason']}", flush=True)
    by_task: dict[str, int] = {}
    for results in runs:
        for x in results:
            by_task[x["task"]] = by_task.get(x["task"], 0) + int(x["passed"])
    return {"suite": "planning", "repeat": repeat, "cases": len(suite["cases"]),
            "summary": {"pass_rates": [round(sum(x["passed"] for x in rs) / len(rs), 3) for rs in runs],
                        "unstable": [t for t, n in by_task.items() if 0 < n < repeat], "always_failed": [t for t, n in by_task.items() if n == 0]},
            "runs": runs}


# ---------- layouts ----------

def run_layouts(seeds: list[int]) -> dict:
    """随机布局的泛化检查（不调用模型、不起飞）：每个种子生成一个新布局，按三种复核策略展开航线、
    按电量分架次，交给安全员校验。用来回答“换一个完全不同的布局，规划还能不能用”。"""
    import tempfile

    from .agents.mission import build_steps, split_sorties
    from .sim import scene as scene_mod

    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for seed in seeds:
            out = scene_mod.generate(seed, f"layout_{seed}", out_dir=Path(tmp), textures=False)
            site = json.loads((out / "site.json").read_text(encoding="utf-8"))
            row = {"seed": seed, "arrays": len(site["arrays"]), "panels": len(site["panels"]),
                   "no_fly_zones": len(site["no_fly_zones"]), "trees": len(site["obstacles"])}
            for policy in ("none", "flagged_and_unsure", "all"):
                plan = {"targets": [a["id"] for a in site["arrays"]], "closeup_policy": policy, "standoff_m": 20.0, "max_sorties": 3}
                sorties = split_sorties(site, plan, 100.0, 25.0)
                violations = []
                for targets in sorties:
                    violations += [v.code for v in safety.check_plan(site, build_steps(site, plan, targets=targets), battery=100.0, reserve=25.0)]
                row[policy] = {"sorties": len(sorties), "ok": not violations, "violations": sorted(set(violations))}
            rows.append(row)
            print(f"种子 {seed}：{row['arrays']} 列 {row['panels']} 块板；" +
                  "；".join(f"{STRATEGY_LABELS.get(k, k)} {'通过' if row[k]['ok'] else '未通过 ' + str(row[k]['violations'])}（{row[k]['sorties']} 架次）"
                           for k in ("none", "flagged_and_unsure", "all")), flush=True)
    summary = {k: {"pass_rate": round(sum(r[k]["ok"] for r in rows) / len(rows), 3),
                   "sorties": {n: sum(1 for r in rows if r[k]["sorties"] == n) for n in (1, 2, 3)}}
               for k in ("none", "flagged_and_unsure", "all")}
    summary["arrays_range"] = [min(r["arrays"] for r in rows), max(r["arrays"] for r in rows)]
    summary["panels_range"] = [min(r["panels"] for r in rows), max(r["panels"] for r in rows)]
    return {"suite": "layouts", "seeds": seeds, "summary": summary, "rows": rows}


# ---------- troubleshoot ----------

async def _troubleshoot(faults: list[str]) -> dict:
    from . import store
    from .agents import runtime, troubleshoot

    rows = []
    for fault in faults:
        spec = troubleshoot.FAULTS[fault]
        mid = store.create_mission(spec["symptom"], "eval")
        started = time.time()
        try:
            result = await troubleshoot.run(mid, spec["symptom"], fault)
        except Exception as error:   # 一个场景失败不影响其他场景
            result = {"status": "failed", "error": f"{type(error).__name__}：{error}", "params": [], "flights": 0, "correct": False}
            f = await runtime.flight()
            if f.state.in_air:
                await f.failsafe_return()
                await f._wait(lambda flight=f: not flight.state.in_air, 180, "返航超时")
            await troubleshoot.reset_to_reference(f)
        # 基线 1：只看参数差异，改动幅度最大的就是原因
        ref = troubleshoot.reference() or {}
        changed = {**spec["cause"], **spec["decoys"]}      # 规则：改动幅度最大的参数就是原因（无因场景下必然判错）
        largest = max(changed, key=lambda p: abs(changed[p] - ref.get(p, changed[p])) / max(abs(ref.get(p, 1)), 1e-6))
        usage = store.usage_summary(mid)["total"]
        rows.append({"fault": fault, "label": spec["label"], "correct": result.get("correct", False), "params": result.get("params"),
                     "flights": result.get("flights"), "minutes": round((time.time() - started) / 60, 1), "cost_yuan": usage["cost_yuan"],
                     "root_cause": result.get("root_cause") or result.get("error"), "rule_baseline_param": largest,
                     "rule_baseline_correct": largest in spec["cause"]})
        print(f"{fault}：{'正确' if rows[-1]['correct'] else '错误'}，{rows[-1]['flights']} 次试飞；规则基线{'正确' if rows[-1]['rule_baseline_correct'] else '错误'}", flush=True)
    return {"suite": "troubleshoot", "rows": rows,
            "summary": {"accuracy": round(sum(r["correct"] for r in rows) / len(rows), 3),
                        "rule_baseline_accuracy": round(sum(r["rule_baseline_correct"] for r in rows) / len(rows), 3),
                        "avg_flights": round(sum(r["flights"] or 0 for r in rows) / len(rows), 1)}}


# ---------- 报告 ----------

def write_report(report: dict) -> Path:
    out = EVAL_DIR / "results"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + report["suite"]
    (out / f"{stamp}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    lines = [f"# 评测报告 {stamp}", ""]
    if report["suite"] == "vision":
        lines += [f"场景：{'、'.join(report['scenes'])}，每个场景 {report['repeat']} 轮；视觉模型：{report['model']}", "",
                  "| 策略 | 检出率 | 精确率 | 类型正确 | 轻微缺陷检出 | 干扰误报 | 近照数 / 场景 | 费用 / 场景 | 估计飞行 |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for k, v in report["summary"].items():
            lines.append(f"| {STRATEGY_LABELS[k]} | {v['recall']} | {v['precision']} | {v['type_accuracy']} | {v['minor_recall']} | "
                         f"{v['distractor_false_alarms']} | {v['closeups_per_scene']} | ¥{v['cost_yuan_per_scene']} | {v['est_minutes']} 分钟 |")
        lines += ["", "检出率范围（各轮各场景）：" + "；".join(f"{STRATEGY_LABELS[k]} {v['recall_min']}–{v['recall_max']}" for k, v in report["summary"].items()),
                  "", "## 各场景明细", ""]
        for run in report["runs"]:
            for k, v in run["strategies"].items():
                if v["missed"] or v["false_alarms"] or v["wrong_type"]:
                    lines.append(f"- 第 {run['round']} 轮 {run['scene']} {STRATEGY_LABELS[k]}：漏检 {v['missed'] or '无'}，误报 {v['false_alarms'] or '无'}，"
                                 f"类型错 {[w['panel'] for w in v['wrong_type']] or '无'}")
    elif report["suite"] == "layouts":
        s = report["summary"]
        lines += [f"{len(report['seeds'])} 个随机布局：每个 {s['arrays_range'][0]}–{s['arrays_range'][1]} 列、"
                  f"{s['panels_range'][0]}–{s['panels_range'][1]} 块板；围栏形状、禁飞区、树的位置均随机。", "",
                  "| 复核策略 | 通过安全校验 | 1 架次 | 2 架次 | 3 架次 |", "| --- | ---: | ---: | ---: | ---: |"]
        lines += [f"| {STRATEGY_LABELS[k]} | {v['pass_rate']:.0%} | {v['sorties'][1]} | {v['sorties'][2]} | {v['sorties'][3]} |"
                  for k, v in s.items() if k in STRATEGY_LABELS]
        bad = [r for r in report["rows"] if not all(r[k]["ok"] for k in ("none", "flagged_and_unsure", "all"))]
        lines += ["", "## 未通过的布局", ""] + ([f"- 种子 {r['seed']}：" + "；".join(f"{STRATEGY_LABELS[k]} {r[k]['violations']}"
                                                                         for k in ("none", "flagged_and_unsure", "all") if not r[k]["ok"]) for r in bad] or ["无"])
    elif report["suite"] == "troubleshoot":
        s = report["summary"]
        lines += [f"排障准确率 {s['accuracy']}（只看参数差异的规则基线 {s['rule_baseline_accuracy']}），平均试飞 {s['avg_flights']} 次", "",
                  "| 故障 | 结论 | 认定的参数 | 试飞次数 | 用时 | 费用 | 规则基线 |", "| --- | --- | --- | ---: | ---: | ---: | --- |"]
        lines += [f"| {r['label']} | {'正确' if r['correct'] else '错误'}：{r['root_cause']} | {r['params']} | {r['flights']} | {r['minutes']} 分钟 | "
                  f"¥{r['cost_yuan']} | {r['rule_baseline_param']}（{'正确' if r['rule_baseline_correct'] else '错误'}） |" for r in report["rows"]]
    else:
        s = report["summary"]
        lines += [f"共 {report['cases']} 题，{report['repeat']} 轮：通过率 {s['pass_rates']}", "",
                  f"不稳定的题：{s['unstable'] or '无'}；每轮都失败：{s['always_failed'] or '无'}", "", "## 失败明细", ""]
        for i, rs in enumerate(report["runs"]):
            lines += [f"- 第 {i + 1} 轮 {x['task']}：{x['reason']}" for x in rs if not x["passed"]]
    path = out / f"{stamp}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _run_async(coro):
    """运行协程但不等待默认线程池关闭：MAVSDK 的订阅线程不会自行结束，asyncio.run 会在退出时一直等待。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="无人机巡检评测")
    sub = parser.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record", help="在仿真中完整拍摄一个场景")
    rec.add_argument("--scene", required=True)
    rec.add_argument("--standoff", type=float, default=20.0)
    vis = sub.add_parser("vision", help="离线比较复核策略")
    vis.add_argument("--scenes", default="solar_1")
    vis.add_argument("--repeat", type=int, default=1)
    vis.add_argument("--provider", choices=["deepseek", "openai"])
    pl = sub.add_parser("planning", help="任务理解与规划")
    pl.add_argument("--repeat", type=int, default=1)
    ly = sub.add_parser("layouts", help="随机布局的规划与安全校验（不起飞、不调用模型）")
    ly.add_argument("--seeds", default="1-50", help="例如 1-50")
    ts = sub.add_parser("troubleshoot", help="排障：注入已知故障，检验诊断员能否找到原因（需要仿真）")
    ts.add_argument("--faults", default="all")
    args = parser.parse_args()
    config.ensure_dirs()
    if args.cmd == "record":
        print("已录制：", _run_async(_record(args.scene, args.standoff)), flush=True)
        os._exit(0)   # MAVSDK 的后台线程会阻止进程正常退出
    if args.cmd == "troubleshoot":
        from .agents import troubleshoot
        faults = list(troubleshoot.FAULTS) if args.faults == "all" else args.faults.split(",")
        report = _run_async(_troubleshoot(faults))
    elif args.cmd == "layouts":
        lo, _, hi = args.seeds.partition("-")
        report = run_layouts(list(range(int(lo), int(hi or lo) + 1)))
    elif args.cmd == "vision":
        report = run_vision(args.scenes.split(","), args.repeat, args.provider)
    else:
        report = run_planning(args.repeat)
    print("报告：", write_report(report), flush=True)
    if args.cmd == "troubleshoot":
        os._exit(0)


if __name__ == "__main__":
    main()
