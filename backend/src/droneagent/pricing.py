"""按 pricing.yaml 计算每次模型调用的费用：区分缓存命中 / 未命中输入、输出，以及高峰 / 空闲时段。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import yaml


@lru_cache
def table() -> dict:
    return yaml.safe_load(Path(__file__).with_name("pricing.yaml").read_text(encoding="utf-8"))


def period(at: datetime) -> str:
    """返回 peak 或 idle。at 为任意时区的时间，按北京时间判断。"""
    t = table()
    local = at.astimezone(timezone(timedelta(hours=t["timezone_offset_hours"])))
    if local.weekday() >= 5 or local.date().isoformat() in set(t.get("holidays") or []):
        return "idle"
    return "peak" if any(start <= local.hour < end for start, end in t["peak_hours"]) else "idle"


def model_prices(model: str) -> dict | None:
    for name, entry in table()["models"].items():
        if model == name or model in entry.get("aliases", []):
            return entry
    return None


def cost(model: str, cache_hit: int, cache_miss: int, output: int, at: datetime) -> tuple[float | None, str]:
    """返回 (费用元, 时段)。模型不在价格表里时费用为 None。"""
    slot = period(at)
    prices = model_prices(model)
    if prices is None:
        return None, slot
    p = prices[slot]
    total = cache_hit * p["input_cache_hit"] + cache_miss * p["input_cache_miss"] + output * p["output"]
    return round(total / 1_000_000, 8), slot
