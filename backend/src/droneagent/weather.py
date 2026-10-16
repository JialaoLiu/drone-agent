"""起飞天气评估（安全员的确定性规则）。数据来自 Open-Meteo（免费、不需要 Key）。

阈值参考常见行业级多旋翼的抗风能力（约 12 米/秒）并留余量；模型不参与判断，只在报告中引用结论。
仿真中风场与真实天气无关，所以默认只提示；真机部署时设置 WEATHER_ENFORCE=1，不通过即拒绝起飞。
"""
from __future__ import annotations

import time

import httpx

from . import config

URL = "https://api.open-meteo.com/v1/forecast"
WIND_MAX, WIND_WARN = 10.0, 7.0          # 10 米高度平均风速（米/秒）
GUST_MAX, GUST_WARN = 12.0, 9.0          # 阵风（米/秒）
RAIN_MAX = 0.2                           # 最近 15 分钟降水（毫米）
VIS_MIN, VIS_WARN = 1500.0, 5000.0       # 能见度（米）：目视飞行需要看清飞机
THUNDER = {95, 96, 99}                   # WMO 天气代码：雷暴
CACHE_SECONDS = 600
_cache: dict[tuple[float, float], tuple[float, dict]] = {}


def fetch(lat: float, lon: float, timeout: float = 5.0) -> dict:
    key = (round(lat, 2), round(lon, 2))
    if key in _cache and time.time() - _cache[key][0] < CACHE_SECONDS:
        return _cache[key][1]
    r = httpx.get(URL, params={"latitude": lat, "longitude": lon, "wind_speed_unit": "ms", "timezone": "auto",
                               "current": "wind_speed_10m,wind_gusts_10m,precipitation,weather_code,visibility,temperature_2m"},
                  timeout=timeout)
    r.raise_for_status()
    current = r.json()["current"]
    _cache[key] = (time.time(), current)
    return current


def assess(c: dict) -> tuple[bool | None, str]:
    """返回（结论，说明）。结论 True 通过、False 不宜起飞、None 有提示。"""
    wind, gust = c.get("wind_speed_10m"), c.get("wind_gusts_10m")
    rain, code, vis = c.get("precipitation") or 0.0, c.get("weather_code"), c.get("visibility")
    stop, warn = [], []
    if code in THUNDER:
        stop.append("有雷暴")
    if wind is not None and wind > WIND_MAX:
        stop.append(f"平均风速 {wind:.1f} 米/秒（上限 {WIND_MAX:.0f}）")
    elif wind is not None and wind > WIND_WARN:
        warn.append(f"平均风速 {wind:.1f} 米/秒，偏大")
    if gust is not None and gust > GUST_MAX:
        stop.append(f"阵风 {gust:.1f} 米/秒（上限 {GUST_MAX:.0f}）")
    elif gust is not None and gust > GUST_WARN:
        warn.append(f"阵风 {gust:.1f} 米/秒，偏大")
    if rain > RAIN_MAX:
        stop.append(f"正在降水（{rain:.1f} 毫米）")
    if vis is not None and vis < VIS_MIN:
        stop.append(f"能见度 {vis:.0f} 米（下限 {VIS_MIN:.0f}）")
    elif vis is not None and vis < VIS_WARN:
        warn.append(f"能见度 {vis:.0f} 米，偏低")
    summary = f"风 {wind if wind is not None else '?'} 米/秒、阵风 {gust if gust is not None else '?'} 米/秒"
    if stop:
        return False, "不宜起飞：" + "；".join(stop)
    if warn:
        return None, "；".join(warn)
    return True, summary + "，适合飞行"


def item(site: dict | None) -> dict:
    """飞行前检查中的“天气”一项。"""
    enforce = config.env("WEATHER_ENFORCE", "0") == "1"
    origin = (site or {}).get("origin")
    if not origin:
        return {"key": "weather", "label": "天气", "ok": None, "detail": "场地没有经纬度，跳过"}
    try:
        ok, detail = assess(fetch(origin["lat"], origin["lon"]))
    except (httpx.HTTPError, KeyError, ValueError) as error:
        return {"key": "weather", "label": "天气", "ok": False if enforce else None,
                "detail": f"天气数据获取失败（{type(error).__name__}），请人工确认现场天气"}
    if ok is False and not enforce:
        return {"key": "weather", "label": "天气", "ok": None, "detail": detail + "（仿真中只提示；真机部署设 WEATHER_ENFORCE=1 时拒绝起飞）"}
    return {"key": "weather", "label": "天气", "ok": ok, "detail": detail}
