"""上下文压缩：诊断员的实验历史、报告员的缺陷清单不随规模线性增长。"""
import json

from droneagent import context
from droneagent.agents import report, roles


def _exp(i, improved):
    return {"restored": [f"P{i}"], "metrics_after": {f"m{k}": {"before": 1.0, "after": 0.5, "status_after": "ok"} for k in range(8)},
            "observations_after": ["动作超时"] * 3, "improved": ["航向误差 6.3→3.8"] if improved else []}


def test_experiment_history_stays_small():
    full = [_exp(i, i % 2 == 0) for i in range(20)]
    compact = context.compact_experiments(full)
    assert len(compact) == 20 and "metrics_after" in compact[-1] and "metrics_after" not in compact[0]
    assert compact[0]["restored"] == ["P0"] and compact[0]["conclusion"] == "有改善"
    assert len(json.dumps(compact, ensure_ascii=False)) < len(json.dumps(full, ensure_ascii=False)) / 3


def test_reporter_input_bounded_and_ids_still_valid():
    result = {"inspected_arrays": ["A", "B"], "skipped_arrays": [], "panels_inspected": 500, "cleared": 3, "closeup_policy": "all",
              "sorties": 3, "flight_seconds": 900, "battery_used": 200, "error": None, "unjudged_arrays": [],
              "defects": [{"panel_id": f"{'AB'[i % 2]}-R1-C{i:02d}", "label": "裂纹", "confidence": 0.9, "verify_photo": 1} for i in range(80)],
              "unverified": []}
    data = report.for_reporter(result)
    assert data["defects"]["其余数量"] == 80 - context.KEEP_ITEMS and sum(data["defects"]["其余按列"].values()) == 68
    small = report.for_reporter({**result, "defects": result["defects"][:5]})
    assert isinstance(small["defects"], list)
    assert len(json.dumps(data, ensure_ascii=False)) < 2 * len(json.dumps(report.for_reporter({**result, "defects": result["defects"][:12]}), ensure_ascii=False))
    assert roles.check_advice("先处理 A-R1-C00。", {"A-R1-C00"})


def test_budget_warning():
    assert context.over_budget("reporter", 3099) and not context.over_budget("reporter", 430)
