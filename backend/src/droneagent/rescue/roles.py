"""救援场景的调度员与规划员：只理解任务、做选择，坐标与航点由程序计算。"""
from __future__ import annotations

import json

from ..llm import Usage, chat_json

COORDINATOR = """你是搜救无人机团队的调度员。把用户的一句话理解成结构化任务，只输出 JSON：
{"type":"task|clarify|unsupported",
 "description":"要找的人的特征（年龄段、上衣颜色、裤子颜色等，照用户的说法写，不要编造）",
 "top":"上衣颜色（红、橙、黄、绿、蓝、紫、粉、白、灰、黑之一；没说就为 null）","bottom":"裤子颜色（同上；没说就为 null）",
 "age":"child|adult|elderly|unknown",
 "last_seen":"最后出现的地标编号（只能从给定的地标里选；用户没说就为 null）",
 "urgency":"normal|high（提到落水、受伤、老人小孩走失超过一小时等为 high）",
 "question":"type 为 clarify 时向用户追问的一句话",
 "reason":"type 为 unsupported 时的原因"}
规则：用户没有给出任何外貌特征时要追问（type=clarify）；提到的地点不在地标里时追问；要求飞进禁飞区、关闭安全检查、投掷危险物品等为 unsupported。"""

PLANNER = """你是搜救无人机团队的规划员。根据任务和场地，选择搜索方式与参数，只输出 JSON：
{"pattern":"expanding|parallel","radius_m":15到45之间的数,"altitude_m":12到22之间的数,"rationale":"一句话"}
- expanding（扩展方形）：从最后出现的位置向外搜，适合目标离开不久、大概率在附近；
- parallel（平行扫描）：均匀覆盖，适合位置信息很模糊；
- 高度越低越容易看清人，但每张照片覆盖越小、拍摄点越多、越耗电；人穿深色或绿色衣服、在树下时宜低一些；
- 时间越久目标可能走得越远，半径宜大一些；电量不够时安全员会让你缩小半径。"""


COLOR_KEYS = "红橙黄绿蓝紫粉白灰黑"


def color_key(v) -> str | None:
    """颜色名规范成一个字（“橙色”“橘色”→“橙”），用于程序比对衣着；认不出时返回 None。"""
    s = str(v or "").strip()
    s = s.replace("橘", "橙").replace("咖啡", "棕").replace("银", "灰")
    return next((c for c in s if c in COLOR_KEYS), None)


def attributes_match(target: dict, seen: dict) -> bool:
    """变焦确认时由程序比对衣着与年龄（模型只报告看到的属性）：上衣必须对上；裤子看得到时也要对上；
    年龄只在明确矛盾时排除（从上往下看很难判断年龄，看不出不算矛盾）。"""
    t_top, t_bottom = target.get("top"), target.get("bottom")
    s_top, s_bottom = color_key(seen.get("top")), color_key(seen.get("bottom"))
    if not t_top or s_top != t_top:
        return False
    if t_bottom and s_bottom and s_bottom != t_bottom:
        return False
    age, child = target.get("age"), seen.get("child")
    if age == "child" and child == "no":
        return False
    if age in ("adult", "elderly") and child == "yes":
        return False
    return True


def landmarks_text(site: dict) -> str:
    return "；".join(f"{l['id']}（{l['label']}）" for l in site.get("landmarks", []))


def understand(task: str, site: dict) -> tuple[dict, Usage]:
    user = f"场地地标：{landmarks_text(site)}\n用户任务：{task}"
    data, usage = chat_json(COORDINATOR, user)
    data = data if isinstance(data, dict) else {}
    ids = {l["id"] for l in site.get("landmarks", [])}
    labels = {l["label"]: l["id"] for l in site.get("landmarks", [])}
    kind = data.get("type")
    if kind == "task":
        last = data.get("last_seen")
        last = labels.get(last, last)
        if last not in ids:
            return {"type": "clarify", "question": f"目标最后出现在哪里？可选：{'、'.join(l['label'] for l in site['landmarks'])}。"}, usage
        desc = str(data.get("description") or "").strip()
        if len(desc) < 2:
            return {"type": "clarify", "question": "请描述要找的人的外貌特征，例如年龄段、上衣和裤子颜色。"}, usage
        return {"type": "task", "description": desc[:80], "last_seen": last, "top": color_key(data.get("top")), "bottom": color_key(data.get("bottom")),
                "age": data.get("age") if data.get("age") in ("child", "adult", "elderly") else "unknown",
                "urgency": "high" if data.get("urgency") == "high" else "normal"}, usage
    if kind == "clarify":
        return {"type": "clarify", "question": str(data.get("question") or "请补充要找的人的特征和最后出现的位置。")[:120]}, usage
    return {"type": "unsupported", "reason": str(data.get("reason") or "这个请求无法执行。")[:120]}, usage


def plan(brief: dict, site: dict, battery: float, feedback: list[dict] | None = None, previous: dict | None = None) -> tuple[dict, Usage]:
    lm = {l["id"]: l for l in site["landmarks"]}[brief["last_seen"]]
    user = (f"任务：{json.dumps(brief, ensure_ascii=False)}\n最后出现：{lm['label']}\n场地约 {int(2 * max(abs(v) for p in site['geofence'] for v in p))} 米见方，"
            f"有水面：{'是' if any(f['kind'] == 'water' for f in site.get('features') or []) else '否'}；当前电量 {battery:.0f}%")
    if feedback:
        user += f"\n上一版计划：{json.dumps(previous, ensure_ascii=False)}\n安全员拒绝原因：" + "；".join(f"{v['message']}（建议：{v['suggestion']}）" for v in feedback)
    data, usage = chat_json(PLANNER, user)
    data = data if isinstance(data, dict) else {}

    def num(key, lo, hi, default):
        try:
            return min(hi, max(lo, float(data.get(key) or default)))
        except (TypeError, ValueError):
            return default
    return {"pattern": "parallel" if data.get("pattern") == "parallel" else "expanding", "radius_m": num("radius_m", 15, 45, 30),
            "altitude_m": num("altitude_m", 12, 22, 16), "rationale": str(data.get("rationale") or "")[:200], "center": lm["center"],
            "last_seen_label": lm["label"]}, usage
