"""网页上可以修改的运行设置：保存在数据目录的 settings.json，修改后立即生效（不需要重启）。

只开放与成本、准确率取舍有关、且有评测依据的开关；安全规则不在这里，不能从网页关闭。
"""
from __future__ import annotations

import json

from . import config

FIELDS = {
    "vision_thinking": {"label": "视觉模型推理过程", "options": {"off": "关闭（默认，省 75%–86% 费用，复核后结果不变）",
                                                         "screen": "只在初筛开启", "on": "初筛与复核都开启"}},
    "segment_screen": {"label": "变焦分段拍整列", "help": "开启时检出更高（新场景 73.8% → 96.4%），飞行时间更长、大场地更常分两个架次"},
    "recheck_thinking": {"label": "“再看一次”时开启推理", "help": "按实时流程评测没有收益，默认关闭"},
}


def _path():
    return config.DATA_DIR / "settings.json"


def current() -> dict:
    return {"vision_thinking": config.VISION_THINKING_MODE, "segment_screen": config.SEGMENT_SCREEN,
            "recheck_thinking": config.VISION_THINKING_RECHECK, "fields": FIELDS}


def _apply(values: dict) -> None:
    if "vision_thinking" in values:
        mode = values["vision_thinking"]
        if mode not in FIELDS["vision_thinking"]["options"]:
            raise ValueError(f"未知的推理设置：{mode}")
        config.VISION_THINKING_MODE = mode
        config.VISION_THINKING = mode != "off"
        config.VISION_THINKING_VERIFY = mode == "on"
    if "segment_screen" in values:
        config.SEGMENT_SCREEN = bool(values["segment_screen"])
    if "recheck_thinking" in values:
        config.VISION_THINKING_RECHECK = bool(values["recheck_thinking"])


def update(values: dict) -> dict:
    _apply(values)
    saved = load_saved()
    saved.update(values)
    _path().write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    return current()


def load_saved() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load() -> None:
    """启动时套用保存过的设置（覆盖 .env 中的默认值）。"""
    try:
        _apply(load_saved())
    except ValueError:
        pass
