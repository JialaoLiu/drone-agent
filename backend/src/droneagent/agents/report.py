"""报告员的确定性部分：汇总结果（数字全部由程序统计）并生成 HTML 报告。

报告中每条缺陷附整列照片中的位置框和近照，便于人工复查；未复核的疑似缺陷单独列出。
"""
from __future__ import annotations

import base64
import html
import io
import time
from pathlib import Path

from PIL import ImageDraw

from .. import config, store, vision
from ..sim.camera import Frame

PRIORITY = {"broken": 0, "missing": 0, "crack": 1, "soiling": 2}
REASONS = {"misaimed": "近照未能对准", "unclear": "近照仍看不清", "no_budget": "电量不够，未复核", "model_down": "视觉模型不可用"}


def collect(state: dict, site: dict, battery_end: float) -> dict:
    mid = state["mission_id"]
    findings = store.findings(mid)
    photos = store.photos(mid)
    defects = [f for f in findings if f["status"] == "confirmed"]
    defects.sort(key=lambda f: (PRIORITY.get(f["type"] or "", 3), f["panel_id"]))
    unverified = [f for f in findings if f["status"] in ("suspect", "unverified")]
    inspected = state.get("inspected", [])
    panels_inspected = sum(1 for p in site["panels"] if p["array"] in inspected)
    started = state.get("started_at")
    return {
        "inspected_arrays": inspected,
        "skipped_arrays": state.get("skipped", []) + state.get("plan", {}).get("deferred", []),
        "panels_inspected": panels_inspected,
        "defects": [{"panel_id": f["panel_id"], "type": f["type"], "label": vision.DEFECT_TYPES.get(f["type"] or "", "缺陷"),
                     "confidence": f["confidence"], "evidence": f["evidence"], "screen_photo": f["screen_photo"],
                     "verify_photo": f["verify_photo"]} for f in defects],
        "unverified": [{"panel_id": f["panel_id"], "type": f["type"], "evidence": f["evidence"], "screen_photo": f["screen_photo"],
                        "reason": REASONS.get(f.get("reason") or "", "初筛疑似，未做近距复核")}
                       for f in unverified],
        "cleared": sum(1 for f in findings if f["status"] == "cleared"),
        "photos": {"overview": sum(1 for p in photos if p["kind"] == "overview"), "closeup": sum(1 for p in photos if p["kind"] == "closeup")},
        "flight_seconds": round(time.time() - started) if started else 0,
        "battery_used": round(state.get("battery_used") or ((state.get("battery_start") or battery_end) - battery_end), 1),
        "sorties": len(state.get("plan", {}).get("sorties", [])) or 1,
        "battery_end": round(battery_end, 1),
        "error": state.get("error"),
        "unjudged_arrays": state.get("unjudged", []),
        "closeup_policy": state.get("plan", {}).get("closeup_policy"),
    }


def facts_sentence(result: dict) -> str:
    """结论中的事实部分，全部来自程序统计。"""
    counts: dict[str, int] = {}
    for d in result["defects"]:
        counts[d["label"]] = counts.get(d["label"], 0) + 1
    arrays = "、".join(result["inspected_arrays"])
    parts = [f"本次巡检了 {arrays} 列共 {result['panels_inspected']} 块板" if arrays else "本次没有完成任何一列的巡检"]
    if result["defects"]:
        parts.append(f"确认缺陷 {len(result['defects'])} 块（" + "、".join(f"{k} {v}" for k, v in counts.items()) + "）")
    elif arrays:
        parts.append("没有确认的缺陷")
    if result["unverified"]:
        parts.append(f"另有 {len(result['unverified'])} 块疑似缺陷未确认，需要人工复查")
    if result["cleared"]:
        parts.append(f"复核后排除 {result['cleared']} 处疑似")
    if result.get("unjudged_arrays"):
        parts.append(f"{'、'.join(result['unjudged_arrays'])} 列只拍了照片，视觉模型不可用，未完成判断")
    if result["skipped_arrays"]:
        parts.append(f"{'、'.join(result['skipped_arrays'])} 列未巡检")
    if result.get("error"):
        parts.append(f"执行中出现异常：{result['error']}")
    return "，".join(parts) + "。"


def default_advice(result: dict, priorities: list[str]) -> str:
    """报告员不可用或建议被丢弃时，由程序按优先级规则生成建议。"""
    order = priorities or [d["panel_id"] for d in result["defects"]]
    if not order and not result["unverified"]:
        return ""
    text = f"建议优先处理 {'、'.join(order[:6])}。" if order else ""
    if result["unverified"]:
        text += "未确认的疑似缺陷请到现场或用照片人工确认。"
    return text


def for_reporter(result: dict) -> dict:
    """交给报告员的数据：字段含义直白，避免模型把照片编号之类的字段误读。"""
    return {
        "巡检的列": result["inspected_arrays"], "未巡检的列": result["skipped_arrays"], "检查的板数": result["panels_inspected"],
        "defects": [{"panel_id": d["panel_id"], "缺陷": d["label"], "置信度": round(d["confidence"] or 0, 2),
                     "确认方式": "近距复核确认" if d["verify_photo"] else "整列照片一眼可见（缺失）"} for d in result["defects"]],
        "unverified": [{"panel_id": u["panel_id"], "疑似": u["evidence"], "未确认原因": u.get("reason")} for u in result["unverified"]],
        "复核后排除的疑似": result["cleared"], "复核策略": result["closeup_policy"],
        "架次": result.get("sorties", 1), "飞行秒数": result["flight_seconds"], "耗电百分比": result["battery_used"], "异常": result["error"],
        "只拍照未判断的列": result.get("unjudged_arrays", []),
    }


def _img(image, width: int) -> str:
    img = image.copy()
    img.thumbnail((width, width * 2))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _frame(photo_id: int | None) -> Frame | None:
    if not photo_id:
        return None
    p = store.get_photo(photo_id)
    return Frame.load(Path(p["path"]).with_suffix("")) if p else None


def _marked_overview(frame: Frame, panel: dict) -> str:
    img = frame.image.copy()
    pts = [frame.pose.project(tuple(c)) for c in panel["corners"]]
    if all(pts):
        ImageDraw.Draw(img).polygon(pts, outline=(255, 40, 40), width=4)
    return _img(img, 520)


def render(mission_id: str, task: str, site: dict, result: dict) -> str:
    panels = {p["id"]: p for p in site["panels"]}
    rows = []
    for d in result["defects"]:
        cells = []
        ov = _frame(d["screen_photo"])
        if ov:
            cells.append(f'<img src="{_marked_overview(ov, panels[d["panel_id"]])}" alt="整列照片中的位置">')
        cl = _frame(d["verify_photo"])
        if cl:
            tile = vision.rectify(cl, panels[d["panel_id"]], (vision.CLOSE_W, vision.CLOSE_H))
            if tile:
                cells.append(f'<img src="{_img(tile.image, 220)}" alt="近照">')
        x, y, _ = panels[d["panel_id"]]["center"]
        rows.append(f"""<section class="defect"><h3>{html.escape(d['panel_id'])} · {html.escape(d['label'])}</h3>
<p>位置：{x:.1f} 米（东）、{y:.1f} 米（北）；置信度 {d['confidence'] or 0:.2f}；{'已近距复核' if d['verify_photo'] else '整列照片即可确认'}</p>
<p class="evidence">{html.escape(d['evidence'] or '')}</p><div class="imgs">{''.join(cells)}</div></section>""")
    unverified = "".join(f"<li>{html.escape(u['panel_id'])}（{html.escape(u.get('reason') or '')}）：{html.escape(u['evidence'] or '')}</li>"
                         for u in result["unverified"])
    skipped = "、".join(result["skipped_arrays"]) or "无"
    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>巡检报告 {mission_id}</title><style>
body{{font-family:-apple-system,"PingFang SC",sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#1c1f24;background:#fff}}
h1{{font-size:22px}} .stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:12px 0}}
.stats div{{border:1px solid #e3e6eb;border-radius:8px;padding:8px 10px}} .stats b{{display:block;font-size:20px}}
.defect{{border-top:1px solid #e3e6eb;padding:12px 0}} .imgs{{display:flex;gap:8px;flex-wrap:wrap}} .imgs img{{border-radius:6px;max-width:100%}}
.evidence{{color:#555}} .summary{{line-height:1.7;background:#f6f7f9;padding:12px;border-radius:8px}}
@media (prefers-color-scheme: dark){{body{{background:#111317;color:#e6e8eb}} .stats div,.defect{{border-color:#2c3139}} .summary{{background:#1f2329}} .evidence{{color:#9aa1ab}}}}
</style></head><body>
<h1>光伏巡检报告</h1><p>任务：{html.escape(task)}<br>任务编号：{mission_id} · 场景：{html.escape(site['name'])}</p>
<div class="summary">{html.escape(result.get('summary') or '')}</div>
<div class="stats"><div>巡检的列<b>{'、'.join(result['inspected_arrays']) or '无'}</b></div><div>检查的板<b>{result['panels_inspected']}</b></div>
<div>确认缺陷<b>{len(result['defects'])}</b></div><div>未复核疑似<b>{len(result['unverified'])}</b></div>
<div>照片<b>{result['photos']['overview']} 整列 / {result['photos']['closeup']} 近照</b></div>
<div>架次<b>{result.get('sorties', 1)}</b></div><div>飞行时间<b>{result['flight_seconds'] // 60} 分 {result['flight_seconds'] % 60} 秒</b></div><div>耗电<b>{result['battery_used']}%</b></div>
<div>模型费用<b>¥{(result.get('usage') or {}).get('cost_yuan', 0):.4f}</b></div></div>
<p>未巡检的列：{skipped}{('；异常：' + html.escape(result['error'])) if result.get('error') else ''}</p>
{('<p><b>未完成判断的列：' + html.escape('、'.join(result['unjudged_arrays'])) + '（视觉模型不可用，只有照片）</b></p>') if result.get('unjudged_arrays') else ''}
<h2>确认的缺陷（按处理优先级）</h2>{''.join(rows) or ('<p>没有确认的缺陷。</p>' if not result.get('unjudged_arrays') else '<p>已完成判断的部分没有确认的缺陷。</p>')}
<h2>未复核的疑似缺陷</h2>{('<ul>' + unverified + '</ul>') if unverified else '<p>无。</p>'}
<p style="color:#888;font-size:12px">本报告由巡检系统自动生成：缺陷判断来自视觉模型并经近距复核，位置与统计数字由程序计算。</p>
</body></html>"""
    path = config.PHOTOS_DIR / mission_id / "report.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return str(path)
