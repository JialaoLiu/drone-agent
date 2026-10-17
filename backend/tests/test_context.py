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
    assert data["defects"]["其余数量"] == 80 - context.KEEP_ITEMS
    assert data["defects"]["全部按列和类型"] == {"A": {"裂纹": 40}, "B": {"裂纹": 40}}      # 统计覆盖全部条目，不只是其余
    small = report.for_reporter({**result, "defects": result["defects"][:5]})
    assert isinstance(small["defects"], list)
    assert len(json.dumps(data, ensure_ascii=False)) < 2 * len(json.dumps(report.for_reporter({**result, "defects": result["defects"][:12]}), ensure_ascii=False))
    assert roles.check_advice("先处理 A-R1-C00。", {"A-R1-C00"})
    assert len(report.for_reporter(result, keep=10 ** 6)["defects"]) == 80      # 评测对比用的完整清单


def test_reporter_keeps_confident_items_within_tier():
    mk = lambda pid, t, c: {"panel_id": pid, "type": t, "label": t, "confidence": c, "verify_photo": 1}   # noqa: E731
    result = {"inspected_arrays": ["A"], "skipped_arrays": [], "panels_inspected": 50, "cleared": 0, "closeup_policy": "all",
              "flight_seconds": 1, "battery_used": 1, "error": None, "unverified": [],
              "defects": [mk("A-R1-C01", "broken", 0.6), mk("A-R1-C02", "crack", 0.99), mk("F-R1-C01", "broken", 0.9)]}
    assert [d["panel_id"] for d in report.for_reporter(result)["defects"]] == ["F-R1-C01", "A-R1-C01", "A-R1-C02"]


def test_synthetic_result_sorted_by_priority():
    from droneagent import evaluate
    site = {"arrays": [{"id": "A"}], "panels": [{"id": f"A-R1-C{i:02d}"} for i in range(60)]}
    r = evaluate._synthetic_result(site, 40, 1)
    order = [report.PRIORITY[d["type"]] for d in r["defects"]]
    assert len(r["defects"]) == 40 and len(r["unverified"]) == 8 and order == sorted(order)


def test_budget_warning():
    assert context.over_budget("reporter", 3099) and not context.over_budget("reporter", 430)


def test_hotspot_arrays_counted_by_program():
    mk = lambda pid, t: {"panel_id": pid, "type": t}   # noqa: E731
    ds = [mk("F-R1-C01", "broken")] * 3 + [mk("D-R1-C01", "missing")] * 2 + [mk("C-R1-C01", "broken")] * 2 + [mk("A-R1-C01", "crack")] * 9
    assert report.hotspot_arrays(ds) == ["C", "D", "F"]          # 第二名并列都算，裂纹不计
    assert report.hotspot_arrays([mk("A-R1-C01", "broken")]) == []


def test_column_count_words_normalized(monkeypatch):
    from droneagent.llm import Usage
    monkeypatch.setattr(roles, "chat_json", lambda *a, **k: ({"advice": "先处理 A、D 两列的破碎板，再看 A-R1-C01。", "priorities": []}, Usage("m", 1, 1, 0, 1, 0.0)))
    out, _ = roles.report_summary({"defects": [{"panel_id": "A-R1-C01"}], "unverified": []})
    assert out["advice"] == "先处理 A、D 列的破碎板，再看 A-R1-C01。" and not out["advice_rejected"]


def test_check_advice_rejects_chinese_quantities():
    for bad in ["缺陷比例为百分之九十九，建议全面更换。", "约三分之一的板需要清洗。", "请检查三到五块板。", "大部分板需要清洗。", "先处理两处破损。"]:
        assert not roles.check_advice(bad, set()), bad
    assert roles.check_advice("先处理破碎和缺失的板，再统一安排清洗，未确认的疑似请到现场一并确认。", set())   # 普通用词“统一”“一并”不误伤
