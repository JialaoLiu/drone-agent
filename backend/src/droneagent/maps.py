"""真实场地：地点查坐标（百度地图）、坐标系换算、卫星影像（Mapbox）。

- 百度地点检索返回国测局坐标（GCJ-02，请求时指定），本模块换算为飞控使用的 WGS-84；换算为近似公式，误差约 1–2 米；
- 百度服务端接口使用 SN 签名（BAIDU_MAP_AK + BAIDU_MAP_SK）；
- 卫星影像来自 Mapbox Static Images（MAPBOX_TOKEN），用作仿真地面贴图和网页底图；百度静态图没有卫星图选项。
密钥只从 backend/.env 读取，不写入日志或场景文件。
"""
from __future__ import annotations

import hashlib
import math
from urllib.parse import quote, quote_plus

import httpx

from . import config

BAIDU = "https://api.map.baidu.com"
MAPBOX_STATIC = "https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static"
EARTH = 40075016.686            # 赤道周长（米）


class MapError(RuntimeError):
    pass


# ---------- 坐标换算（GCJ-02 ↔ WGS-84）----------

_A, _EE = 6378245.0, 0.00669342162296594323


def _out_of_china(lat: float, lon: float) -> bool:
    return not (73.66 < lon < 135.05 and 3.86 < lat < 53.55)


def _delta(lat: float, lon: float) -> tuple[float, float]:
    x, y = lon - 105.0, lat - 35.0
    dlat = (-100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
            + (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
            + (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
            + (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0)
    dlon = (300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
            + (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
            + (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
            + (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0)
    rad = lat / 180.0 * math.pi
    magic = 1 - _EE * math.sin(rad) ** 2
    sq = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sq) * math.pi)
    dlon = (dlon * 180.0) / (_A / sq * math.cos(rad) * math.pi)
    return dlat, dlon


def wgs84_to_gcj02(lat: float, lon: float) -> tuple[float, float]:
    if _out_of_china(lat, lon):
        return lat, lon
    dlat, dlon = _delta(lat, lon)
    return lat + dlat, lon + dlon


def gcj02_to_wgs84(lat: float, lon: float) -> tuple[float, float]:
    """迭代反解，误差在厘米级（相对正算公式）；正算公式本身与真实偏移的差异约 1–2 米。"""
    if _out_of_china(lat, lon):
        return lat, lon
    wlat, wlon = lat, lon
    for _ in range(10):
        glat, glon = wgs84_to_gcj02(wlat, wlon)
        wlat, wlon = wlat - (glat - lat), wlon - (glon - lon)
    return wlat, wlon


_X_PI = math.pi * 3000.0 / 180.0


def gcj02_to_bd09(lat: float, lon: float) -> tuple[float, float]:
    z = math.sqrt(lon * lon + lat * lat) + 0.00002 * math.sin(lat * _X_PI)
    theta = math.atan2(lat, lon) + 0.000003 * math.cos(lon * _X_PI)
    return z * math.sin(theta) + 0.006, z * math.cos(theta) + 0.0065


def bd09_to_gcj02(lat: float, lon: float) -> tuple[float, float]:
    x, y = lon - 0.0065, lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * _X_PI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * _X_PI)
    return z * math.sin(theta), z * math.cos(theta)


def wgs84_to_bd09(lat: float, lon: float) -> tuple[float, float]:
    """WGS-84 → GCJ-02 → BD-09。境外不做偏移（GCJ-02 只在中国境内生效）。"""
    if _out_of_china(lat, lon):
        return lat, lon
    return gcj02_to_bd09(*wgs84_to_gcj02(lat, lon))


def in_china(lat: float, lon: float) -> bool:
    return not _out_of_china(lat, lon)


def baidu_frame(origin: dict, half_extent: float = 150.0) -> dict:
    """场地本地坐标（米，x 东、y 北）→ 百度 BD-09 经纬度的线性换算参数。

    场地只有几百米，这个范围内 WGS-84 到 BD-09 的偏移变化很小（±150 米内与逐点精确换算相差约 0.1 米，
    远小于换算公式本身约 1–2 米的误差），所以用精确公式换算原点和东、北各一个点，
    得到“每米对应多少度”，网页上所有要素（边界、阵列、禁飞区、树、航线、绕行点、飞机位置）都用同一组参数换算，
    不会出现有的转了、有的没转。max_error_m 是这组参数在 ±half_extent 范围内相对逐点精确换算的最大误差。"""
    from .geo import Origin

    o = Origin(origin["lat"], origin["lon"], origin.get("alt", 0.0))

    def exact(x: float, y: float) -> tuple[float, float]:
        lat, lon, _ = o.to_global(x, y)
        return wgs84_to_bd09(lat, lon)

    b0 = exact(0, 0)
    be, bn = exact(100, 0), exact(0, 100)
    # 线性部分：东向与北向各自对经、纬度的影响（偏移随位置有极小的变化，也包含进来）
    m = {"lat_per_e": (be[0] - b0[0]) / 100, "lon_per_e": (be[1] - b0[1]) / 100,
         "lat_per_n": (bn[0] - b0[0]) / 100, "lon_per_n": (bn[1] - b0[1]) / 100}

    def linear(x: float, y: float) -> tuple[float, float]:
        return b0[0] + m["lat_per_e"] * x + m["lat_per_n"] * y, b0[1] + m["lon_per_e"] * x + m["lon_per_n"] * y

    err = 0.0
    for x in (-half_extent, 0, half_extent):
        for y in (-half_extent, 0, half_extent):
            (la, lo), (lb, lb2) = exact(x, y), linear(x, y)
            err = max(err, math.hypot((la - lb) * 111320, (lo - lb2) * 111320 * math.cos(math.radians(la))))
    return {"origin_bd09": [b0[0], b0[1]], **m, "max_error_m": round(err, 4), "in_china": in_china(origin["lat"], origin["lon"])}


# ---------- 百度地点检索 ----------

def _baidu_get(path: str, params: dict) -> dict:
    ak, sk = config.env("BAIDU_MAP_AK"), config.env("BAIDU_MAP_SK")
    if not ak:
        raise MapError("没有配置 BAIDU_MAP_AK（backend/.env）")
    params = {**params, "ak": ak}
    if sk:   # SN 校验（百度官方算法）：用未编码的“路径?参数”串，按规定字符集编码后接上 SK，再整体 quote_plus 取 MD5
        query = path + "?" + "&".join(f"{k}={v}" for k, v in params.items())
        raw = quote(query, safe="/:=&?#+!$,;'@()*[]") + sk
        params["sn"] = hashlib.md5(quote_plus(raw).encode()).hexdigest()
    try:
        r = httpx.get(BAIDU + path, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as error:
        raise MapError(f"百度地图接口请求失败：{type(error).__name__}") from error
    if data.get("status") != 0:
        raise MapError(f"百度地图接口返回错误：{data.get('status')} {data.get('message', '')}")
    return data


def search_place(query: str, region: str = "全国") -> list[dict]:
    """地名检索，返回候选地点（WGS-84 坐标）。"""
    data = _baidu_get("/place/v2/search", {"query": query, "region": region, "output": "json", "ret_coordtype": "gcj02ll",
                                           "page_size": 5, "scope": 1})
    out = []
    for item in data.get("results", []):
        loc = item.get("location")
        if not loc:
            continue
        lat, lon = gcj02_to_wgs84(loc["lat"], loc["lng"])
        out.append({"name": item.get("name", ""), "address": item.get("address", ""), "city": item.get("city", ""),
                    "lat": round(lat, 7), "lon": round(lon, 7), "source": "百度地点检索（GCJ-02 换算为 WGS-84，误差约 1–2 米）"})
    return out


# ---------- Mapbox 卫星影像 ----------

def meters_per_pixel(lat: float, zoom: float) -> float:
    """Mapbox 静态图（512 像素瓦片）在给定纬度与缩放级别下每个像素对应的米数。"""
    return EARTH * math.cos(math.radians(lat)) / (512 * 2 ** zoom)


def zoom_for_extent(lat: float, extent_m: float, pixels: int) -> float:
    """让 pixels 个像素正好覆盖 extent_m 米的缩放级别。"""
    return math.log2(EARTH * math.cos(math.radians(lat)) / (512 * extent_m / pixels))


def satellite_image(lat: float, lon: float, extent_m: float, pixels: int = 1024) -> tuple[bytes, float]:
    """以 (lat, lon) 为中心、边长 extent_m 米的正方形卫星影像。返回（PNG 字节，每像素米数）。
    请求 @2x 高清图：pixels 为最终像素数，接口参数取一半。"""
    token = config.env("MAPBOX_TOKEN")
    if not token:
        raise MapError("没有配置 MAPBOX_TOKEN（backend/.env）")
    half = max(1, min(1280, pixels // 2))
    zoom = zoom_for_extent(lat, extent_m, half)
    if not 0 <= zoom <= 22:
        raise MapError(f"场地尺寸 {extent_m:.0f} 米超出影像缩放范围")
    url = f"{MAPBOX_STATIC}/{lon:.7f},{lat:.7f},{zoom:.3f},0/{half}x{half}@2x"
    try:
        r = httpx.get(url, params={"access_token": token, "attribution": "false", "logo": "false"}, timeout=20)
    except httpx.HTTPError as error:
        raise MapError(f"Mapbox 请求失败：{type(error).__name__}") from error
    if r.status_code != 200:
        raise MapError(f"Mapbox 返回 {r.status_code}（Token 是否有效、是否有 Static Images 权限？）")
    return r.content, meters_per_pixel(lat, zoom) / 2
