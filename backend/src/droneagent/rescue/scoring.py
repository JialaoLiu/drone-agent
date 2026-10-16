"""救援任务与标准答案对照（仿真才有答案）：目标在确认那一刻的真实位置来自确认照片附带的位姿。"""
from __future__ import annotations

import math

FOUND_RADIUS = 5.0       # 确认位置距真实位置 5 米以内算找到


def score(site: dict, result: dict, photo_meta: dict | None) -> dict:
    target = next(p for p in site["people"] if p["role"] == "target")
    found = result.get("found")
    out = {"scenario": "rescue", "target_state": target["state"], "target_description": site["brief"]["description"],
           "found": False, "misidentified": False, "location_error_m": None, "elapsed_s": None,
           "bystanders_seen": sum(1 for d in result.get("detections", []) if d["kind"] in ("bystander", "rejected")),
           "decoys": len(site.get("decoys", [])), "coverage": result.get("coverage")}
    if not found:
        return out
    truth = ((photo_meta or {}).get("people") or {}).get(target["model"])
    if truth is None:
        return {**out, "note": "确认照片没有附带人物真实位置，无法评分"}
    err = math.hypot(found["x"] - truth[0], found["y"] - truth[1])
    out.update(location_error_m=round(err, 2), elapsed_s=found.get("elapsed_s"))
    if err <= FOUND_RADIUS:
        out["found"] = True
    else:
        # 确认的位置离目标很远：把别人（路人或衣物）认成了目标
        others = {k: v for k, v in ((photo_meta or {}).get("people") or {}).items() if k != target["model"]}
        nearest = min(others.items(), key=lambda kv: math.hypot(found["x"] - kv[1][0], found["y"] - kv[1][1]), default=None)
        out.update(misidentified=True, confused_with=nearest[0] if nearest and math.hypot(found["x"] - nearest[1][0], found["y"] - nearest[1][1]) <= FOUND_RADIUS else "物体")
    return out
