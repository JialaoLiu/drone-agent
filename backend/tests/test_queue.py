"""任务排队与网页设置。"""
from droneagent import config, settings, simctl, store
from droneagent.agents import mission, queue, runtime


def test_queued_mission_starts_when_idle_on_same_scene(monkeypatch):
    started = []
    monkeypatch.setattr(mission, "start", lambda mid, task, scene, auto: started.append((mid, auto)))
    monkeypatch.setattr(simctl, "status", lambda: {"scene": "solar_7", "px4": True, "camera": True})
    runtime.active_mission = None
    busy = store.create_mission("第一个", "solar_7")
    store.update_mission(busy, status="running")
    nxt = store.create_mission("第二个", "solar_7")
    assert queue.busy() and queue.enqueue(nxt, True) == 1
    assert queue.dispatch_next() is None                       # 还有任务在执行
    store.update_mission(busy, status="done")
    assert queue.dispatch_next() == nxt and started == [(nxt, True)] and store.mission_status(nxt) == "planning"


def test_queued_mission_fails_if_scene_changed(monkeypatch):
    monkeypatch.setattr(mission, "start", lambda *a: None)
    monkeypatch.setattr(simctl, "status", lambda: {"scene": "solar_9", "px4": True, "camera": True})
    runtime.active_mission = None
    mid = store.create_mission("排队", "solar_7")
    queue.enqueue(mid, False)
    assert queue.dispatch_next() is None and store.mission_status(mid) == "failed"


def test_settings_apply_and_persist(monkeypatch):
    monkeypatch.setattr(config, "VISION_THINKING_MODE", "off")
    out = settings.update({"vision_thinking": "screen", "segment_screen": False})
    assert out["vision_thinking"] == "screen" and config.VISION_THINKING and not config.VISION_THINKING_VERIFY and not config.SEGMENT_SCREEN
    assert settings.load_saved()["segment_screen"] is False
    settings.update({"vision_thinking": "off", "segment_screen": True})
