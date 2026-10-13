"""安全员的确定性规则：围栏、禁飞区（含航段）、障碍物净空、高度、电量。"""
from droneagent import safety, survey
from droneagent.agents.mission import build_steps, split_sorties


def step(x, y, z):
    return survey.Step("overview", "T", (x, y, z), 0, -60, "wide")


def codes(violations):
    return {v.code for v in violations}


def test_valid_overview_plan_passes(site):
    plan = {"targets": [a["id"] for a in site["arrays"]], "closeup_policy": "flagged", "standoff_m": 20}
    assert safety.check_plan(site, build_steps(site, plan)) == []


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


def test_split_sorties_respects_battery(site):
    plan = {"targets": [a["id"] for a in site["arrays"]], "closeup_policy": "all", "standoff_m": 20, "max_sorties": 3}
    sorties = split_sorties(site, plan, battery=100, reserve=25)
    assert len(sorties) >= 2 and sorted(sum(sorties, [])) == sorted(plan["targets"])
    for targets in sorties:
        assert safety.check_plan(site, build_steps(site, plan, targets=targets), battery=100, reserve=25) == []
    # 只允许一个架次时不拆分，交给安全员拒绝
    assert len(split_sorties(site, {**plan, "max_sorties": 1}, battery=100, reserve=25)) == 1
