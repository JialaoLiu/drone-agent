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

from .. import config, context, store, vision
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
        "photos": {"overview": sum(1 for p in photos if p["kind"] == "overview"), "closeup": sum(1 for p in photos if p["kind"] == "closeup"),
                   "segment": sum(1 for p in photos if p["kind"] == "segment")},
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


def hotspot_arrays(defects: list[dict], top: int = 2) -> list[str]:
    """破碎和缺失最集中的列（程序统计）：数量排前 top 名的列，与第 top 名并列的也算，最多 3 列；不到 2 处的列不算集中。
    评测发现模型自己从清单里数“哪几列最集中”经常数错（不论清单是否压缩，见工程日志 35）。"""
    from collections import Counter
    counts = Counter(d["panel_id"].split("-")[0] for d in defects if PRIORITY.get(d.get("type") or "", 3) == 0)
    ranked = [c for c in counts.most_common() if c[1] >= 2]
    if not ranked:
        return []
    kth = ranked[min(top, len(ranked)) - 1][1]
    return sorted(a for a, n in ranked if n >= kth)[:3]


def for_reporter(result: dict, keep: int = context.KEEP_ITEMS) -> dict:
    """交给报告员的数据：字段含义直白，避免模型把照片编号之类的字段误读。keep：清单保留的条数（评测对比用）。"""
    # 缺陷多时只保留前若干条，另附全部条目按列和类型的数量：报告员只写建议，不需要逐条细节
    # 同一优先级内置信度高的在前：压缩时保留的是最该先处理、把握最大的几条，而不是编号靠前的几条
    ordered = sorted(result["defects"], key=lambda d: (PRIORITY.get(d.get("type") or "", 3), -(d["confidence"] or 0)))
    defects = [{"panel_id": d["panel_id"], "缺陷": d["label"], "置信度": round(d["confidence"] or 0, 2),
                "确认方式": "近距复核确认" if d["verify_photo"] else "整列照片一眼可见（缺失）"} for d in ordered]
    unverified = [{"panel_id": u["panel_id"], "疑似": u["evidence"], "未确认原因": u.get("reason")} for u in result["unverified"]]
    return {
        "巡检的列": result["inspected_arrays"], "未巡检的列": result["skipped_arrays"], "检查的板数": result["panels_inspected"],
        "defects": context.compact_items(defects, "缺陷", keep),
        "破碎和缺失最集中的列": hotspot_arrays(result["defects"]),
        "unverified": context.compact_items(unverified, "未确认原因", keep),
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
            cells.append(f'<img src="{_marked_overview(ov, panels[d["panel_id"]])}" alt="初筛照片中的位置">')
        cl = _frame(d["verify_photo"])
        if cl:
            tile = vision.rectify(cl, panels[d["panel_id"]], (vision.CLOSE_W, vision.CLOSE_H))
            if tile:
                cells.append(f'<img src="{_img(tile.image, 220)}" alt="近照">')
        x, y, _ = panels[d["panel_id"]]["center"]
        tone = {"broken": "p0", "missing": "p0", "crack": "p1"}.get(d.get("type") or "", "p2")
        rows.append(f"""<section class="defect {tone}"><div class="dh"><b>{html.escape(d['panel_id'])}</b><span class="badge {tone}">{html.escape(d['label'])}</span>
<span class="muted">置信度 {d['confidence'] or 0:.2f} · {'已近距复核' if d['verify_photo'] else '初筛照片即可确认'}</span></div>
<p class="muted">位置：东 {x:.1f} 米、北 {y:.1f} 米（相对起飞点）</p>
<p class="evidence">{html.escape(d['evidence'] or '')}</p><div class="imgs">{''.join(cells)}</div></section>""")
    unverified = "".join(f"<li><b>{html.escape(u['panel_id'])}</b>（{html.escape(u.get('reason') or '')}）：{html.escape(u['evidence'] or '')}</li>"
                         for u in result["unverified"])
    skipped = "、".join(result["skipped_arrays"]) or "无"
    n_def, n_unv = len(result["defects"]), len(result["unverified"])
    verdict = ("bad", f"发现 {n_def} 处缺陷，需要安排处理") if n_def else ("ok", "没有确认的缺陷")
    if n_unv:
        verdict = ("warn", verdict[1] + f"；另有 {n_unv} 处疑似需要人工复查")
    photos = result["photos"]
    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>巡检报告 {mission_id}</title><style>
:root{{--t:#1c1f24;--m:#6b7280;--l:#e3e6eb;--bg:#f6f7f9;--card:#fff;--a:#2f6fdf;--bad:#c2410c;--badbg:#fdecdf;--warn:#b45309;--warnbg:#fdf3e2;--ok:#15803d;--okbg:#e7f6ec}}
@media (prefers-color-scheme: dark){{:root{{--t:#e6e8eb;--m:#9aa1ab;--l:#2c3139;--bg:#111317;--card:#181b20;--a:#7aa2ff;--bad:#fb923c;--badbg:#3a2115;--warn:#fbbf24;--warnbg:#33270f;--ok:#4ade80;--okbg:#12301f}}}}
*{{box-sizing:border-box}} body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;margin:0;color:var(--t);background:var(--bg);line-height:1.6}}
.wrap{{max-width:980px;margin:0 auto;padding:24px 18px 48px}}
header{{background:linear-gradient(135deg,#10192b,#1e3a6e);color:#fff;padding:28px 18px}} header .wrap{{padding:0 18px}}
header h1{{margin:0 0 6px;font-size:24px}} header p{{margin:0;color:#c7d0de;font-size:14px}}
.verdict{{margin:-18px 0 16px;padding:12px 16px;border-radius:12px;background:var(--card);border:1px solid var(--l);border-left:6px solid var(--a);font-weight:600}}
.verdict.bad{{border-left-color:var(--bad)}} .verdict.warn{{border-left-color:var(--warn)}} .verdict.ok{{border-left-color:var(--ok)}}
.summary{{background:var(--card);border:1px solid var(--l);border-radius:12px;padding:14px 16px;margin-bottom:16px}}
.kpi{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:10px}}
.kpi div{{background:var(--card);border:1px solid var(--l);border-radius:12px;padding:10px 12px;font-size:13px;color:var(--m)}} .kpi b{{display:block;font-size:22px;color:var(--t)}}
.kpi.small b{{font-size:16px}} h2{{font-size:17px;margin:26px 0 10px}} .muted{{color:var(--m);font-size:13px}}
.defects{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:12px}}
.defect{{background:var(--card);border:1px solid var(--l);border-left:5px solid var(--l);border-radius:12px;padding:12px 14px;break-inside:avoid}}
.defect.p0{{border-left-color:var(--bad)}} .defect.p1{{border-left-color:var(--warn)}} .defect.p2{{border-left-color:var(--a)}}
.dh{{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}} .badge{{font-size:12px;padding:1px 9px;border-radius:999px;background:var(--bg)}}
.badge.p0{{background:var(--badbg);color:var(--bad)}} .badge.p1{{background:var(--warnbg);color:var(--warn)}}
.defect p{{margin:4px 0}} .evidence{{font-size:14px}} .imgs{{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}} .imgs img{{border-radius:8px;max-width:100%;max-height:260px}}
ul.unv{{background:var(--card);border:1px solid var(--l);border-radius:12px;padding:12px 12px 12px 30px}}
footer{{color:var(--m);font-size:12px;margin-top:28px;border-top:1px solid var(--l);padding-top:10px}}
@media print{{header{{background:#10192b;-webkit-print-color-adjust:exact;print-color-adjust:exact}} body{{background:#fff}}}}
@media (max-width:520px){{.defects{{grid-template-columns:1fr}}}}
</style></head><body>
<header><div class="wrap"><h1>光伏巡检报告</h1><p>任务：{html.escape(task)}<br>任务编号 {mission_id} · 场景 {html.escape(site['name'])}{(' · ' + html.escape(site['location']['name'])) if site.get('location') else ''}</p></div></header>
<div class="wrap">
<div class="verdict {verdict[0]}">{verdict[1]}</div>
<div class="summary">{html.escape(result.get('summary') or '')}</div>
<div class="kpi"><div>确认缺陷<b>{n_def}</b></div><div>待人工复查<b>{n_unv}</b></div><div>检查的板<b>{result['panels_inspected']}</b></div><div>巡检的列<b>{'、'.join(result['inspected_arrays']) or '无'}</b></div></div>
<div class="kpi small"><div>飞行时间<b>{result['flight_seconds'] // 60} 分 {result['flight_seconds'] % 60} 秒</b></div><div>架次<b>{result.get('sorties', 1)}</b></div>
<div>耗电<b>{result['battery_used']}%</b></div><div>照片<b>{photos['overview']} 整列 / {photos['closeup']} 近照{(' / ' + str(photos['segment']) + ' 分段') if photos.get('segment') else ''}</b></div>
<div>模型费用<b>¥{(result.get('usage') or {}).get('cost_yuan', 0):.4f}</b></div></div>
<p class="muted">未巡检的列：{skipped}{('；异常：' + html.escape(result['error'])) if result.get('error') else ''}</p>
{('<p><b>未完成判断的列：' + html.escape('、'.join(result['unjudged_arrays'])) + '（视觉模型不可用，只有照片）</b></p>') if result.get('unjudged_arrays') else ''}
<h2>确认的缺陷（按处理优先级：破碎与缺失 → 裂纹 → 污损）</h2>
{('<div class="defects">' + ''.join(rows) + '</div>') if rows else ('<p>没有确认的缺陷。</p>' if not result.get('unjudged_arrays') else '<p>已完成判断的部分没有确认的缺陷。</p>')}
<h2>待人工复查的疑似</h2>{('<ul class="unv">' + unverified + '</ul>') if unverified else '<p class="muted">无。</p>'}
<footer>本报告由巡检系统生成：缺陷判断来自视觉模型并经近距复核，位置与统计数字由程序计算；处理建议由报告员撰写，不含数字。</footer>
</div></body></html>"""
    path = config.PHOTOS_DIR / mission_id / "report.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return str(path)
