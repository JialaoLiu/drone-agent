"""救援报告：事实全部由程序生成（是否找到、位置、是否落水、用时、覆盖、路人与误报数量），建议按规则给出。"""
from __future__ import annotations

import html
import time

from .. import config, store
from ..agents.report import _frame, _img

KIND_LABELS = {"target": "确认目标", "rejected": "确认后排除", "bystander": "路人", "object": "非人物体", "unsure": "看不清", "candidate": "疑似未确认"}


def collect(state: dict, site: dict, battery_end: float) -> dict:
    mid = state["mission_id"]
    dets = state.get("detections") or []
    found = state.get("found")
    plan = state.get("plan") or {}
    searched, planned = state.get("searched", 0), plan.get("photos", 0)
    started = state.get("started_at")
    photos = store.photos(mid)
    counts = {k: sum(1 for d in dets if d["kind"] == k) for k in KIND_LABELS}
    lm = plan.get("last_seen_label", "")
    if found:
        dist = ((found["x"] - plan["center"][0]) ** 2 + (found["y"] - plan["center"][1]) ** 2) ** 0.5
        facts = (f"已找到目标，位于东 {found['x']:.1f} 米、北 {found['y']:.1f} 米（相对起飞点），距最后出现的{lm}约 {dist:.0f} 米，"
                 f"起飞后 {found['elapsed_s'] // 60} 分 {found['elapsed_s'] % 60} 秒确认") + ("；目标在水中" if found["in_water"] else "") + "。"
        advice = ("目标在水中，请立即派救生人员携带救生圈前往，无人机可返回悬停指示位置。" if found["in_water"]
                  else "请地面人员按坐标前往接回，途中注意目标可能继续走动。")
    else:
        facts = f"本次没有找到目标：搜索了计划 {planned} 个拍摄点中的 {searched} 个" + ("（因电量或异常提前结束）" if searched < planned else "") + "。"
        advice = "建议扩大搜索半径或改为平行扫描再飞一次，并组织地面人员在水边与出入口重点排查。"
    facts += f"发现路人 {counts['bystander']} 人，确认后排除 {counts['rejected']} 人，非人物体 {counts['object']} 处，看不清 {counts['unsure']} 处。"
    return {"scenario": "rescue", "found": found, "detections": dets, "counts": counts, "searched": searched, "planned": planned,
            "coverage": round(searched / planned, 3) if planned else None, "summary": facts + advice, "advice": advice,
            "description": (state.get("brief") or {}).get("description"), "last_seen": lm,
            "flight_seconds": round(time.time() - started) if started else 0, "battery_used": round(state.get("battery_used") or 0, 1),
            "photos": {"search": sum(1 for p in photos if p["kind"] == "search"), "confirm": sum(1 for p in photos if p["kind"] == "confirm")},
            "error": state.get("error"), "inspected_arrays": [], "skipped_arrays": [], "panels_inspected": 0, "defects": [], "unverified": []}


def render(mission_id: str, task: str, site: dict, result: dict) -> str:
    found = result["found"]
    img = ""
    if found and found.get("photo"):
        fr = _frame(found["photo"])
        if fr:
            img = f'<img src="{_img(fr.image, 640)}" alt="确认照片">'
    rows = "".join(f"<tr><td>{d['id']}</td><td>{KIND_LABELS.get(d['kind'], d['kind'])}</td><td>{d['x']:.1f}, {d['y']:.1f}</td>"
                   f"<td>{html.escape(str(d.get('top') or ''))} / {html.escape(str(d.get('bottom') or ''))}</td><td>{html.escape(d.get('evidence') or '')}</td></tr>"
                   for d in result["detections"])
    tone = ("bad" if found["in_water"] else "ok") if found else "warn"
    title = ("已找到目标（在水中）" if found["in_water"] else "已找到目标") if found else "没有找到目标"
    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>搜救报告 {mission_id}</title><style>
:root{{--t:#1c1f24;--m:#6b7280;--l:#e3e6eb;--bg:#f6f7f9;--card:#fff;--bad:#c2410c;--warn:#b45309;--ok:#15803d}}
@media (prefers-color-scheme: dark){{:root{{--t:#e6e8eb;--m:#9aa1ab;--l:#2c3139;--bg:#111317;--card:#181b20;--bad:#fb923c;--warn:#fbbf24;--ok:#4ade80}}}}
body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;margin:0;color:var(--t);background:var(--bg);line-height:1.6}}
header{{background:linear-gradient(135deg,#3b0d0d,#7a1f1f);color:#fff;padding:26px 18px}} .wrap{{max-width:980px;margin:0 auto;padding:0 18px}}
header h1{{margin:0 0 6px;font-size:24px}} header p{{margin:0;color:#f3d6d6;font-size:14px}}
.verdict{{margin:16px 0;padding:12px 16px;border-radius:12px;background:var(--card);border:1px solid var(--l);border-left:6px solid var(--ok);font-weight:600}}
.verdict.bad{{border-left-color:var(--bad)}} .verdict.warn{{border-left-color:var(--warn)}}
.card{{background:var(--card);border:1px solid var(--l);border-radius:12px;padding:14px 16px;margin-bottom:14px}}
img{{max-width:100%;border-radius:8px}} table{{width:100%;border-collapse:collapse;font-size:13px}} td,th{{text-align:left;padding:6px;border-bottom:1px solid var(--l)}}
.muted{{color:var(--m);font-size:13px}}
</style></head><body>
<header><div class="wrap"><h1>搜救报告</h1><p>任务：{html.escape(task)}<br>任务编号 {mission_id} · 场景 {html.escape(site['name'])}{(' · ' + html.escape(site['location']['name'])) if site.get('location') else ''}</p></div></header>
<div class="wrap">
<div class="verdict {tone}">{title}</div>
<div class="card">{html.escape(result['summary'])}</div>
<div class="card"><b>目标特征</b>：{html.escape(result.get('description') or '')}；最后出现：{html.escape(result.get('last_seen') or '')}<br>
<span class="muted">搜索覆盖 {result['searched']}/{result['planned']} 个拍摄点 · 飞行 {result['flight_seconds'] // 60} 分 {result['flight_seconds'] % 60} 秒 · 耗电 {result['battery_used']}% ·
照片 {result['photos']['search']} 张搜索、{result['photos']['confirm']} 张确认 · 模型费用 ¥{(result.get('usage') or {}).get('cost_yuan', 0):.4f}</span></div>
{('<div class="card"><b>确认照片</b><br>' + img + '</div>') if img else ''}
<div class="card"><b>搜索中看到的人和物</b><table><tr><th>编号</th><th>判断</th><th>位置（东, 北，米）</th><th>上衣 / 裤子</th><th>依据</th></tr>{rows or '<tr><td colspan=5>无</td></tr>'}</table></div>
<p class="muted">本报告由搜救系统生成：位置由程序根据相机位姿计算；人物判断来自视觉模型，目标经变焦近照确认。</p>
</div></body></html>"""
    path = config.PHOTOS_DIR / mission_id / "report.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return str(path)
