"""任务排队：一架仿真飞机同一时间只执行一个任务，执行中再下达的任务排队，按下达顺序执行。

每个任务的上下文彼此独立（LangGraph 线程号 = 任务号），排队只决定先后。
排队的任务记下当时的场景；轮到它时如果仿真已经换了场景或没有就绪，就标为失败并说明原因，不在别的场地上飞。
"""
from __future__ import annotations

import threading

from .. import simctl, store
from . import runtime

_lock = threading.Lock()


def busy(exclude: str | None = None) -> bool:
    """有任务正在规划、等待确认或执行（exclude：不算这个任务，例如刚创建、还没开始的它自己）。"""
    if runtime.active_mission and runtime.active_mission != exclude:
        return True
    return any(m["status"] in runtime.LIVE_STATUSES and m["id"] != exclude for m in store.list_missions(limit=50))


def enqueue(mid: str, auto_approve: bool) -> int:
    """把任务标为排队，返回它前面还有几个任务（含正在执行的）。"""
    store.update_mission(mid, status="queued", brief={"queued": {"auto_approve": auto_approve}})
    ahead = sum(1 for m in store.list_missions(limit=200) if m["status"] == "queued" and m["id"] != mid) + 1
    store.event(mid, "system", "queued", f"已加入队列，前面还有 {ahead} 个任务；轮到时自动开始")
    return ahead


def dispatch_next() -> str | None:
    """没有任务在执行时，启动最早排队的任务。返回启动的任务编号。"""
    from . import mission
    with _lock:
        if busy():
            return None
        queued = sorted((m for m in store.list_missions(limit=200) if m["status"] == "queued"), key=lambda m: m["created_at"])
        if not queued:
            return None
        nxt = store.get_mission(queued[0]["id"])
        status = simctl.status()
        if status["scene"] != nxt["scene"] or not status["px4"]:
            store.update_mission(nxt["id"], status="failed", only_from=("queued",))
            store.event(nxt["id"], "system", "queue_skip",
                        f"排队任务未执行：下达时的场景是 {nxt['scene']}，轮到时仿真{'是 ' + status['scene'] if status['scene'] else '没有运行'}")
            return None
        auto = bool(((nxt.get("brief") or {}).get("queued") or {}).get("auto_approve"))
        store.update_mission(nxt["id"], status="planning", brief=None, only_from=("queued",))
        store.event(nxt["id"], "system", "dequeued", "轮到这个任务，开始执行")
        mission.start(nxt["id"], nxt["task"], nxt["scene"], auto)
        return nxt["id"]
