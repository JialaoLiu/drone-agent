"""测试共用：每个测试使用临时的数据目录与数据库，场景在临时目录中生成，不依赖仿真和模型。"""
import pytest

from droneagent import config, store
from droneagent.sim import scene


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "WORLDS_DIR", tmp_path / "worlds")
    monkeypatch.setattr(config, "PHOTOS_DIR", tmp_path / "photos")
    monkeypatch.setattr(config, "APP_DB", tmp_path / "app.db")
    store._ready.clear()
    config.ensure_dirs()


@pytest.fixture
def site(tmp_path):
    scene.generate(7, "solar_7")
    return scene.load_site("solar_7")
