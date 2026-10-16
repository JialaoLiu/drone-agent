"""巡检员的看图能力：把照片中的每块光伏板投影、裁切、校正为正视图，拼成带编号的拼图交给视觉模型。

为什么不直接把整张照片交给模型：视觉模型每张图只有约 384 Token 的预算，整张照片会被压缩，
小目标看不清，模型也难以把缺陷对应到具体编号。按编号裁切后，每块板的归属是确定的。
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from . import config
from .llm import Usage, chat_json
from .sim.camera import Frame

TILE_W, TILE_H = 150, 255               # 拼图中单块板的尺寸（宽:长 = 1:1.7）
CLOSE_W, CLOSE_H = 360, 612
MIN_TILE_PIXELS = 20                    # 照片中板宽小于这个像素数就不判断，交给补拍
DEFECT_TYPES = {"crack": "裂纹", "soiling": "污损", "broken": "玻璃破碎", "missing": "缺失"}

GUIDE = """你在检查光伏板。每块板已从无人机照片中裁切并校正为正视图。
正常的板：深蓝色电池片排成整齐的网格，浅色细栅线，四周银色边框，颜色均匀。
缺陷类型：
- crack 裂纹：一条或几条细长、弯折的亮白色线条
- soiling 污损：白色或褐色的斑块、污渍（鸟粪、泥灰）；不透明的斑点即使很小也算污损（处理优先级最低，但要报出来）；整片均匀覆盖的灰尘（板面明显发灰、发白，电池片颜色变浅、网格变得不清楚）也算污损
- broken 玻璃破碎：从一点向外放射的碎纹，中心常有亮斑
- missing 缺失：边框内没有电池片，能看到地面（绿色、褐色）或支架
以下不是缺陷，不要报：
- 反光：大片、边缘非常模糊的半透明亮区，透过它仍能清楚看到下面的电池片网格和栅线，电池片本身的深蓝色不变；整块板颜色均匀变浅、发灰、网格变模糊的是灰尘，不是反光
- 阴影：大片、边缘模糊的暗区（树枝、云的影子），下面的电池片网格仍然完整可见
- 边框、栅线、电池片之间轻微的颜色差异
- 遮挡：后排的板从斜上方拍摄时，下边缘可能被前排的板挡住，出现一条贴着边缘的深色或黑色条带；只要条带以外的电池片完整，就不是缺陷
“缺失”指整块板都看不到电池片（框内大部分是地面或支架），只缺一条边不算。
污损与反光的区别：污损是不透明的斑点，边缘相对清楚，盖住了下面的电池片纹理；反光是半透明的，纹理仍在。"""


@dataclass
class Tile:
    panel_id: str
    image: Image.Image
    pixel_width: float        # 板在原始照片中的像素宽度（越大越清楚）


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def rectify(frame: Frame, panel: dict, size: tuple[int, int] = (TILE_W, TILE_H)) -> Tile | None:
    """把一块板从照片中裁出并校正为正视图；不在画面内或太小时返回 None。"""
    pts = [frame.pose.project(tuple(c)) for c in panel["corners"]]
    if any(p is None for p in pts):
        return None
    w, h = frame.pose.width, frame.pose.height
    if any(not (2 <= u <= w - 2 and 2 <= v <= h - 2) for u, v in pts):
        return None
    width = (abs(pts[1][0] - pts[0][0]) + abs(pts[2][0] - pts[3][0])) / 2
    if width < MIN_TILE_PIXELS:
        return None
    # 角点顺序：0 左下（西、坡底）、1 右下、2 右上、3 左上（坡顶）。PIL QUAD 需要：左上、左下、右下、右上
    quad = (*pts[3], *pts[0], *pts[1], *pts[2])
    img = frame.image.transform(size, Image.Transform.QUAD, quad, Image.Resampling.BICUBIC)
    return Tile(panel["id"], img, width)


def contact_sheet(tiles: list[Tile], cols: int = 5) -> bytes:
    """拼图：每块板上方印编号。"""
    rows = (len(tiles) + cols - 1) // cols
    label_h, pad = 30, 8
    sheet = Image.new("RGB", (cols * (TILE_W + pad) + pad, rows * (TILE_H + label_h + pad) + pad), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    font = _font(22)
    for i, t in enumerate(tiles):
        x = pad + (i % cols) * (TILE_W + pad)
        y = pad + (i // cols) * (TILE_H + label_h + pad)
        d.text((x + 2, y + 2), t.panel_id, fill=(200, 0, 0), font=font)
        sheet.paste(t.image.resize((TILE_W, TILE_H)), (x, y + label_h))
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def screen(tiles: list[Tile], provider: str | None = None) -> tuple[list[dict], Usage]:
    """初筛：一张拼图里的多块板，返回疑似缺陷与看不清的板。"""
    ids = [t.panel_id for t in tiles]
    user = (f"拼图中共 {len(ids)} 块板，编号：{', '.join(ids)}。逐块检查，"
            "只列出有缺陷（defect）或看不清无法判断（unsure）的板，正常的不要列出。"
            '只输出 JSON：{"panels":[{"id":"编号","status":"defect|unsure","type":"crack|soiling|broken|missing|null",'
            '"confidence":0到1之间的数,"evidence":"一句话说明看到了什么"}]}')
    data, usage = chat_json(GUIDE, user, [contact_sheet(tiles)], model=_model(provider), provider=_provider(provider),
                            thinking=config.VISION_THINKING, max_tokens=1500)
    panels = data.get("panels") if isinstance(data, dict) else None
    if not isinstance(panels, list):
        # 输出结构不对：整张拼图的板都按“看不清”处理，交给复核，不能当成“没有可疑板”（深度审查 H06）
        return [{"id": pid, "status": "unsure", "type": None, "confidence": 0.0, "evidence": "初筛输出格式错误，未能判断"}
                for pid in ids], usage
    out = []
    for p in panels:
        if not isinstance(p, dict) or p.get("id") not in ids:
            continue
        status = _status(p)            # 与复核共用规范化：无法识别的状态判为 unsure，不会被丢弃
        if status == "ok":
            continue
        kind = str(p.get("type") or "").strip().lower()
        out.append({"id": p["id"], "status": status, "type": kind if kind in DEFECT_TYPES else None,
                    "confidence": _confidence(p), "evidence": str(p.get("evidence") or "")[:200]})
    return out, usage


def verify(tile: Tile, provider: str | None = None, attempt: int = 0) -> tuple[dict, Usage]:
    """复核：一块板的高清近照，给出最终判断。"""
    buf = io.BytesIO()
    tile.image.resize((CLOSE_W, CLOSE_H)).save(buf, "JPEG", quality=92)
    user = (f"这是光伏板 {tile.panel_id} 的高清近照（已校正为正视图）。判断它是否有缺陷。"
            '只输出 JSON：{"status":"ok|defect","type":"crack|soiling|broken|missing|null","confidence":0到1之间的数,'
            '"evidence":"一句话说明看到了什么、在板的哪个位置"}')
    data, usage = chat_json(GUIDE, user, [buf.getvalue()], model=_model(provider), provider=_provider(provider),
                            thinking=config.VISION_THINKING_VERIFY, max_tokens=400, attempt=attempt)
    status = _status(data)
    kind = str(data.get("type") or "").strip().lower()
    return {"id": tile.panel_id, "status": status,
            "type": kind if status == "defect" and kind in DEFECT_TYPES else None,
            "confidence": _confidence(data), "evidence": str(data.get("evidence") or "")[:200], "raw": data}, usage


def verify_photo(image: Image.Image, provider: str | None = None, attempt: int = 0) -> tuple[dict, Usage]:
    """真实现场照片的复核（真实图像评测用）：系统说明、输出格式、结论规范化都与 verify 相同，
    只是保留照片原始比例（最长边 768 像素），并说明这是没有经过校正的现场照片。"""
    img = image.convert("RGB")
    img.thumbnail((768, 768))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    user = ("这是一张光伏板的现场照片（没有经过裁切校正，可能有多块板、拍摄角度不正）。判断画面中的光伏板是否有缺陷。"
            '只输出 JSON：{"status":"ok|defect","type":"crack|soiling|broken|missing|null","confidence":0到1之间的数,'
            '"evidence":"一句话说明看到了什么、在哪个位置"}')
    data, usage = chat_json(GUIDE, user, [buf.getvalue()], model=_model(provider), provider=_provider(provider),
                            thinking=config.VISION_THINKING_VERIFY, max_tokens=400, attempt=attempt)
    status = _status(data)
    kind = str(data.get("type") or "").strip().lower()
    return {"status": status, "type": kind if status == "defect" and kind in DEFECT_TYPES else None,
            "confidence": _confidence(data), "evidence": str(data.get("evidence") or "")[:200]}, usage


def _status(data: dict) -> str:
    """规范化复核结论。无法识别的输出返回 unsure，绝不默认为正常（那样会把真缺陷悄悄排除）。"""
    raw = str(data.get("status") or "").strip().lower()
    if raw in ("ok", "normal", "正常", "无缺陷"):
        return "ok"
    if raw in ("defect", "defective", "缺陷", "有缺陷"):
        return "defect"
    kind = str(data.get("type") or "").strip().lower()
    return "defect" if kind in DEFECT_TYPES and _confidence(data) >= 0.5 else "unsure"


def _confidence(data: dict) -> float:
    try:
        return max(0.0, min(1.0, float(data.get("confidence") or 0)))
    except (TypeError, ValueError):
        return 0.0


def _provider(provider: str | None) -> str:
    return provider or config.VISION_PROVIDER


def _model(provider: str | None) -> str:
    return config.OPENAI_VISION_MODEL if _provider(provider) == "openai" else config.VISION_MODEL
