"""坐标换算、相机投影、拍摄点几何。"""
import math

import pytest

from droneagent import survey
from droneagent.geo import CameraPose, Origin, rpy_to_matrix
from droneagent.sim.camera import look_angles


def test_local_global_round_trip():
    o = Origin(47.397971, 8.546163, 488.0)
    lat, lon, alt = o.to_global(35.0, -12.5, 20.0)
    x, y, z = o.to_local(lat, lon, alt)
    assert abs(x - 35.0) < 1e-6 and abs(y + 12.5) < 1e-6 and abs(z - 20.0) < 1e-9


def test_projection_center_and_behind():
    # 相机在原点、朝东（x 向前），正前方 10 米的点应落在图像中心；身后的点不可见
    cam = CameraPose((0, 0, 0), rpy_to_matrix(0, 0, 0), hfov=1.0, width=1000, height=500)
    u, v = cam.project((10, 0, 0))
    assert abs(u - 500) < 1e-6 and abs(v - 250) < 1e-6
    assert cam.project((-5, 0, 0)) is None
    # 偏左的点在图像左侧，偏上的点在图像上方
    assert cam.project((10, 1, 0))[0] < 500 and cam.project((10, 0, 1))[1] < 250


def test_look_angles():
    yaw, pitch = look_angles((0, 0, 10), (0, 10, 0))
    assert abs(yaw - 0) < 1e-6 and abs(pitch + 45) < 1e-6        # 正北、向下 45°
    yaw, _ = look_angles((0, 0, 0), (5, 0, 0))
    assert abs(yaw - 90) < 1e-6                                   # 正东


def test_overview_faces_panel_normal(site):
    for array in site["arrays"]:
        step = survey.overview_step(array, 20)
        assert math.dist(step.position[:2], array["center"]) < 20
        assert step.position[1] < array["center"][1]                   # 板朝南（偏角不超过 30°），拍摄点在南侧
        assert -70 < step.pitch < -60
        # 机头正对阵列：航向等于阵列朝向（北为 0、顺时针；阵列偏角为逆时针）
        heading = (-array["yaw_deg"]) % 360
        assert abs(((step.yaw - heading + 180) % 360) - 180) < 1


def test_route_time_grows_with_closeups(site):
    a = site["arrays"][0]
    ov = survey.overview_step(a, 20)
    panels = [p for p in site["panels"] if p["array"] == a["id"]]
    base = survey.route_seconds([ov])
    more = survey.route_seconds([ov] + [survey.closeup_step(p, ov.position) for p in panels])
    assert more - base == pytest.approx(len(panels) * survey.SHOT_SECONDS["closeup"], rel=0.05)


def test_visibility_detour_avoids_zone_and_stays_in_fence():
    site = {"geofence": [[-60, -60], [60, -60], [60, 60], [-60, 60]],
            "no_fly_zones": [{"id": "Z1", "label": "测试", "center": [0, 0], "radius": 8}], "obstacles": []}
    a, b = (-40, 0, 20), (40, 0, 20)
    for method in ("grid", "visibility"):
        wps = survey.detour(a, b, site, method)
        route = [a] + wps + [b]
        assert wps and all(survey._clear(p, q, site["no_fly_zones"]) for p, q in zip(route, route[1:]))
        assert all(survey._inside(site["geofence"], p[0], p[1]) for p in wps)
    # 可见性图的路径不长于半个外接多边形周长加两段切线的粗略上界
    vis = [a] + survey.detour_visibility(a, b, site) + [b]
    assert sum(math.dist(p[:2], q[:2]) for p, q in zip(vis, vis[1:])) < 95
    # 直飞不受影响时不插点
    assert survey.detour((-40, 40, 20), (40, 40, 20), site, "visibility") == []


def test_routes_summary_without_detours():
    from droneagent import evaluate
    seed = next(s for s in range(1, 40) if not evaluate.run_routes([s])["rows"])
    assert evaluate.run_routes([seed])["summary"]["length_saving_pct"] is None


def test_visibility_edges_keep_segment_clearance():
    site = {"geofence": [[-60, -60], [60, -60], [60, 60], [-60, 60]],
            "no_fly_zones": [{"id": "Z1", "label": "测试", "center": [0, 0], "radius": 8}], "obstacles": []}
    a, b = (-40, 0, 20), (40, 0, 20)
    route = [a] + survey.detour_visibility(a, b, site) + [b]
    gap = min(math.dist(survey._segment_closest((0, 0), p, q), (0, 0)) - 8 for p, q in zip(route, route[1:]))
    assert gap >= survey.CLEAR - 1e-6


def test_baidu_frame_matches_exact_conversion():
    from droneagent import maps
    from droneagent.geo import Origin
    origin = {"lat": 23.0172979, "lon": 113.232189, "alt": 10.0}          # 佛山文翰湖公园
    f = maps.baidu_frame(origin)
    assert f["in_china"] and f["max_error_m"] < 0.2          # 实测约 0.12 米，远小于换算本身约 1–2 米的误差
    o = Origin(origin["lat"], origin["lon"], 10.0)
    for x, y in ((0, 0), (120, -80), (-150, 150)):
        lat, lon, _ = o.to_global(x, y)
        ex = maps.wgs84_to_bd09(lat, lon)
        li = (f["origin_bd09"][0] + f["lat_per_e"] * x + f["lat_per_n"] * y, f["origin_bd09"][1] + f["lon_per_e"] * x + f["lon_per_n"] * y)
        assert math.dist(ex, li) * 111320 < 0.2
    # 偏移量级：国内 WGS-84 与 BD-09 相差几百米；往返换算误差在厘米级
    b = maps.wgs84_to_bd09(origin["lat"], origin["lon"])
    assert 100 < math.dist(b, (origin["lat"], origin["lon"])) * 111320 < 1500
    g = maps.bd09_to_gcj02(*b)
    w = maps.gcj02_to_wgs84(*g)
    assert math.dist(w, (origin["lat"], origin["lon"])) * 111320 < 0.5
    # 境外不偏移
    assert maps.wgs84_to_bd09(-34.9285, 138.6007) == (-34.9285, 138.6007)
    assert not maps.baidu_frame({"lat": -34.9285, "lon": 138.6007})["in_china"]
