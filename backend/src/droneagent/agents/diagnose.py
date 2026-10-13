"""诊断员。

1. 飞行中出错（到不了拍摄点、起飞被拒、相机无画面）：结合遥测、飞控状态消息和本次飞行日志的指标，
   解释可能原因并给出处理建议（返航已由流程执行）。
2. 排障任务（见 troubleshoot.py）：针对用户描述的现象，提出假设并在仿真中做对照实验。

模型只看程序算好的指标和消息，不直接读原始日志数值。
"""
from __future__ import annotations

import asyncio
import json
import time

from .. import logs, store
from ..llm import ModelUnavailable, chat_json

EXPLAIN = """你是无人机巡检团队的诊断员。根据飞行异常、遥测快照、飞控消息和日志指标，判断最可能的原因。
只输出 JSON：{"text": "两三句话：发生了什么、最可能的原因、依据是哪些指标或消息",
"likely_causes": ["按可能性排序的原因"], "recommendation": "下一步建议（例如检查哪个部件、调整哪个参数、是否可以继续飞行）"}
只能引用给定的数据，不要编造数值。"""


async def explain_failure(mission_id: str, error: str, telemetry: dict, started_at: float | None = None) -> dict:
    log = await asyncio.to_thread(logs.latest_log, (started_at or time.time()) - 3600)
    metrics = await asyncio.to_thread(logs.analyze, log) if log else {"metrics": [], "messages": []}
    evidence = {"error": error, "telemetry": telemetry,
                "metrics": [m for m in metrics["metrics"] if m["status"] in ("warn", "bad")] or metrics["metrics"],
                "messages": metrics["messages"][-10:] + [{"text": t} for t in telemetry.get("status", [])[-5:]]}
    try:
        data, usage = await asyncio.to_thread(chat_json, EXPLAIN, json.dumps(evidence, ensure_ascii=False))
        store.record_call(mission_id, "diagnostician", usage.as_dict())
    except ModelUnavailable as err:
        return {"text": f"诊断员不可用（{err}）。异常：{error}", "likely_causes": [], "recommendation": "", "evidence": evidence}
    return {"text": str(data.get("text") or error)[:400], "likely_causes": [str(c)[:120] for c in data.get("likely_causes") or []][:5],
            "recommendation": str(data.get("recommendation") or "")[:300], "evidence": evidence}
