"""应急救援：场景生成、搜索航线、发现分类、评分（不需要仿真和模型）。"""
import json
import math

from droneagent import config, safety, survey
from droneagent.agents.rescue import _classify
from droneagent.rescue import plan, scene, scoring


def _site(seed=3):
    out = scene.generate(seed, f"rescue_t{seed}")
    return json.loads((out / "site.json").read_text(encoding="utf-8")), out


def test_scene_has_target_bystanders_and_crowd_file():
    site, out = _site()
    target = [p for p in site["people"] if p["role"] == "target"]
    assert len(target) == 1 and site["scenario"] == "rescue" and site["brief"]["description"]
    assert 5 <= len(site["people"]) - 1 <= 10 and site["decoys"]
    # 与目标上衣同色的路人，裤子或年龄必须不同（否则无法区分）
    t = target[0]
    for p in site["people"][1:]:
        assert not (p["top"] == t["top"] and p["bottom"] == t["bottom"] and p["child"] == t["child"])
    crowd = json.loads((out / "crowd.json").read_text(encoding="utf-8"))
    assert all(c["speed"] > 0 for c in crowd)
    assert scene.generate(3, "rescue_t3b") and json.loads((config.WORLDS_DIR / "rescue_t3b" / "site.json").read_text())["people"] == site["people"]   # 同一种子可复现


def test_water_target_last_seen_at_pier():
    for seed in range(1, 30):
        site, _ = _site(seed)
        t = site["people"][0]
        if t["state"] == "water":
            assert site["brief"]["last_seen"] == "pier" and t["z"] < 0
            return


def test_search_steps_inside_fence_and_start_near_last_seen():
    site, _ = _site()
    center = next(l for l in site["landmarks"] if l["id"] == site["brief"]["last_seen"])["center"]
    steps = plan.search_steps(site, center, 30, 16)
    assert steps and all(s.kind == "search" and s.pitch == -90 for s in steps)
    d = [math.hypot(s.position[0] - center[0], s.position[1] - center[1]) for s in steps]
    assert d[0] <= min(d) + 15          # 扩展方形：从最后出现的位置附近开始
    codes = {v.code for v in safety.check_plan(site, survey.with_detours(steps, site), battery=100.0, reserve=25.0)}
    assert not codes & {"geofence", "no_fly", "altitude", "obstacle"}     # 航线本身合规（电量是否够由规划员按半径调整）


def test_classify_people_in_water_always_followed():
    base = {"person": "yes", "match": "no", "in_water": "no"}
    assert _classify(base) == "bystander"
    assert _classify({**base, "in_water": "yes"}) == "candidate"      # 衣服在水下看不清，不能按颜色排除
    assert _classify({**base, "person": "no"}) == "object" and _classify({**base, "person": "unsure"}) == "unsure"
    assert _classify({**base, "match": "partial"}) == "candidate"


def test_scoring_found_and_misidentified():
    site, _ = _site()
    t = site["people"][0]
    meta = {"people": {t["model"]: [10.0, 10.0, 0.0], "person_b1": [30.0, 30.0, 0.0]}}
    ok = scoring.score(site, {"found": {"x": 11.0, "y": 10.5, "elapsed_s": 100}, "detections": []}, meta)
    assert ok["found"] and ok["location_error_m"] < 2
    bad = scoring.score(site, {"found": {"x": 30.5, "y": 30.0, "elapsed_s": 100}, "detections": []}, meta)
    assert not bad["found"] and bad["misidentified"] and bad["confused_with"] == "person_b1"
