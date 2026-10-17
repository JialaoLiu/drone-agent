"""救援端到端评测：通过网页同一套接口，逐个种子生成场景、启动仿真、下达搜救任务、等任务结束、与标准答案对照。

用法（后端与网页服务需在运行，见 scripts/start.sh）：
  python -m droneagent.rescue.evaluate --seeds 1-6 [--place 文翰湖公园:23.0172979:113.232189]
结果写入 backend/eval/results/<时间>-rescue.json / .md。
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime

import httpx

from .. import config

API = "http://127.0.0.1:8881"


def _wait(client: httpx.Client, check, timeout: float, every: float = 3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = check()
        if v:
            return v
        time.sleep(every)
    raise TimeoutError("等待超时")


def run_one(client: httpx.Client, seed: int, location: dict | None) -> dict:
    r = client.post("/scenes", json={"seed": seed, "scenario": "rescue", "location": location})
    r.raise_for_status()
    scene = r.json()["name"]
    truth = client.get(f"/site/{scene}/truth").json()
    target = next(p for p in truth["people"] if p["role"] == "target")
    client.post("/sim/start", json={"scene": scene}).raise_for_status()      # 被拒绝（例如有任务在执行）时直接报错
    _wait(client, lambda: client.get("/sim").json().get("job", {}).get("state") == "idle" and client.get("/sim").json()["px4"], 240)
    last = {}
    try:
        _wait(client, lambda: (last.update(client.get("/sim/health").json()) or last.get("ok") is True), 180)
    except TimeoutError:
        raise TimeoutError(f"飞行前检查 3 分钟内没有通过：{last.get('advice') or [i['label'] + ' ' + i['detail'] for i in last.get('items', []) if i.get('ok') is False]}")
    task = f"公园里有人走失：{truth['brief']['description']}，最后在{truth['brief']['last_seen_label']}附近被看到"
    retried = False
    for attempt in range(2):
        r = client.post("/missions", json={"task": task, "auto_approve": True})
        r.raise_for_status()
        mid = r.json()["id"]
        started = time.time()
        try:
            status = _wait(client, lambda: (s := client.get(f"/missions/{mid}").json()["status"]) not in ("planning", "awaiting_approval", "running", "queued") and s, 900, 6)
        except TimeoutError:
            client.post(f"/missions/{mid}/cancel")        # 卡住的任务主动取消，不拖累后面的场景
            time.sleep(20)
            status = "timeout"
        m = client.get(f"/missions/{mid}").json()
        # 飞行前检查因电脑临时忙（仿真速度不足）没有起飞：等 30 秒重试一次，报告中注明
        if status == "failed" and not (m.get("result") or {}).get("searched") and attempt == 0:
            retried = True
            time.sleep(30)
            continue
        break
    score = client.get(f"/missions/{mid}/score").json() if m.get("result") else {}
    row = {"seed": seed, "scene": scene, "mission": mid, "status": status, "task": task, "target_state": target["state"],
           "bystanders": sum(1 for p in truth["people"] if p["role"] == "bystander"), "decoys": len(truth.get("decoys", [])),
           "found": score.get("found", False), "misidentified": score.get("misidentified", False), "location_error_m": score.get("location_error_m"),
           "elapsed_s": score.get("elapsed_s"), "coverage": score.get("coverage"), "wall_s": round(time.time() - started),
           "cost_yuan": round(m.get("usage", {}).get("total", {}).get("cost_yuan", 0), 4), "place": (location or {}).get("name"), "retried": retried}
    print(json.dumps(row, ensure_ascii=False), flush=True)
    return row


def write(rows: list[dict]) -> str:
    out = config.BACKEND_DIR / "eval" / "results"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-rescue"
    n = len(rows)
    found = sum(r["found"] for r in rows)
    errs = [r["location_error_m"] for r in rows if r["found"] and r["location_error_m"] is not None]
    times = [r["elapsed_s"] for r in rows if r["found"] and r["elapsed_s"] is not None]
    by_state = {}
    for st in ("walking", "sitting", "water"):
        rs = [r for r in rows if r["target_state"] == st]
        if rs:
            by_state[st] = {"n": len(rs), "found": sum(r["found"] for r in rs)}
    summary = {"runs": n, "found": found, "found_rate": round(found / n, 3) if n else None, "misidentified": sum(r["misidentified"] for r in rows),
               "location_error_m_mean": round(sum(errs) / len(errs), 2) if errs else None, "elapsed_s_mean": round(sum(times) / len(times)) if times else None,
               "cost_yuan_mean": round(sum(r["cost_yuan"] for r in rows) / n, 4) if n else None, "by_state": by_state}
    (out / f"{stamp}.json").write_text(json.dumps({"suite": "rescue", "summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    labels = {"walking": "走动", "sitting": "静止", "water": "落水"}
    lines = [f"# 评测报告 {stamp}", "", f"端到端：生成救援场景 → 启动仿真 → 下达搜救任务（自动起飞）→ 与标准答案对照。共 {n} 个场景。", "",
             f"- 找到：{found}/{n}；错认（把路人或衣物确认为目标）：{summary['misidentified']}",
             f"- 定位误差平均 {summary['location_error_m_mean']} 米；发现用时平均 {summary['elapsed_s_mean']} 秒；每次模型费用平均 ¥{summary['cost_yuan_mean']}",
             "- 按目标状态：" + "；".join(f"{labels[k]} {v['found']}/{v['n']}" for k, v in by_state.items()), "",
             "| 场景 | 地点 | 目标状态 | 路人 | 干扰衣物 | 结果 | 定位误差 | 发现用时 | 覆盖 | 费用 |", "| --- | --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |"]
    for r in rows:
        res = "找到" if r["found"] else ("错认" if r["misidentified"] else ("未找到" if r["status"] == "done" else r["status"]))
        lines.append(f"| {r['scene']} | {r['place'] or '随机'} | {labels.get(r['target_state'], r['target_state'])} | {r['bystanders']} | {r['decoys']} | {res} | "
                     f"{r['location_error_m'] if r['location_error_m'] is not None else '—'} | {r['elapsed_s'] if r['elapsed_s'] is not None else '—'} | {r['coverage']} | ¥{r['cost_yuan']} |" + ("（重试一次）" if r.get("retried") else ""))
    (out / f"{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(out / f"{stamp}.md")


def main() -> None:
    parser = argparse.ArgumentParser(description="救援端到端评测")
    parser.add_argument("--seeds", default="1-6")
    parser.add_argument("--place", action="append", default=[], help="名称:纬度:经度（WGS-84），可重复")
    args = parser.parse_args()
    lo, _, hi = args.seeds.partition("-")
    jobs = [(s, None) for s in range(int(lo), int(hi or lo) + 1)]
    for i, p in enumerate(args.place):
        name, lat, lon = p.split(":")
        jobs.append((100 + i, {"name": name, "address": "", "city": "", "lat": float(lat), "lon": float(lon), "source": "评测指定（WGS-84）"}))
    rows = []
    with httpx.Client(base_url=API, timeout=120) as client:
        for seed, loc in jobs:
            try:
                rows.append(run_one(client, seed, loc))
            except Exception as error:      # noqa: BLE001  单个场景失败不影响其余
                print(f"种子 {seed} 失败：{type(error).__name__}：{error}", flush=True)
                rows.append({"seed": seed, "scene": f"rescue_{seed}", "status": f"error: {error}", "target_state": "?", "bystanders": 0, "decoys": 0,
                             "found": False, "misidentified": False, "location_error_m": None, "elapsed_s": None, "coverage": None, "cost_yuan": 0,
                             "place": (loc or {}).get("name"), "mission": None, "task": "", "wall_s": 0})
    print("报告：", write(rows), flush=True)


if __name__ == "__main__":
    main()
