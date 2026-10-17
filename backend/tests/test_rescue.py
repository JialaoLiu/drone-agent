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


def test_attributes_match_rules():
    from droneagent.rescue.roles import attributes_match, color_key
    t = {"top": "橙", "bottom": "灰", "age": "child"}
    assert color_key("橘色") == "橙" and color_key("深蓝") == "蓝" and color_key("看不清") is None
    assert attributes_match(t, {"top": "橙", "bottom": "灰", "child": "unsure"})          # 看不出年龄不算矛盾
    assert attributes_match(t, {"top": "橙色", "bottom": None, "child": "unsure"})        # 裤子看不到时只比上衣
    assert not attributes_match(t, {"top": "橙", "bottom": "黑", "child": "unsure"})
    assert not attributes_match(t, {"top": "橙", "bottom": "灰", "child": "no"})          # 明确是成年人
    assert not attributes_match(t, {"top": "红", "bottom": "灰", "child": "yes"})


def test_own_shadow_filter():
    from types import SimpleNamespace
    from droneagent.agents.rescue import _own_shadow
    site = {"sun": [0.3, 0.0, -0.95]}
    frame = SimpleNamespace(pose=SimpleNamespace(position=(10.0, 0.0, 15.0)))
    shadow_x = 10.0 - 15.0 * 0.3 / -0.95
    assert _own_shadow(site, frame, {"x": shadow_x, "y": 0.2}) and not _own_shadow(site, frame, {"x": 10.0, "y": 8.0})


def test_identity_conflict_cannot_be_overridden_by_model_label():
    from droneagent.rescue.roles import identity
    t = {"top": "红", "bottom": "蓝", "age": "child"}
    assert identity(t, {"person": "yes", "top": "蓝", "bottom": "黑", "child": "no", "in_water": "no", "match": "yes"}) == "rejected"   # 审查二 H03
    assert identity(t, {"person": "yes", "top": None, "bottom": None, "child": "unsure", "in_water": "no", "match": "partial"}) == "unsure"
    assert identity(t, {"person": "unsure", "match": "unsure"}) == "unsure"
    assert identity(t, {"person": "no", "match": "no"}) == "no_person"
    assert identity(t, {"person": "yes", "top": "灰", "in_water": "yes", "match": "no"}) == "water"
    assert identity(t, {"person": "yes", "top": "红色", "bottom": "蓝", "child": "unsure", "in_water": "no", "match": "partial"}) == "confirmed"


def test_classify_unsure_match_still_followed():
    assert _classify({"person": "yes", "match": "unsure", "in_water": "no"}) == "candidate"     # 审查二 H04


def test_low_pass_checks_real_path_from_current_position():
    """飞机在 (-20,20,16)，候选在 (20,20)，中间有 12 米高的树：平移段要过安全员（审查二 H01）。"""
    from droneagent.agents.rescue import _low_pass
    site = {"geofence": [[-70, -70], [70, -70], [70, 70], [-70, 70]], "max_altitude": 40, "min_altitude": 8, "no_fly_zones": [],
            "obstacles": [{"id": "T1", "label": "树", "center": [0, 20], "radius": 3, "height": 12}]}
    item = {"id": "P1", "x": 20, "y": 20}
    assert _low_pass(site, (-20, 20, 16), item, 16, 90) is not None          # 16 米平移在树顶 3 米以上：可以
    assert _low_pass(site, (-20, 20, 13), item, 16, 90) is None              # 当前只有 13 米：平移会擦到树，不补拍
    near_tree = {"id": "P2", "x": 1, "y": 20}
    plan = _low_pass(site, (-20, 20, 16), near_tree, 16, 90)
    assert plan is None or plan[0] >= 12                                       # 树旁边不能降到 9 米


def test_rescue_report_keeps_cancelled(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from droneagent import store
    from droneagent.agents import rescue, runtime
    site, _ = _site()
    mid = store.create_mission("找人", site["name"])
    store.update_mission(mid, status="cancelled")          # 返航途中被取消
    async def fake_flight():
        return SimpleNamespace(state=SimpleNamespace(battery=60.0))
    monkeypatch.setattr(runtime, "flight", fake_flight)
    from droneagent.rescue import report as rr
    monkeypatch.setattr(rr, "render", lambda *a: "/tmp/x.html")
    state = {"mission_id": mid, "scene": site["name"], "task": "找人", "plan": {"photos": 10, "center": [0, 0], "last_seen_label": "码头"},
             "brief": {"description": "小孩"}, "detections": [], "searched": 3}
    asyncio.run(rescue.report(state))
    assert store.mission_status(mid) == "cancelled"          # 审查二 H06
