"""上下文预算与压缩。

本项目的设计让大多数调用的上下文不随任务变长（角色之间只传结构化状态）。实测有两处会增长：
- 诊断员：每多做一次实验，输入多约 260 Token（之前所有实验的完整结果都带上）；
- 报告员：缺陷逐条列出，缺陷多时输入可到 3000 Token 以上。
这里用“程序做结构化摘要”而不是“让模型总结”：摘要规则确定、可测试，关键事实（参数名、编号、数量）不会丢。
另给每个角色设输入预算，超出时记一条事件，便于发现新的膨胀点。
"""
from __future__ import annotations

from collections import Counter

# 每次调用的输入 Token 预算（实测常态的 2–3 倍）；超出只告警，不拦截
BUDGETS = {"coordinator": 1500, "planner": 2000, "reporter": 1500, "diagnostician": 1500, "inspector": 1500}
KEEP_EXPERIMENTS = 1        # 诊断员：最近的实验保留完整指标，更早的只保留结论
KEEP_ITEMS = 12             # 报告员：缺陷 / 未确认各保留前若干条（已按处理优先级排序），其余汇总


def over_budget(role: str, prompt_tokens: int) -> str | None:
    budget = BUDGETS.get(role)
    if budget and prompt_tokens > budget:
        return f"{role} 本次输入 {prompt_tokens} Token，超过预算 {budget}"
    return None


def compact_experiments(experiments: list[dict], keep_last: int = KEEP_EXPERIMENTS) -> list[dict]:
    """更早的实验压缩为“恢复了哪些参数、哪些异常指标恢复正常”，去掉逐项的前后数值。"""
    if len(experiments) <= keep_last:
        return experiments
    old, recent = experiments[:-keep_last], experiments[-keep_last:]
    summary = [{"restored": e["restored"], "improved": e.get("improved") or [],
                "conclusion": "有改善" if e.get("improved") else "异常指标没有恢复"} for e in old]
    return summary + recent


def compact_items(items: list[dict], key_type: str, keep: int = KEEP_ITEMS) -> dict | list:
    """缺陷或未确认清单：保留前 keep 条（已按优先级排序），其余按类型和列汇总数量。"""
    if len(items) <= keep:
        return items
    rest = items[keep:]
    return {"前若干条（按处理优先级）": items[:keep], "其余数量": len(rest),
            "其余按类型": dict(Counter(str(i.get(key_type) or "未知") for i in rest)),
            "其余按列": dict(Counter(str(i.get("panel_id", "?")).split("-")[0] for i in rest))}
