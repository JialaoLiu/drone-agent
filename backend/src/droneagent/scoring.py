"""把巡检结果与场景的标准答案对比：检出率、精确率、类型准确率。评测与网页“对照答案”共用。"""
from __future__ import annotations


def score(site: dict, reported: dict[str, str | None], arrays: list[str] | None = None) -> dict:
    """reported：板编号 -> 判定的缺陷类型（只包含判为缺陷的板）。arrays：只统计这些列（未巡检的列不计入）。"""
    panels = [p for p in site["panels"] if not arrays or p["array"] in arrays]
    truth = {p["id"]: p["defect"]["type"] for p in panels if p["defect"]}
    ids = {p["id"] for p in panels}
    pred = {k: v for k, v in reported.items() if k in ids}
    tp = [k for k in pred if k in truth]
    fp = [k for k in pred if k not in truth]
    fn = [k for k in truth if k not in pred]
    type_ok = [k for k in tp if pred[k] == truth[k]]
    by_type = {}
    for t in sorted(set(truth.values())):
        total = [k for k, v in truth.items() if v == t]
        by_type[t] = {"total": len(total), "found": sum(1 for k in total if k in pred)}
    minor = [p["id"] for p in panels if p["defect"] and p["defect"].get("severity") == "minor"]
    distractors = [p["id"] for p in panels if p.get("distractor")]
    return {
        "panels": len(panels), "defects": len(truth), "reported": len(pred),
        "recall": round(len(tp) / len(truth), 3) if truth else None,
        "precision": round(len(tp) / len(pred), 3) if pred else None,
        "type_accuracy": round(len(type_ok) / len(tp), 3) if tp else None,
        "minor_recall": round(sum(1 for k in minor if k in pred) / len(minor), 3) if minor else None,
        "distractor_false_alarms": sum(1 for k in distractors if k in pred),
        "by_type": by_type, "missed": fn, "false_alarms": fp,
        "wrong_type": [{"panel": k, "truth": truth[k], "reported": pred[k]} for k in tp if pred[k] != truth[k]],
    }
