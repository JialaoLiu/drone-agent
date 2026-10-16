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
import math
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
    from . import health

    site = load_site(scene)
    out = CAPTURES / scene
    # 录制同样要过安全员：先展开完整航线（含绕行），校验围栏、禁飞区、净空、高度（深度审查 H03）。
    # 电量与步数上限是单次任务的规划约束，录制按一次长飞行处理，不在此检查。
    steps = []
    for a in survey.order_arrays(site["arrays"]):
        ov = survey.overview_step(a, standoff)
        steps += [ov] + [survey.closeup_step(p, ov.position) for p in site["panels"] if p["array"] == a["id"]]
    route = survey.with_detours(steps, site)
    bad = [v for v in safety.check_plan(site, route, battery=1000.0, reserve=0.0) if v.code not in ("battery", "too_many")]
    if bad:
        raise RuntimeError("录制航线未通过安全校验：" + "；".join(v.message for v in bad[:5]))
    async with Flight(Origin(**site["origin"])) as f:
        await f.wait_ready()           # 仿真刚启动，先等飞控定位与自检就绪，再做飞行前检查
        check = await health.preflight(f)
        if not check["ok"]:
            raise RuntimeError("飞行前检查未通过：" + check["advice"])
        await f.takeoff(10)
        try:
            for st in route:
                await f.goto(*st.position, st.yaw)
                if st.kind == "transit":
                    continue
                await f.point_gimbal(st.pitch, settle=2.5 if st.kind == "overview" else 1.5)
                if st.kind == "overview":
                    camera.capture("wide").save(out / f"overview_{st.target}")
                    print(f"{st.target} 列整列照片已拍摄，电量 {f.state.battery:.0f}%", flush=True)
                else:
                    camera.capture("zoom").save(out / f"closeup_{st.target}")
            for w in survey.detour((f.state.x, f.state.y, f.state.z), (0.0, 0.0, f.state.z), site):
                await f.goto(*w, f.state.yaw)
            await f.return_home()
        except Exception:
            if f.state.in_air:
                await f.failsafe_return()
                await f._wait(lambda: not f.state.in_air, 240, "返航超时")
            raise
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


# ---------- routes ----------

ROUTERS = {"grid": "栅格 A*", "visibility": "可见性图"}


def run_routes(seeds: list[int]) -> dict:
    """绕行方法对比（不调用模型、不起飞）：每个种子生成随机布局，按“全部复核”展开整条航线（含返航），
    找出所有直飞会进入禁飞区余量范围的航段，分别用栅格 A* 和可见性图求绕行，比较安全校验、长度、拐点数、耗时。"""
    import tempfile

    from .agents.mission import _arrays, _panels, EXPECTED_CLOSEUP_SHARE
    from .sim import scene as scene_mod

    segments, plans = [], {m: {"ok": 0, "total": 0} for m in ROUTERS}
    with tempfile.TemporaryDirectory() as tmp:
        for seed in seeds:
            out = scene_mod.generate(seed, f"route_{seed}", out_dir=Path(tmp), textures=False)
            site = json.loads((out / "site.json").read_text(encoding="utf-8"))
            arrays = _arrays(site)
            base = []
            for a in survey.order_arrays(list(arrays.values())):
                ov = survey.overview_step(a, 20.0)
                panels = _panels(site, a["id"])
                base += [ov] + [survey.closeup_step(p, ov.position) for p in panels[:math.ceil(len(panels) * EXPECTED_CLOSEUP_SHARE["all"])]]
            # 整条航线的安全校验（电量不在此比较，按 1000% 电量计）
            for m in ROUTERS:
                steps = survey.with_detours(base, site, method=m)
                codes = {v.code for v in safety.check_plan(site, steps, battery=1000.0, reserve=0.0)} - {"battery", "too_many"}
                plans[m]["total"] += 1
                plans[m]["ok"] += not codes
            # 逐段比较
            points = [(0.0, 0.0, base[0].position[2])] + [st.position for st in base] + [(0.0, 0.0, base[-1].position[2])]
            for a, b in zip(points, points[1:]):
                if survey._clear(a, b, site["no_fly_zones"]):
                    continue
                row = {"seed": seed, "straight": round(math.dist(a[:2], b[:2]), 2)}
                for m in ROUTERS:
                    t0 = time.perf_counter()
                    wps = survey.detour(a, b, site, m) if m == "grid" else survey.detour_visibility(a, b, site)
                    ms = (time.perf_counter() - t0) * 1000
                    found = bool(wps)
                    route = [a] + (wps or []) + [b]
                    bad = [v.code for i, (p, q) in enumerate(zip(route, route[1:])) for v in safety.check_segment(site, p, q, i)
                           if v.code in ("no_fly_zone", "geofence")]
                    row[m] = {"found": found, "ok": found and not bad, "length": round(sum(math.dist(p[:2], q[:2]) for p, q in zip(route, route[1:])), 2),
                              "waypoints": len(wps or []), "ms": round(ms, 2)}
                segments.append(row)
            print(f"种子 {seed}：需要绕行的航段 {sum(1 for r in segments if r['seed'] == seed)}", flush=True)
    summary = {"seeds": len(seeds), "segments": len(segments), "plans": plans}
    for m in ROUTERS:
        ok = [r for r in segments if r[m]["ok"]]
        summary[m] = {"segment_pass_rate": round(len(ok) / max(1, len(segments)), 3),
                      "plan_pass_rate": round(plans[m]["ok"] / max(1, plans[m]["total"]), 3),
                      "mean_length": round(sum(r[m]["length"] for r in ok) / max(1, len(ok)), 2),
                      "mean_extra_pct": round(100 * sum(r[m]["length"] / r["straight"] - 1 for r in ok) / max(1, len(ok)), 1),
                      "mean_waypoints": round(sum(r[m]["waypoints"] for r in ok) / max(1, len(ok)), 2),
                      "mean_ms": round(sum(r[m]["ms"] for r in segments) / max(1, len(segments)), 2),
                      "max_ms": round(max((r[m]["ms"] for r in segments), default=0), 2)}
    both = [r for r in segments if r["grid"]["ok"] and r["visibility"]["ok"]]
    summary["both_ok"] = len(both)
    summary["visibility_shorter"] = sum(1 for r in both if r["visibility"]["length"] < r["grid"]["length"] - 0.01)
    summary["visibility_longer"] = sum(1 for r in both if r["visibility"]["length"] > r["grid"]["length"] + 0.01)
    # 没有可比较的航段时不给百分比（否则会算出“缩短 100%”）
    summary["length_saving_pct"] = (round(100 * (1 - sum(r["visibility"]["length"] for r in both) / sum(r["grid"]["length"] for r in both)), 1)
                                    if both else None)
    return {"suite": "routes", "seeds": seeds, "summary": summary, "rows": segments}


# ---------- triage ----------

TRIAGE_TRAIN = ["solar_1", "solar_2", "solar_3", "solar_101", "solar_102", "solar_103", "solar_104"]
TRIAGE_TEST = ["solar_46", "solar_63", "solar_71", "solar_105", "solar_106"]
TRIAGE_TARGETS = [0.98, 0.95, 0.90]


def run_triage(train_scenes: list[str], test_scenes: list[str], replay: bool = False) -> dict:
    """本地初筛分类器：按场景划分训练 / 测试（测试场景完全没见过）。阈值只用训练场景的留一场景交叉验证结果选取，
    再在测试场景上报告实际保留的缺陷比例与少调用的初筛比例。replay 为真时，对测试场景真的调用视觉模型，
    比较“全部初筛”与“分类器过滤后初筛”的结果和费用。"""
    import numpy as np
    from sklearn.metrics import average_precision_score, roc_auc_score

    from . import triage

    xtr, ytr, mtr = triage.dataset(train_scenes)
    xte, yte, mte = triage.dataset(test_scenes)
    # 训练集上的留一场景交叉验证：每个场景的概率都来自没见过它的模型，用来选阈值
    oof = np.zeros(len(ytr))
    for sc in train_scenes:
        idx = np.array([m.scene == sc for m in mtr])
        if idx.any() and (~idx).any():
            oof[idx] = triage.train(xtr[~idx], ytr[~idx]).predict_proba(xtr[idx])[:, 1]
    model = triage.train(xtr, ytr)
    p = model.predict_proba(xte)[:, 1]
    rows = []
    for target in TRIAGE_TARGETS:
        th = triage.threshold_for_recall(ytr, oof, target)
        keep = p >= th
        lost = [m for m, k, y in zip(mte, keep, yte) if y and not k]
        rows.append({"target_recall": target, "threshold": round(th, 4),
                     "defects_kept": round(float(keep[yte == 1].mean()), 3),
                     "tiles_sent": round(float(keep.mean()), 3),
                     "distractors_sent": round(float(np.mean([k for k, m in zip(keep, mte) if m.distractor])) if any(m.distractor for m in mte) else 0, 3),
                     "lost": [{"scene": m.scene, "panel": m.panel_id, "type": m.defect, "severity": m.severity} for m in lost]})
    summary = {"train_tiles": int(len(ytr)), "train_defects": int(ytr.sum()), "test_tiles": int(len(yte)), "test_defects": int(yte.sum()),
               "auc": round(float(roc_auc_score(yte, p)), 3), "average_precision": round(float(average_precision_score(yte, p)), 3),
               "cv_auc": round(float(roc_auc_score(ytr, oof)), 3)}
    report = {"suite": "triage", "train_scenes": train_scenes, "test_scenes": test_scenes, "summary": summary, "rows": rows}
    triage.save(model, rows[0]["threshold"], {"train_scenes": train_scenes, "target_recall": TRIAGE_TARGETS[0], **summary})
    if replay:
        report["replay"] = _triage_replay(test_scenes, model, rows[0]["threshold"])
    return report


def _triage_replay(scenes: list[str], model, threshold: float) -> dict:
    """真的调用视觉模型：同一批整列照片，全部初筛 vs 过滤后初筛。"""
    from . import triage

    out = {"threshold": threshold, "scenes": []}
    for scene in scenes:
        site = _captured_site(scene)
        truth = {p["id"]: p["defect"]["type"] for p in site["panels"] if p.get("defect")}
        row = {"scene": scene}
        for mode in ("all", "filtered"):
            found, cost, sent, total = set(), 0.0, 0, 0
            for a in site["arrays"]:
                panels = [p for p in site["panels"] if p["array"] == a["id"]]
                frame = Frame.load(CAPTURES / scene / f"overview_{a['id']}")
                if mode == "filtered":
                    ids, x = triage.photo_features(frame, panels)
                    keep = {pid for pid, pr in zip(ids, model.predict_proba(x)[:, 1]) if pr >= threshold} if len(ids) else set()
                    keep |= {p["id"] for p in panels} - set(ids)          # 看不清的板仍交给初筛
                    panels = [p for p in panels if p["id"] in keep]
                total += sum(1 for p in site["panels"] if p["array"] == a["id"])
                sent += len(panels)
                if not panels:
                    continue
                res = inspection.screen_frame(frame, panels)
                cost += sum(u.cost_yuan or 0 for u in res.usages)
                found |= {c["id"] for c in res.candidates if c["status"] == "defect"}
            hits = found & set(truth)
            row[mode] = {"flagged": len(found), "defects_flagged": len(hits), "defects": len(truth),
                         "recall": round(len(hits) / max(1, len(truth)), 3), "precision": round(len(hits) / max(1, len(found)), 3),
                         "tiles_sent": sent, "tiles": total, "cost_yuan": round(cost, 4)}
        out["scenes"].append(row)
        print(f"{scene}：全部初筛 {row['all']}；过滤后 {row['filtered']}", flush=True)
    return out


# ---------- twin ----------

# 评测地点（坐标固定，保证可复现）：国内用百度检索后换算的 WGS-84，境外直接给坐标
TWIN_PLACES = [
    {"name": "文翰湖公园（佛山，湖景）", "lat": 23.0172979, "lon": 113.232189},
    {"name": "千灯湖（佛山，城区湖景）", "lat": 23.0560707, "lon": 113.142338},
    {"name": "三山森林公园（佛山，山林）", "lat": 23.0341089, "lon": 113.2226896},
    {"name": "Nyngan 光伏电站（澳大利亚，平原）", "lat": -31.55648, "lon": 147.08608},
    {"name": "Adelaide Botanic Garden（澳大利亚，城区）", "lat": -34.9177, "lon": 138.6100},
]


def run_twin(seeds: list[int]) -> dict:
    """真实场地数字孪生：同一地点、同一种子，比较“完全随机布置”与“孪生约束布置”。
    指标：阵列压在水面或建筑上的比例、安全校验（含离地高度）通过率。不调用模型、不起飞。"""
    import random

    from . import twin
    from .agents.mission import build_steps, split_sorties
    from .sim import scene as scene_mod

    rows = []
    for place in TWIN_PLACES:
        loc = {**place, "address": "", "city": "", "source": "评测固定坐标"}
        try:
            info = scene_mod._twin(loc)
        except Exception as error:   # 数据源不可用时记录原因，不中断其他地点
            rows.append({"place": place["name"], "error": f"{type(error).__name__}：{error}"})
            print(f"{place['name']}：{error}", flush=True)
            continue
        blocked = info["blocked"]
        naive_bad = naive_total = twin_bad = twin_total = 0
        passes = {"none": 0, "flagged_and_unsure": 0, "all": 0}
        terrain_violations = generated = 0
        for seed in seeds:
            naive = scene_mod._layout(random.Random(seed))
            naive_total += len(naive["arrays"])
            naive_bad += sum(1 for a in naive["arrays"] if blocked(*a["center"], a["radius"]))
            try:
                layout = scene_mod._layout(random.Random(seed), blocked=blocked, preferred=info["preferred"], real_obstacles=info["obstacles"])
            except RuntimeError:
                continue
            generated += 1
            twin_total += len(layout["arrays"])
            twin_bad += sum(1 for a in layout["arrays"] if blocked(*a["center"], a["radius"]))
            site = {**layout, "name": "twin", "origin": info["origin"], "terrain": info["terrain"], "panels": []}
            site["panels"] = [asdict_panel for asdict_panel in scene_mod._panels_dicts(layout["arrays"], seed)]
            for policy in passes:
                plan = {"targets": [a["id"] for a in layout["arrays"]], "closeup_policy": policy, "standoff_m": 20.0, "max_sorties": 3}
                codes = []
                for targets in split_sorties(site, plan, 100.0, 25.0):
                    codes += [v.code for v in safety.check_plan(site, build_steps(site, plan, targets=targets), battery=100.0, reserve=25.0)]
                passes[policy] += not codes
                terrain_violations += codes.count("terrain")
        rows.append({"place": place["name"], "feature_counts": info["summary"]["feature_counts"], "osm_source": info["summary"]["osm_source"],
                     "relief_m": info["terrain"]["relief"], "origin_shift_m": info["summary"]["origin_shift_m"], "obstacles": len(info["obstacles"]),
                     "naive_arrays_blocked": round(naive_bad / max(1, naive_total), 3), "twin_arrays_blocked": round(twin_bad / max(1, twin_total), 3),
                     "generated": generated, "seeds": len(seeds), "pass_rate": {k: round(v / max(1, generated), 3) for k, v in passes.items()},
                     "terrain_violations": terrain_violations})
        print(f"{place['name']}：随机布置压在水面 / 建筑上 {naive_bad}/{naive_total}，孪生布置 {twin_bad}/{twin_total}；"
              f"生成 {generated}/{len(seeds)}；高差 {info['terrain']['relief']} 米", flush=True)
    return {"suite": "twin", "seeds": seeds, "rows": rows}


# ---------- real ----------

REAL_DIR = config.RELATED_DIR / "datasets" / "pv-panel-defect"
# 公开数据集的类别 → 本系统的判断：期望结论，以及算作“类型正确”的缺陷类型
REAL_CLASSES = {
    "Clean": ("ok", set()),
    "Dusty": ("defect", {"soiling"}),
    "Bird-drop": ("defect", {"soiling"}),
    "Physical-Damage": ("defect", {"crack", "broken"}),
    "Electrical-damage": ("defect", set()),      # 本系统没有对应类型（烧痕、热斑），只看是否判为缺陷
    "Snow-Covered": ("defect", set()),           # 超出本系统范围，单独统计
}


def run_real(splits: list[str], limit: int = 0) -> dict:
    """真实图像评测：用公开数据集的现场照片检验仿真中调出来的复核判断能迁移多少。
    与实时任务相同的系统说明与裁决（无法识别判为不确定；不确定时再看一次）。"""
    from PIL import Image

    items = []
    for split in splits:
        for cls in REAL_CLASSES:
            files = sorted(p for p in (REAL_DIR / split / cls).glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
            items += [(split, cls, f) for f in (files[:limit] if limit else files)]

    def judge(item):
        split, cls, path = item
        img = Image.open(path)
        verdict, usage = vision.verify_photo(img)
        usages = [usage]
        if verdict["status"] == "unsure":
            verdict, usage = vision.verify_photo(img)
            usages.append(usage)
        return {"split": split, "class": cls, "file": path.name, **verdict, "cost": sum(u.cost_yuan or 0 for u in usages)}

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = []
        for i, r in enumerate(pool.map(judge, items), 1):
            rows.append(r)
            if i % 50 == 0:
                print(f"已判断 {i}/{len(items)}", flush=True)
    per_class = {}
    for cls, (expect, types) in REAL_CLASSES.items():
        rs = [r for r in rows if r["class"] == cls]
        if not rs:
            continue
        ok = [r for r in rs if r["status"] == expect]
        per_class[cls] = {"n": len(rs), "expected": expect, "correct_status": round(len(ok) / len(rs), 3),
                          "unsure": round(sum(r["status"] == "unsure" for r in rs) / len(rs), 3),
                          "type_correct": round(sum(r["type"] in types for r in ok) / len(ok), 3) if types and ok else None,
                          "types": {t: sum(r["type"] == t for r in rs) for t in vision.DEFECT_TYPES}}
    core = [r for r in rows if r["class"] in ("Clean", "Dusty", "Bird-drop", "Physical-Damage")]
    tp = sum(r["status"] == "defect" for r in core if r["class"] != "Clean")
    fp = sum(r["status"] == "defect" for r in core if r["class"] == "Clean")
    pos = sum(r["class"] != "Clean" for r in core)
    summary = {"images": len(rows), "splits": splits, "per_class": per_class,
               "core_recall": round(tp / max(1, pos), 3), "core_precision": round(tp / max(1, tp + fp), 3),
               "clean_false_alarm": round(fp / max(1, sum(r["class"] == "Clean" for r in core)), 3),
               "cost_yuan": round(sum(r["cost"] for r in rows), 3)}
    return {"suite": "real", "summary": summary, "rows": rows}


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
    elif report["suite"] == "routes":
        s = report["summary"]
        lines += [f"{s['seeds']} 个随机布局，按“全部复核”展开整条航线（含返航）；需要绕行的航段共 {s['segments']} 段。", "",
                  "| 方法 | 整条航线通过安全校验 | 航段通过 | 平均绕行长度 | 比直线多 | 平均拐点 | 平均耗时 | 最长耗时 |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        lines += [f"| {ROUTERS[m]} | {s[m]['plan_pass_rate']:.0%} | {s[m]['segment_pass_rate']:.0%} | {s[m]['mean_length']} 米 | {s[m]['mean_extra_pct']}% | "
                  f"{s[m]['mean_waypoints']} | {s[m]['mean_ms']} 毫秒 | {s[m]['max_ms']} 毫秒 |" for m in ROUTERS]
        lines += ["", f"两种方法都通过的 {s['both_ok']} 段中：可见性图更短 {s['visibility_shorter']} 段、更长 {s['visibility_longer']} 段，"
                  + (f"总长度少 {s['length_saving_pct']}%。" if s["length_saving_pct"] is not None else "总长度对比：不适用（没有需要绕行的航段）。"), "",
                  "说明：两种方法的约束相同：栅格点 / 多边形顶点离禁飞区边界至少 5 米，航段直飞判定离边界至少 3.5 米（安全员下限 3 米）。可见性图把膨胀后的圆近似为外切正十二边形，找不到路径时退回栅格 A*（本表单独统计，未计入退回）。"]
        bad = [r for r in report["rows"] if not (r["grid"]["ok"] and r["visibility"]["ok"])]
        lines += ["", "## 未通过的航段", ""] + ([f"- 种子 {r['seed']}：" + "；".join(f"{ROUTERS[m]} {'找到但不安全' if r[m]['found'] else '未找到'}"
                                                                         for m in ROUTERS if not r[m]["ok"]) for r in bad] or ["无"])
    elif report["suite"] == "triage":
        s = report["summary"]
        lines += [f"训练场景 {'、'.join(report['train_scenes'])}（{s['train_tiles']} 块板、{s['train_defects']} 个缺陷）；"
                  f"测试场景 {'、'.join(report['test_scenes'])}（{s['test_tiles']} 块板、{s['test_defects']} 个缺陷，训练时完全没见过）。", "",
                  f"测试场景 AUC {s['auc']}，平均精确率 {s['average_precision']}；训练集留一场景交叉验证 AUC {s['cv_auc']}。", "",
                  "| 阈值按训练集保留缺陷 | 阈值 | 测试集实际保留缺陷 | 交给视觉模型的板 | 干扰板被送去复核 | 漏掉的缺陷 |",
                  "| ---: | ---: | ---: | ---: | ---: | --- |"]
        lines += [f"| {r['target_recall']:.0%} | {r['threshold']} | {r['defects_kept']:.1%} | {r['tiles_sent']:.1%} | {r['distractors_sent']:.1%} | "
                  + ("、".join(f"{x['panel']}（{x['type']}，{x['severity']}）" for x in r["lost"]) or "无") + " |" for r in report["rows"]]
        if report.get("replay"):
            lines += ["", f"## 实际调用视觉模型对比（阈值 {report['replay']['threshold']}）", "",
                      "| 场景 | 全部初筛：标出缺陷 / 实际缺陷 | 精确率 | 费用 | 过滤后：标出缺陷 / 实际缺陷 | 精确率 | 送检板数 | 费用 |",
                      "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
            for r in report["replay"]["scenes"]:
                a, f = r["all"], r["filtered"]
                lines.append(f"| {r['scene']} | {a['defects_flagged']} / {a['defects']} | {a['precision']} | ¥{a['cost_yuan']} | "
                             f"{f['defects_flagged']} / {f['defects']} | {f['precision']} | {f['tiles_sent']} / {f['tiles']} | ¥{f['cost_yuan']} |")
    elif report["suite"] == "twin":
        lines += [f"每个地点 {len(report['seeds'])} 个种子；同一地点、同一种子比较“完全随机布置”与“孪生约束布置”（不调用模型、不起飞）。", "",
                  "| 地点 | 地物（水面 / 建筑 / 树林 / 光伏轮廓） | 高差 | 随机布置：阵列压在水面或建筑上 | 孪生布置 | 生成成功 | 安全校验通过（只初筛 / 复核可疑 / 全部复核） | 离地高度违规 |",
                  "| --- | --- | ---: | ---: | ---: | ---: | --- | ---: |"]
        for r in report["rows"]:
            if "error" in r:
                lines.append(f"| {r['place']} | 数据不可用：{r['error']} | | | | | | |")
                continue
            c, p = r["feature_counts"], r["pass_rate"]
            lines.append(f"| {r['place']} | {c['water']} / {c['building']} / {c['wood']} / {c['solar']} | {r['relief_m']} 米 | {r['naive_arrays_blocked']:.0%} | "
                         f"{r['twin_arrays_blocked']:.0%} | {r['generated']}/{r['seeds']} | {p['none']:.0%} / {p['flagged_and_unsure']:.0%} / {p['all']:.0%} | {r['terrain_violations']} |")
        lines += ["", "说明：高程为 Copernicus DEM GLO-30（地表模型，含树冠与建筑高度）；地物来自 OpenStreetMap，国内覆盖较弱；禁飞区仍为模拟。"]
    elif report["suite"] == "real":
        s = report["summary"]
        labels = {"Clean": "干净", "Dusty": "灰尘", "Bird-drop": "鸟粪", "Physical-Damage": "物理损坏", "Electrical-damage": "电气损坏", "Snow-Covered": "积雪"}
        lines += [f"公开数据集 alicjalena/pv-panel-defect-dataset（CC BY-NC-SA 4.0），{'、'.join(s['splits'])}，共 {s['images']} 张现场照片；"
                  f"费用 ¥{s['cost_yuan']}。判断说明与实时任务相同（为仿真画面编写，未针对真实照片调整）。", "",
                  f"核心四类（干净、灰尘、鸟粪、物理损坏）：缺陷检出 {s['core_recall']:.1%}，精确率 {s['core_precision']:.1%}，干净板误报 {s['clean_false_alarm']:.1%}。", "",
                  "| 类别 | 张数 | 期望 | 结论正确 | 不确定 | 类型正确（对应类型） | 判出的类型分布 |", "| --- | ---: | --- | ---: | ---: | ---: | --- |"]
        for cls, v in s["per_class"].items():
            dist = "、".join(f"{vision.DEFECT_TYPES[t]} {n}" for t, n in v["types"].items() if n) or "无"
            tc = "—" if v["type_correct"] is None else f"{v['type_correct']:.1%}"
            lines.append(f"| {labels[cls]} | {v['n']} | {'正常' if v['expected'] == 'ok' else '缺陷'} | {v['correct_status']:.1%} | {v['unsure']:.1%} | {tc} | {dist} |")
        lines += ["", "说明：电气损坏（烧痕、热斑）和积雪不在本系统的缺陷类型里，只统计是否判为缺陷；物理损坏判为裂纹或玻璃破碎都算类型正确，灰尘和鸟粪对应污损。"]
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
    tg = sub.add_parser("triage", help="本地初筛分类器：训练、选阈值、在没见过的场景上评估（--replay 真的调用视觉模型对比）")
    tg.add_argument("--train", default=",".join(TRIAGE_TRAIN))
    tg.add_argument("--test", default=",".join(TRIAGE_TEST))
    tg.add_argument("--replay", action="store_true")
    rl = sub.add_parser("real", help="真实图像评测：公开光伏缺陷数据集的现场照片（调用视觉模型）")
    rl.add_argument("--splits", default="test")
    rl.add_argument("--limit", type=int, default=0, help="每个类别最多取多少张，0 为全部")
    tw = sub.add_parser("twin", help="真实场地数字孪生：随机布置与孪生约束布置对比（不起飞、不调用模型）")
    tw.add_argument("--seeds", default="1-10")
    rt = sub.add_parser("routes", help="绕行方法对比：栅格 A* 与可见性图（不起飞、不调用模型）")
    rt.add_argument("--seeds", default="1-100", help="例如 1-100")
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
    elif args.cmd == "triage":
        report = run_triage(args.train.split(","), args.test.split(","), args.replay)
    elif args.cmd == "real":
        report = run_real(args.splits.split(","), args.limit)
    elif args.cmd == "twin":
        lo, _, hi = args.seeds.partition("-")
        report = run_twin(list(range(int(lo), int(hi or lo) + 1)))
    elif args.cmd == "routes":
        lo, _, hi = args.seeds.partition("-")
        report = run_routes(list(range(int(lo), int(hi or lo) + 1)))
    elif args.cmd == "vision":
        report = run_vision(args.scenes.split(","), args.repeat, args.provider)
    else:
        report = run_planning(args.repeat)
    print("报告：", write_report(report), flush=True)
    if args.cmd == "troubleshoot":
        os._exit(0)


if __name__ == "__main__":
    main()
