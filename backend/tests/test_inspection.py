"""场景生成、评分、巡检员规则与模型输出的解析（不调用模型）。"""
import json
import math

import pytest

from droneagent import inspection, llm, scoring, store, vision
from droneagent.agents import roles
from droneagent.llm import Usage
from droneagent.sim import scene


def test_scene_is_deterministic(site, tmp_path):
    scene.generate(7, "again", out_dir=tmp_path / "other")
    other = json.loads((tmp_path / "other" / "again" / "site.json").read_text())
    assert [p["defect"] for p in other["panels"]] == [p["defect"] for p in site["panels"]]
    assert other["arrays"] == site["arrays"] and other["geofence"] == site["geofence"]
    defects = [p for p in site["panels"] if p["defect"]]
    assert 20 <= len(site["panels"]) <= 120 and len(defects) >= 1
    assert all(not (p["defect"] and p["distractor"]) for p in site["panels"])


@pytest.mark.parametrize("seed", range(20, 30))
def test_random_layout_is_flyable(seed, tmp_path):
    """随机布局的不变量：阵列在围栏内、互不重叠、避开禁飞区；拍摄点在围栏内；起飞点不在阵列或禁飞区里。"""
    from droneagent import safety, survey
    out = scene.generate(seed, f"t{seed}", out_dir=tmp_path, textures=False)
    site = json.loads((out / "site.json").read_text())
    assert 2 <= len(site["arrays"]) <= 6
    for a in site["arrays"]:
        assert -30 <= a["yaw_deg"] <= 30
        step = survey.overview_step(a, 20)
        assert not safety.check_point(site, step.position), f"{a['id']} 的拍摄点不可用"
        for b in site["arrays"]:
            if a is not b:
                assert math.dist(a["center"], b["center"]) > a["radius"] + b["radius"]
    for p in site["panels"]:
        assert all(safety._inside(site["geofence"], c[0], c[1]) for c in p["corners"])
    assert all(math.dist((0, 0), z["center"]) > z["radius"] for z in site["no_fly_zones"])


def test_scoring(site):
    truth = {p["id"]: p["defect"]["type"] for p in site["panels"] if p["defect"]}
    first, *rest = truth
    clean = next(p["id"] for p in site["panels"] if not p["defect"])
    reported = {k: truth[k] for k in rest} | {first: "crack" if truth[first] != "crack" else "soiling", clean: "soiling"}
    s = scoring.score(site, reported)
    assert s["recall"] == 1.0 and s["false_alarms"] == [clean] and s["wrong_type"][0]["panel"] == first
    assert s["precision"] == pytest.approx(len(truth) / (len(truth) + 1), abs=1e-3)
    # 只统计巡检过的列
    only_a = scoring.score(site, {}, ["A"])
    assert only_a["panels"] == sum(1 for p in site["panels"] if p["array"] == "A")
    assert only_a["recall"] in (0, None)


def test_select_targets_policies():
    panels = [{"id": f"A-R1-C{i:02d}"} for i in range(1, 6)]
    cands = [{"id": "A-R1-C01", "status": "defect", "type": "missing", "confidence": 0.95},
             {"id": "A-R1-C02", "status": "defect", "type": "crack", "confidence": 0.8},
             {"id": "A-R1-C03", "status": "defect", "type": "soiling", "confidence": 0.5},
             {"id": "A-R1-C04", "status": "unsure", "type": None, "confidence": 0.3}]
    targets, confirmed = inspection.select_targets("flagged", cands, panels)
    assert confirmed == ["A-R1-C01"] and targets == ["A-R1-C03", "A-R1-C02"]     # 置信度低的先复核
    assert inspection.select_targets("flagged_and_unsure", cands, panels)[0][-1] == "A-R1-C04"
    assert len(inspection.select_targets("all", cands, panels)[0]) == 4         # 缺失已确认，其余全部复核
    assert inspection.select_targets("none", cands, panels) == ([], [])


@pytest.mark.parametrize("data, expected", [
    ({"status": "defect", "type": "crack", "confidence": 0.9}, "defect"),
    ({"status": "OK", "type": None}, "ok"),
    ({"status": "缺陷", "type": "broken"}, "defect"),
    ({"status": "broken", "type": "broken", "confidence": 0.8}, "defect"),     # 状态写错但类型和置信度明确
    ({"status": "maybe", "type": None, "confidence": 0.9}, "unsure"),           # 无法识别时绝不默认为正常
    ({}, "unsure"),
])
def test_verdict_parsing(data, expected):
    assert vision._status(data) == expected


def test_adjudicate_rechecks_conflicts(monkeypatch):
    answers = iter([{"id": "X", "status": "ok", "type": None, "confidence": 0.7, "evidence": ""},
                    {"id": "X", "status": "defect", "type": "crack", "confidence": 0.8, "evidence": ""}])
    usage = Usage("m", 1, 1, 0, 1, 0.0)
    monkeypatch.setattr(vision, "verify", lambda tile, provider=None, attempt=0, thinking=None: (next(answers), usage))
    verdict, usages, again = inspection.adjudicate(None, {"status": "defect", "confidence": 0.8})
    assert again and verdict["status"] == "defect" and len(usages) == 2
    # 再看一次时按配置打开推理
    monkeypatch.setattr(inspection.config, "VISION_THINKING_RECHECK", True)
    seen = []
    monkeypatch.setattr(vision, "verify", lambda tile, provider=None, attempt=0, thinking=None: (seen.append((attempt, thinking)) or {"id": "X", "status": "ok", "type": None, "confidence": 0.7, "evidence": ""}, usage))
    inspection.adjudicate(None, {"status": "defect", "confidence": 0.8})
    assert seen == [(0, None), (1, True)]
    # 初筛本来就不确定时，不再追加复核
    monkeypatch.setattr(vision, "verify", lambda tile, provider=None, attempt=0, thinking=None: ({"id": "X", "status": "ok", "type": None, "confidence": 0.9, "evidence": ""}, usage))
    assert inspection.adjudicate(None, {"status": "unsure", "confidence": 0.3})[2] is False


def test_llm_json_parsing():
    assert llm._parse('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm._parse('结论如下：{"status": "ok"} 以上') == {"status": "ok"}
    with pytest.raises(ValueError):
        llm._parse("没有 JSON")


def test_coordinator_validates_model_output(site, monkeypatch):
    usage = Usage("m", 1, 1, 0, 1, 0.0)
    monkeypatch.setattr(roles, "chat_json", lambda *a, **k: ({"type": "task", "targets": ["A", "Z"]}, usage))
    brief, _ = roles.coordinate("检查 A 和 Z", site, 100)
    assert brief["type"] == "clarify" and "Z" in brief["question"]
    monkeypatch.setattr(roles, "chat_json", lambda *a, **k: ({"type": "task", "targets": ["all"], "reserve_pct": 5,
                                                              "thoroughness": "extreme", "focus": ["crack", "rust"]}, usage))
    brief, _ = roles.coordinate("全部", site, 100)
    assert brief["targets"] == [a["id"] for a in site["arrays"]] and brief["reserve_pct"] == 15 and brief["thoroughness"] == "standard"
    assert brief["focus"] == ["crack"]
    monkeypatch.setattr(roles, "chat_json", lambda *a, **k: ({"type": "something"}, usage))
    assert roles.coordinate("?", site, 100)[0]["type"] == "unsupported"


def test_store_mission_lifecycle():
    mid = store.create_mission("检查 A 列", "solar_7")
    store.event(mid, "planner", "plan", "计划", {"x": 1})
    assert store.update_mission(mid, status="running", only_from=("planning",))
    assert store.update_mission(mid, status="cancelled", only_from=("running",))
    assert not store.update_mission(mid, status="done", only_from=("running",))   # 已取消的任务不会被改成完成
    store.upsert_finding(mid, "A-R1-C01", status="suspect", type="crack", confidence=0.6)
    store.upsert_finding(mid, "A-R1-C01", status="confirmed", confidence=0.9)
    f = store.findings(mid)[0]
    assert f["status"] == "confirmed" and f["type"] == "crack" and f["confidence"] == 0.9
    assert [e["kind"] for e in store.events(mid)] == ["plan"]


def test_triage_threshold_and_features():
    import numpy as np
    from PIL import Image
    from droneagent import triage
    y = np.array([0, 0, 1, 1, 1, 1])
    p = np.array([0.1, 0.5, 0.2, 0.6, 0.7, 0.9])
    th = triage.threshold_for_recall(y, p, 1.0)
    assert (p[y == 1] >= th).all() and th <= 0.2
    th75 = triage.threshold_for_recall(y, p, 0.75)
    assert (p[y == 1] >= th75).mean() >= 0.75 and th75 > th
    plain = Image.new("RGB", (150, 255), (30, 40, 90))
    cracked = plain.copy()
    for i in range(255):
        cracked.putpixel((40 + i // 6, i), (250, 250, 250))
    a, b = triage.features(plain), triage.features(cracked)
    assert a.shape == b.shape and not np.allclose(a, b)


def test_screen_frame_skips_triaged_panels(site, monkeypatch):
    from droneagent import triage
    panels = [p for p in site["panels"] if p["array"] == "A"][:4]
    monkeypatch.setattr(vision, "rectify", lambda frame, p, size=None: vision.Tile(p["id"], None, 50))
    monkeypatch.setattr(triage, "photo_features", lambda frame, ps: ([p["id"] for p in ps], [[0]] * len(ps)))
    sent = []
    monkeypatch.setattr(vision, "screen", lambda tiles, provider=None: (sent.extend(t.panel_id for t in tiles) or [], Usage("m", 1, 1, 0, 1, 0.0)))

    class Model:
        def predict_proba(self, x):
            import numpy as np
            return np.array([[0.9, 0.1], [0.2, 0.8], [0.95, 0.05], [0.4, 0.6]])
    res = inspection.screen_frame(None, panels, triage_model={"model": Model(), "threshold": 0.3})
    assert res.triaged == [panels[0]["id"], panels[2]["id"]] and sent == [panels[1]["id"], panels[3]["id"]] and res.tiles == 4
