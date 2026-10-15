"""飞行前检查与位置一致性规则（不需要仿真）。"""
import asyncio
import sqlite3

from droneagent import config, health, store


def test_recent_sensor_faults():
    now = 1000.0
    log = [(now - 120, "Accel #0 fail:  TIMEOUT!"),                  # 太久以前，不算
           (now - 30, "Preflight Fail: No connection to the GCS"),   # 与传感器无关
           (now - 20, "Takeoff detected"),
           (now - 10, "Accel #0 fail:  TIMEOUT!"),
           (now - 5, "Baro #0 fail: STALE!")]
    assert health.recent_faults(log, now) == ["Accel #0 fail:  TIMEOUT!", "Baro #0 fail: STALE!"]
    assert health.recent_faults([(now - 1, "Landing detected")], now) == []


def test_drift_thresholds():
    assert health.drift_item((0, 0, 19.7), (0.1, 0.1, 19.5))["ok"] is True
    item = health.drift_item((63.7, 18.4, 18.8), (63.8, 18.4, 16.4))         # 实测：高度估计比真实高 2.4 米
    assert item["ok"] is False and "对不准" in item["detail"]
    assert health.drift_item((0, 0, 10), (3, 0, 10))["ok"] is False
    assert health.drift_item((0, 0, 10), None)["ok"] is None
    # 停在起飞点时：估计高度约 0.05 米，模型原点约 -0.01 米，不应误判
    assert health.frame_drift((0.0, 0.0, 0.05), (0.0, 0.0, -0.01))["ok"] is True


def test_real_time_factor():
    assert health.real_time_item(0.98)["ok"] is True
    assert health.real_time_item(0.7)["ok"] is None          # 偏慢只提示
    assert health.real_time_item(0.4)["ok"] is False
    assert health.real_time_item(None)["ok"] is None


class FakeFlight:
    def __init__(self, sensors_ok=True, faults=()):
        self.sensors_ok = sensors_ok
        self.status_log = [(health.time.time(), t) for t in faults]
        self.state = type("S", (), {"x": 0.0, "y": 0.0, "z": 0.05})()

    async def health(self):
        ok = self.sensors_ok
        return {"gyro": True, "accel": ok, "mag": True, "local_position": ok, "global_position": True, "home": True, "armable": ok}


def test_preflight_blocks_unhealthy(monkeypatch):
    clock = iter([10.0, 10.004])                             # 至少 0.01 秒的真实时间里仿真只走了 0.004 秒：仿真速度不到 40%
    monkeypatch.setattr(health.camera, "sim_state", lambda: {"sim_time": next(clock), "drone": (0.0, 0.0, -0.01)})
    monkeypatch.setattr(health, "load_item", lambda: health._item("load", "电脑负载", True, ""))
    good = asyncio.run(health.preflight(FakeFlight(), sample_seconds=0.01))
    assert not good["ok"] and [i["key"] for i in good["items"] if i["ok"] is False] == ["real_time"]

    clock = iter([10.0, 10.01])
    bad = asyncio.run(health.preflight(FakeFlight(sensors_ok=False, faults=["Accel #0 fail:  TIMEOUT!"]), sample_seconds=0.01))
    failed = {i["key"] for i in bad["items"] if i["ok"] is False}
    assert {"autopilot", "sensors"} <= failed and "重启" in bad["advice"]


def test_findings_reason_column_migrates():
    # 旧版本数据库没有 reason 列，连接时自动补上
    con = sqlite3.connect(config.APP_DB)
    con.execute("CREATE TABLE findings (mission_id TEXT NOT NULL, panel_id TEXT NOT NULL, status TEXT NOT NULL, type TEXT, "
                "confidence REAL, evidence TEXT, screen_photo INTEGER, verify_photo INTEGER, updated_at TEXT NOT NULL, "
                "PRIMARY KEY (mission_id, panel_id))")
    con.commit()
    con.close()
    store.upsert_finding("m1", "A-R1-C01", status="unverified", reason="misaimed")
    assert store.findings("m1")[0]["reason"] == "misaimed"
