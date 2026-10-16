"""测试共用：每个测试使用临时的数据目录与数据库，场景在临时目录中生成，不依赖仿真和模型。"""
import pytest

from droneagent import config, store, weather
from droneagent.sim import scene


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "WORLDS_DIR", tmp_path / "worlds")
    monkeypatch.setattr(config, "PHOTOS_DIR", tmp_path / "photos")
    monkeypatch.setattr(config, "APP_DB", tmp_path / "app.db")
    store._ready.clear()
    config.ensure_dirs()
    # 测试不访问网络：天气固定为无风晴天
    monkeypatch.setattr(weather, "fetch", lambda lat, lon, timeout=5.0: {"wind_speed_10m": 2.0, "wind_gusts_10m": 3.0,
                                                                          "precipitation": 0.0, "weather_code": 1, "visibility": 30000.0})


@pytest.fixture
def site(tmp_path):
    scene.generate(7, "solar_7")
    return scene.load_site("solar_7")
