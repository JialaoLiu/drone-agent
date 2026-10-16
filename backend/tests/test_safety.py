"""安全员的确定性规则：围栏、禁飞区（含航段）、障碍物净空、高度、电量。"""
from droneagent import safety, survey
from droneagent.agents.mission import build_steps, split_sorties


def step(x, y, z):
    return survey.Step("overview", "T", (x, y, z), 0, -60, "wide")


def codes(violations):
    return {v.code for v in violations}


def test_valid_overview_plan_passes(site, monkeypatch):
    from droneagent import config
    monkeypatch.setattr(config, "SEGMENT_SCREEN", False)
    plan = {"targets": [a["id"] for a in site["arrays"]], "closeup_policy": "flagged", "standoff_m": 20}
    assert safety.check_plan(site, build_steps(site, plan)) == []


def test_segment_plan_split_into_passing_sorties(site, monkeypatch):
    from droneagent import config
    monkeypatch.setattr(config, "SEGMENT_SCREEN", True)
    plan = {"targets": [a["id"] for a in site["arrays"]], "closeup_policy": "flagged", "standoff_m": 20, "max_sorties": 3}
    steps = build_steps(site, plan, with_estimated_closeups=False)
    assert any(s.kind == "segment" for s in steps)
    for targets in split_sorties(site, plan, 100.0, 25.0):         # 分段拍摄更耗电，按电量分架次后每个架次都要通过
        assert safety.check_plan(site, build_steps(site, plan, targets=targets), battery=100.0, reserve=25.0) == []
    assert not any(s.kind == "segment" for s in build_steps(site, {**plan, "closeup_policy": "all"}, with_estimated_closeups=False))


def test_altitude_and_geofence(site):
    assert "altitude" in codes(safety.check_plan(site, [step(10, 10, 60)]))
    assert "geofence" in codes(safety.check_plan(site, [step(200, 10, 20)]))


def test_no_fly_zone_point_and_segment(site):
    z = site["no_fly_zones"][0]
    assert "no_fly_zone" in codes(safety.check_plan(site, [step(z["center"][0], z["center"][1], 20)]))
    # 两个拍摄点都在禁飞区外，但连线穿过禁飞区
    cx, cy = z["center"]
    r = z["radius"] + safety.MARGIN + 2
    crossing = [step(cx, cy - r, 20), step(cx, cy + r, 20)]   # 南北向穿越（东西向会越出围栏）
    assert all(not safety.check_point(site, s.position) for s in crossing)
    assert "no_fly_zone" in codes(safety.check_plan(site, crossing))


def test_obstacle_clearance_depends_on_altitude(site):
    tree = site["obstacles"][0]
    x, y = tree["center"]
    low = [step(x - 8, y, 6), step(x + 8, y, 6)]
    high = [step(x - 8, y, tree["height"] + safety.OBSTACLE_VERTICAL_MARGIN + 1), step(x + 8, y, tree["height"] + safety.OBSTACLE_VERTICAL_MARGIN + 1)]
    assert "obstacle" in codes(safety.check_plan(site, low))
    assert "obstacle" not in codes(safety.check_plan(site, high))


def test_battery_reserve(site):
    plan = {"targets": [a["id"] for a in site["arrays"]], "closeup_policy": "all", "standoff_m": 20}
    steps = build_steps(site, plan)
    assert "battery" in codes(safety.check_plan(site, steps, battery=100, reserve=25))
    assert "battery" in codes(safety.check_plan(site, build_steps(site, {**plan, "closeup_policy": "none"}), battery=20, reserve=25))


def test_detour_avoids_no_fly_zones(site):
    """航段穿过禁飞区时插入绕行点；绕行后的每一段都通过安全员校验。"""
    z = site["no_fly_zones"][0]
    cx, cy = z["center"]
    r = z["radius"] + safety.MARGIN + 2
    a, b = (cx, cy - r, 20.0), (cx, cy + r, 20.0)
    via = survey.detour(a, b, site)
    assert via, "应当需要绕行"
    route = [a, *via, b]
    for i in range(len(route) - 1):
        assert not [v for v in safety.check_segment(site, route[i], route[i + 1], i) if v.code == "no_fly_zone"]
    assert survey.detour((0, 0, 20), (0.5, 0.5, 20), site) == []        # 不穿过禁飞区时不绕


def test_split_sorties_respects_battery(site):
    plan = {"targets": [a["id"] for a in site["arrays"]], "closeup_policy": "all", "standoff_m": 20, "max_sorties": 3}
    sorties = split_sorties(site, plan, battery=100, reserve=25)
    assert sorted(sum(sorties, [])) == sorted(plan["targets"])
    one = split_sorties(site, {**plan, "max_sorties": 1}, battery=100, reserve=25)
    if safety.check_plan(site, build_steps(site, plan, targets=one[0]), battery=100, reserve=25):
        assert len(sorties) >= 2                                          # 一个架次装不下时会拆分
    for targets in sorties:
        assert safety.check_plan(site, build_steps(site, plan, targets=targets), battery=100, reserve=25) == []
    # 只允许一个架次时不拆分，交给安全员拒绝
    assert len(split_sorties(site, {**plan, "max_sorties": 1}, battery=100, reserve=25)) == 1


def test_terrain_clearance_uses_height_above_ground():
    from droneagent import safety
    site = {"geofence": [[-100, -100], [100, -100], [100, 100], [-100, 100]], "no_fly_zones": [], "obstacles": [],
            "min_altitude": 5.0, "max_altitude": 40.0,
            "terrain": {"half": 100.0, "step": 100.0, "rel": [[0, 0, 0], [0, 0, 18], [0, -30, 0]]}}
    # 东北角地面高 18 米：起飞点上方 20 米处离地只有约 2 米
    assert [v.code for v in safety.check_point(site, (99, 0, 20))] == ["terrain"]
    # 北边中间地面低 30 米：相对起飞点 20 米，离地 50 米，超过限高
    assert [v.code for v in safety.check_point(site, (0, 99, 20))] == ["terrain"]
    assert not safety.check_point(site, (-99, -99, 20))
    # 没有高程数据的场地不受影响
    assert not safety.check_point({**site, "terrain": None}, (0, 99, 20))


def test_missing_textures_are_detected(tmp_path, monkeypatch):
    from droneagent import config, simctl
    monkeypatch.setattr(config, "WORLDS_DIR", tmp_path)
    (tmp_path / "w").mkdir()
    ok = tmp_path / "w" / "a.png"
    ok.write_bytes(b"x")
    (tmp_path / "w" / "w.sdf").write_text(f"<albedo_map>{ok}</albedo_map><albedo_map>/nowhere/b.png</albedo_map>")
    assert simctl.missing_textures("w") == ["/nowhere/b.png"]
