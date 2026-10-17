"""应急救援：场景生成、搜索航线、发现分类、评分（不需要仿真和模型）。"""
import json
import math

from droneagent import config, safety, survey
from droneagent.agents.rescue import _classify
from droneagent.rescue import plan, scene, scoring


def _site(seed=3, environment="park"):
    out = scene.generate(seed, f"rescue_t{seed}", environment=environment)
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
    assert scene.generate(3, "rescue_t3b", environment="park") and json.loads((config.WORLDS_DIR / "rescue_t3b" / "site.json").read_text())["people"] == site["people"]   # 同一种子可复现


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
    assert not codes & {"geofence", "no_fly_zone", "altitude", "obstacle", "terrain"}     # 航线本身合规（电量是否够由规划员按半径调整）


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


def test_incident_is_visible_and_may_contain_errors():
    errs = 0
    for seed in range(1, 25):
        site, _ = _site(seed)
        inc = site["incident"]
        assert inc["text"] and "：" in inc["text"]                       # 报案人说的话
        assert "errors" not in inc and "reported" not in inc               # 网页可见部分不含误差说明与真实答案
        errs += bool(site["brief"]["incident"]["errors"])
    assert errs > 0                                                        # 有一部分警情故意不准


def test_search_all_identity_and_near_misses():
    from droneagent.rescue.roles import identity
    from droneagent.rescue.report import near_misses
    assert identity({"search_all": True}, {"person": "yes", "in_water": "no"}) == "person"
    brief = {"top": "红", "bottom": "蓝"}
    dets = [{"id": "P1", "kind": "rejected", "top": "红", "bottom": "黑", "x": 1, "y": 2, "photo": 5},
            {"id": "P2", "kind": "bystander", "top": "绿", "bottom": "白", "x": 3, "y": 4, "photo": 6}]
    m = near_misses(brief, dets)
    assert [x["id"] for x in m] == ["P1"] and "上衣" in m[0]["text"]


def test_only_target_can_be_in_water():
    """“水中有人就确认”依赖一个不变量：场景里只有目标会落水，路人不进水（独立审查 L1）。改场景生成时这条测试会提醒。"""
    for seed in range(1, 30):
        site, _ = _site(seed)
        assert all(p["state"] != "water" for p in site["people"] if p["role"] != "target")


def test_poi_items_real_landmarks_only():
    """真实场地地标：有类型的地物才当地标；同名或太近的只留一个；道路等只有名字的 OSM 元素不算（第三轮反馈：不存在的凉亭）。"""
    from droneagent import twin
    from droneagent.geo import Origin
    o = Origin(23.0, 113.0, 0.0)
    at = lambda x, y: o.to_global(x, y, 0)[:2]          # noqa: E731
    raw = [{"tags": {"amenity": "parking"}, "points": [at(20, 10)]},
           {"tags": {"amenity": "parking"}, "points": [at(25, 12)]},                     # 同名且太近
           {"tags": {"leisure": "playground", "name": "Wattle Park Playground"}, "points": [at(-30, 5)]},
           {"tags": {"highway": "residential", "name": "Main Road"}, "points": [at(0, 40)]},   # 道路名不算
           {"tags": {"man_made": "pier"}, "points": [at(10, -40)]},
           {"tags": {"amenity": "bench"}, "points": [at(500, 0)]}]                       # 场地外
    items = twin.poi_items(raw, o, 70)
    labels = [l["label"] for l in items]
    assert labels.count("停车场") == 1 and "Wattle Park Playground（儿童游乐场）" in labels and "码头" in labels
    assert "Main Road" not in labels and "长椅" not in labels
    assert next(l for l in items if l["label"] == "码头").get("shore")
    assert twin._poi_label({"name": "新舟码头"}) == "新舟码头"          # 百度检索结果只有名字


def test_real_site_without_pois_uses_directions_not_invented_objects():
    from droneagent.rescue import scene
    water = [[[10, 10], [30, 10], [30, 30], [10, 30]]]
    lms = scene._real_landmarks({"pois": []}, water, lambda x, y: 10 < x < 30 and 10 < y < 30)
    labels = {l["label"] for l in lms}
    assert "凉亭" not in labels and "码头" not in labels and "东门" not in labels
    assert "水边" in labels and any(l["source"] == "direction" for l in lms)
    assert any(l.get("shore") for l in lms)                  # 落水场景仍有“最后出现在水边”的地标


def test_buildings_are_extruded_footprints():
    from droneagent.sim.scene import obstacle_sdf
    b = obstacle_sdf({"id": "B1", "label": "建筑", "center": [0, 0], "radius": 10, "height": 12.0,
                      "polygon": [[0, 0], [10, 0], [10, 8], [0, 8], [0, 0]]})
    assert "<polyline>" in b and b.count("<point>") == 4 and "<height>12.0</height>" in b
    assert "<sphere>" in obstacle_sdf({"id": "T1", "label": "树", "center": [0, 0], "radius": 2, "height": 8})


def test_environments_are_valid_layouts():
    """山林、河岸、城市广场：起飞点离水和障碍物足够远，地标在围栏内，人站在可走区域，落水者在水里，广场没有落水。"""
    import math
    import random
    from droneagent.rescue import scene
    from droneagent.sim.scene import _inside
    from droneagent.twin import _edge_dist
    for env in ("forest", "riverside", "plaza"):
        for sd in range(1, 31):
            lay = scene._layout(random.Random(sd), None, env)
            for w in lay["water"]:
                assert not _inside(w, 0, 0) and _edge_dist(w, 0, 0) >= 10, (env, sd)
            for o in lay["obstacles"]:
                d = _edge_dist(o["polygon"], 0, 0) if o.get("polygon") else math.hypot(*o["center"]) - o["radius"]
                assert d >= 6, (env, sd, o["id"])
            assert len(lay["landmarks"]) >= 4 and all(abs(v) < scene.HALF for l in lay["landmarks"] for v in l["center"])
            people, _, brief = scene._people(random.Random(sd), lay)
            for p in people:
                assert lay["in_water"](*p["start"]) if p["state"] == "water" else lay["walkable"](*p["start"]), (env, sd, p["id"])
            if env == "plaza":
                assert people[0]["state"] != "water" and any(o.get("polygon") for o in lay["obstacles"])


def test_environment_choice_keeps_park_seeds_and_is_recorded(tmp_path):
    from droneagent.rescue import scene
    out = scene.generate(5, "rescue_env_t", out_dir=tmp_path, environment="forest")
    site = json.loads((out / "site.json").read_text())
    assert site["environment"]["type"] == "forest" and "不含坡度" in site["ground"]["note"]
    assert "ranger_hut" in (out / "rescue_env_t.sdf").read_text()
    assert scene.pick_environment(5) == scene.pick_environment(5)


def test_plaza_search_routes_around_tall_buildings():
    """城市广场：高于航线的楼当绕行区，拍摄点不在楼上，整条航线通过安全员（含真实轮廓的净空检查）。"""
    from droneagent.rescue import plan as search_plan
    for seed in (1, 2, 3, 4):
        site, _ = _site(seed, "plaza")
        routing = search_plan.route_site(site, 18)
        center = next(l for l in site["landmarks"] if l["id"] == site["brief"]["last_seen"])["center"]
        steps = search_plan.search_steps(routing, center, 30, 18)
        route = survey.with_detours(steps, routing)
        codes = {v.code for v in safety.check_plan(site, route)}
        assert steps and not codes & {"geofence", "no_fly_zone", "altitude", "obstacle", "terrain"}, (seed, codes)


def test_world_model_names_unique(tmp_path):
    """Gazebo 遇到重名模型会拒绝加载整个世界（河岸环境曾因两个 pier 启动超时）。"""
    import re
    from collections import Counter
    for env in ("park", "forest", "riverside", "plaza"):
        for sd in (1, 2, 3):
            out = scene.generate(sd, f"w_{env}_{sd}", out_dir=tmp_path, environment=env)
            names = re.findall(r'<model name="([^"]+)"', (out / f"w_{env}_{sd}.sdf").read_text())
            dup = [n for n, c in Counter(names).items() if c > 1]
            assert not dup, (env, sd, dup)


def test_low_pass_triggers_and_likely_target_report():
    """变焦拍到旁边的路人（目标走开了）：初筛衣着全对时也去低空重找；没确认的全对者报告为疑似目标，而不是“描述有误”。"""
    from droneagent.agents.rescue import _needs_low_pass
    from droneagent.rescue.report import near_misses
    brief = {"top": "绿", "bottom": "蓝", "age": "child"}
    seen = {"person": "yes", "top": "绿", "bottom": "蓝", "child": "yes"}
    assert _needs_low_pass(brief, seen, "no_person") and _needs_low_pass(brief, seen, "unsure")
    assert _needs_low_pass(brief, seen, "rejected")                                   # 初筛全对，变焦拍到的人不对
    assert _needs_low_pass(brief, {**seen, "bottom": None}, "rejected")               # 上衣对上、裤子看不到也算初筛相符
    assert not _needs_low_pass(brief, {**seen, "top": None}, "rejected")              # 初筛没看清上衣：变焦冲突就是排除
    assert not _needs_low_pass(brief, {**seen, "person": "unsure"}, "unsure")
    m = near_misses(brief, [{"id": "P7", "kind": "unsure", "x": 0, "y": 0, "photo": 1, "top": "绿", "bottom": "蓝"},
                            {"id": "P2", "kind": "rejected", "x": 0, "y": 0, "photo": 2, "top": "绿", "bottom": "黑"}])
    assert m[0]["id"] == "P7" and m[0]["full"] and "没能近距离确认" in m[0]["text"] and not m[1]["full"]


def test_already_seen_does_not_hide_a_different_person():
    """目标坐在路人旁边：同一张照片里的不算重复；之前照片里 3 米内的路人衣着不同也不算重复。"""
    from droneagent.agents.rescue import _already_seen
    pink = {"id": "P1", "x": 0, "y": 0, "kind": "bystander", "photo": 7, "top": "粉"}
    target = {"x": 0.7, "y": -1.6, "top": "白"}
    assert not _already_seen([pink], target, 7)                         # 同一张照片
    assert not _already_seen([pink], target, 8)                         # 上衣不同，是另一个人
    assert _already_seen([pink], {"x": 1, "y": 1, "top": "粉色"}, 8)     # 同一个路人出现在下一张照片
    assert _already_seen([pink], {"x": 1, "y": 1, "top": None}, 8)      # 看不清衣着时按同一个人处理
