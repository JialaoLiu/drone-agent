"""救援搜索的“看”：程序找候选 → 模型判断 → 程序定位。

1. 候选：俯视照片里颜色与周围明显不同的小块（人和人的影子都很显眼）。用大窗口中值滤波估计背景，差异大且面积合适的连通块即候选；
   比逐块切图交给模型省得多（一张照片通常几个候选，而不是十几块切图）。
2. 判断：候选裁成小图拼成一张，交给视觉模型：是不是人、上衣与裤子颜色、大人还是小孩、在不在水里、与描述是否相符。
3. 定位：候选中心像素沿相机光线投到地面（z = 0），坐标由程序计算，模型不输出坐标。
4. 确认：用变焦相机对准疑似目标再判断一次；变焦画面里再找一次人，得到更准的位置。
"""
from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from .. import config
from ..llm import Usage, chat_json
from ..sim.camera import Frame
from ..vision import _confidence, _font, _model, _provider

GUIDE = """你是搜救无人机的观察员，看的是无人机拍的俯视或斜视照片（仿真画面）。
- 人从上往下看：头是一个小圆点（头发多为黑色），肩膀和手臂是上衣颜色，常伴有人形的影子；站立的人看起来很小。
- 落水的人：水面上只露出头和肩膀，周围是水。衣服在水下，颜色看不清或偏灰，不能因为颜色对不上就判 no；水里有人时 match 至少为 partial。
- 地上的衣服、背包、垃圾袋没有头和影子，不是人。树冠、灌木、石头、长椅也不是人。
- 判断衣着颜色时说最接近的常见颜色（红、橙、黄、绿、蓝、紫、粉、白、灰、黑）。
- 看不清就说看不清（unsure），不要猜。只输出要求的 JSON。"""


# ---------- 候选与定位 ----------

def ground_point(frame: Frame, u: float, v: float) -> tuple[float, float] | None:
    """像素 (u, v) 沿相机光线与地面（z = 0）的交点，本地坐标（米）。"""
    pose = frame.pose
    f = pose.width / (2 * math.tan(pose.hfov / 2))
    d_cam = (1.0, (pose.width / 2 - u) / f, (pose.height / 2 - v) / f)          # 相机坐标：x 前、y 左、z 上
    r = pose.rotation
    d = [sum(r[i][k] * d_cam[k] for k in range(3)) for i in range(3)]
    if d[2] >= -1e-6:
        return None
    t = -pose.position[2] / d[2]
    return pose.position[0] + t * d[0], pose.position[1] + t * d[1]


def metres_per_pixel(frame: Frame) -> float:
    return frame.pose.ground_footprint()


def candidates(frame: Frame, max_count: int = 24) -> list[dict]:
    """颜色异常的小块。返回 [{u, v, area_m2, x, y}]，按显著程度排序。"""
    img = np.asarray(frame.image.convert("RGB"), dtype=np.float32)
    small = img[::2, ::2]
    bg = np.stack([ndimage.median_filter(small[..., c], size=25) for c in range(3)], axis=-1)
    diff = np.abs(small - bg).sum(axis=-1)
    thr = max(60.0, float(diff.mean() + 4 * diff.std()))
    mask = ndimage.binary_closing(diff > thr, iterations=2)
    labels, n = ndimage.label(mask)
    if not n:
        return []
    mpp = metres_per_pixel(frame) * 2           # 降采样后每像素的米数
    out = []
    for idx, sl in enumerate(ndimage.find_objects(labels), start=1):
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        area = float((labels[sl] == idx).sum()) * mpp * mpp
        if not (0.04 <= area <= 3.0) or max(h, w) * mpp > 3.2:      # 人（含影子）约 0.1–2 平方米，最大边不超过 3 米
            continue
        score = float(diff[sl][labels[sl] == idx].mean())
        cy, cx = ndimage.center_of_mass(labels[sl] == idx)
        u, v = (sl[1].start + cx) * 2, (sl[0].start + cy) * 2
        g = ground_point(frame, u, v)
        if g:
            out.append({"u": round(float(u), 1), "v": round(float(v), 1), "area_m2": round(area, 2), "x": round(float(g[0]), 2),
                        "y": round(float(g[1]), 2), "score": score})
    out.sort(key=lambda c: -c["score"])
    # 相距不到 1.2 米的候选合并（人和影子常被分成两块）
    merged: list[dict] = []
    for c in out:
        if all(math.hypot(c["x"] - m["x"], c["y"] - m["y"]) > 1.2 for m in merged):
            merged.append(c)
    return merged[:max_count]


def crop(frame: Frame, c: dict, metres: float = 3.0, size: int = 160) -> Image.Image:
    """以候选为中心、边长约 metres 米的小图，放大到 size 像素。"""
    half = max(16, int(metres / metres_per_pixel(frame) / 2))
    u, v = int(c["u"]), int(c["v"])
    box = (max(0, u - half), max(0, v - half), min(frame.image.width, u + half), min(frame.image.height, v + half))
    return frame.image.crop(box).resize((size, size), Image.LANCZOS)


def _sheet(tiles: list[tuple[str, Image.Image]], cols: int = 4) -> bytes:
    size, label_h, pad = 160, 26, 6
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (size + pad) + pad, rows * (size + label_h + pad) + pad), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    font = _font(20)
    for i, (cid, img) in enumerate(tiles):
        x, y = pad + (i % cols) * (size + pad), pad + (i // cols) * (size + label_h + pad)
        d.text((x + 2, y + 2), cid, fill=(200, 0, 0), font=font)
        sheet.paste(img, (x, y + label_h))
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=92)
    return buf.getvalue()


MATCH = ("yes", "partial", "no", "unsure")


def _norm(v, allowed, default):
    s = str(v or "").strip().lower()
    return s if s in allowed else default


def screen(frame: Frame, cands: list[dict], description: str, provider: str | None = None) -> tuple[list[dict], Usage | None]:
    """候选小图拼成一张交给模型。返回每个候选的判断（与 cands 一一对应，加上 person / match / top / bottom / child / in_water / confidence）。"""
    if not cands:
        return [], None
    tiles = [(f"C{i + 1}", crop(frame, c)) for i, c in enumerate(cands)]
    user = (f"要找的人：{description}。拼图里有 {len(tiles)} 张小图（编号 {', '.join(t[0] for t in tiles)}），每张以一个可疑位置为中心、边长约 3 米。"
            "逐张判断是否有人，以及这个人是否就是要找的人（衣着、大人或小孩都要对上才算 yes；只对上一部分算 partial）。"
            '只输出 JSON：{"items":[{"id":"C1","person":"yes|no|unsure","top":"颜色或null","bottom":"颜色或null",'
            '"child":"yes|no|unsure","in_water":"yes|no","match":"yes|partial|no|unsure","confidence":0到1之间的数,"evidence":"一句话"}]}')
    data, usage = chat_json(GUIDE, user, [_sheet(tiles)], model=_model(provider), provider=_provider(provider),
                            thinking=config.VISION_THINKING, max_tokens=1500)
    items = {str(it.get("id")): it for it in (data.get("items") or []) if isinstance(it, dict)} if isinstance(data, dict) else {}
    out = []
    for i, c in enumerate(cands):
        it = items.get(f"C{i + 1}", {})
        person = _norm(it.get("person"), ("yes", "no", "unsure"), "unsure")       # 认不出的一律“不确定”，不当成没有人
        out.append({**c, "person": person, "match": _norm(it.get("match"), MATCH, "unsure") if person != "no" else "no",
                    "top": it.get("top"), "bottom": it.get("bottom"), "child": _norm(it.get("child"), ("yes", "no", "unsure"), "unsure"),
                    "in_water": _norm(it.get("in_water"), ("yes", "no"), "no"), "confidence": _confidence(it),
                    "evidence": str(it.get("evidence") or "")[:160]})
    return out, usage


def verify(frame: Frame, description: str, provider: str | None = None) -> tuple[dict, Usage]:
    """变焦近照确认：画面中央附近有没有人、是否就是要找的人。"""
    img = frame.image.convert("RGB")
    img.thumbnail((960, 960))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    user = (f"这是变焦相机对准可疑位置拍的近照。要找的人：{description}。画面里有没有人？如果有，是不是要找的人？"
            '只输出 JSON：{"person":"yes|no|unsure","match":"yes|partial|no|unsure","top":"颜色或null","bottom":"颜色或null",'
            '"child":"yes|no|unsure","in_water":"yes|no","confidence":0到1之间的数,"evidence":"一句话说明看到了什么"}')
    data, usage = chat_json(GUIDE, user, [buf.getvalue()], model=_model(provider), provider=_provider(provider),
                            thinking=config.VISION_THINKING_VERIFY, max_tokens=400)
    data = data if isinstance(data, dict) else {}
    person = _norm(data.get("person"), ("yes", "no", "unsure"), "unsure")
    return {"person": person, "match": _norm(data.get("match"), MATCH, "unsure") if person != "no" else "no",
            "top": data.get("top"), "bottom": data.get("bottom"), "child": _norm(data.get("child"), ("yes", "no", "unsure"), "unsure"),
            "in_water": _norm(data.get("in_water"), ("yes", "no"), "no"), "confidence": _confidence(data),
            "evidence": str(data.get("evidence") or "")[:200]}, usage


def locate_in_zoom(frame: Frame) -> tuple[float, float] | None:
    """变焦近照里离画面中心最近的候选（人），投到地面得到位置；没有时用画面中心。"""
    cands = candidates(frame, max_count=8)
    w, h = frame.pose.width, frame.pose.height
    if cands:
        c = min(cands, key=lambda c: math.hypot(c["u"] - w / 2, c["v"] - h / 2))
        return c["x"], c["y"]
    g = ground_point(frame, w / 2, h / 2)
    return g
