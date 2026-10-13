"""巡检员的判断规则：初筛、选择复核对象、复核与裁决。实时任务和离线评测共用这一份代码。

流程：整列照片 → 初筛（拼图）→ 按策略选复核对象 → 近照复核 →
      复核与初筛矛盾（初筛很确定是缺陷，复核却说正常）或复核结论无法识别时，再看一次，以第二次为准。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import vision
from .llm import Usage
from .sim.camera import Frame

SHEET = 10                          # 初筛时一张拼图放几块板
CONFIRM_WITHOUT_CLOSEUP = 0.9       # “缺失”一眼可见，置信度够高就不再复核
CONFLICT_CONFIDENCE = 0.6           # 初筛置信度达到这个值、复核却判正常时，再看一次


@dataclass
class Screening:
    candidates: list[dict]                 # 疑似缺陷与看不清的板
    tiles: int                             # 实际判断的板数
    usages: list[Usage] = field(default_factory=list)


def screen_frame(frame: Frame, panels: list[dict], provider: str | None = None, parallel=None) -> Screening:
    """对一张整列照片做初筛。parallel(fn, args_list) 可传入并行执行器。"""
    tiles, unseen = [], []
    for p in panels:
        t = vision.rectify(frame, p)
        if t:
            tiles.append(t)
        else:
            unseen.append(p["id"])
    sheets = [tiles[i:i + SHEET] for i in range(0, len(tiles), SHEET)]
    results = parallel(lambda s: vision.screen(s, provider), sheets) if parallel else [vision.screen(s, provider) for s in sheets]
    candidates, usages = [], []
    for found, usage in results:
        usages.append(usage)
        candidates += found
    candidates += [{"id": pid, "status": "unsure", "type": None, "confidence": 0.0, "evidence": "整列照片中看不到或太小",
                    "unseen": True} for pid in unseen]
    return Screening(candidates, len(tiles), usages)


def select_targets(policy: str, candidates: list[dict], array_panels: list[dict]) -> tuple[list[str], list[str]]:
    """按复核策略选择要近距复核的板。返回（复核对象, 不需复核即可确认的板）。
    复核顺序：疑似缺陷在前、看不清在后；同类按置信度从低到高（越不确定越需要看）。"""
    cands = {c["id"]: c for c in candidates}
    if policy == "all":
        targets = [p["id"] for p in array_panels]
    elif policy == "flagged_and_unsure":
        targets = list(cands)
    elif policy == "flagged":
        targets = [k for k, c in cands.items() if c["status"] == "defect"]
    else:
        targets = []
    confirmed = [k for k in targets if cands.get(k, {}).get("type") == "missing" and cands[k]["confidence"] >= CONFIRM_WITHOUT_CLOSEUP]
    targets = [k for k in targets if k not in confirmed]
    targets.sort(key=lambda k: (0 if cands.get(k, {}).get("status") == "defect" else 1, cands.get(k, {}).get("confidence", 0)))
    return targets, confirmed


def adjudicate(tile: vision.Tile, screened: dict | None, provider: str | None = None) -> tuple[dict, list[Usage], bool]:
    """复核一块板并裁决。返回（结论, 用量, 是否再看了一次）。结论 status 为 defect / ok / unsure。"""
    verdict, usage = vision.verify(tile, provider)
    usages = [usage]
    screened = screened or {}
    conflict = verdict["status"] == "ok" and screened.get("status") == "defect" and screened.get("confidence", 0) >= CONFLICT_CONFIDENCE
    if verdict["status"] == "unsure" or conflict:
        verdict, usage = vision.verify(tile, provider)
        usages.append(usage)
        return verdict, usages, True
    return verdict, usages, False
