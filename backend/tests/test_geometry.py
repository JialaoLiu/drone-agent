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
