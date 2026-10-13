"""本地 SQLite：任务、事件（先落库再推送）、照片、发现、模型用量。

照片文件存放在 PHOTOS_DIR/<任务编号>/，数据库只存路径与拍摄位姿。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS missions (
    id TEXT PRIMARY KEY,
    task TEXT NOT NULL,                 -- 用户的自然语言任务
    scene TEXT NOT NULL,
    status TEXT NOT NULL,               -- planning / awaiting_approval / running / done / failed / cancelled / rejected
    brief TEXT,                         -- JSON：调度员理解的任务
    plan TEXT,                          -- JSON：拍摄步骤与安全校验结果
    result TEXT,                        -- JSON：发现汇总、未完成项、报告
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id TEXT NOT NULL,
    role TEXT NOT NULL,                 -- coordinator / planner / safety / pilot / inspector / reporter / diagnostician / system
    kind TEXT NOT NULL,                 -- 事件类型，例如 plan、violation、telemetry、photo、finding
    message TEXT NOT NULL,              -- 给人看的一句话（程序生成）
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_mission ON events (mission_id, seq);
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    kind TEXT NOT NULL,                 -- overview / closeup
    target TEXT NOT NULL,
    camera TEXT NOT NULL,
    path TEXT NOT NULL,
    meta TEXT NOT NULL,                 -- JSON：相机位姿、仿真时间、电量
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS photos_mission ON photos (mission_id, id);
CREATE TABLE IF NOT EXISTS findings (
    mission_id TEXT NOT NULL,
    panel_id TEXT NOT NULL,
    status TEXT NOT NULL,               -- suspect / confirmed / cleared / unverified
    type TEXT,
    confidence REAL,
    evidence TEXT,
    screen_photo INTEGER,
    verify_photo INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (mission_id, panel_id)
);
CREATE TABLE IF NOT EXISTS model_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id TEXT,
    role TEXT NOT NULL,
    model TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
    images INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    cost_yuan REAL,
    created_at TEXT NOT NULL
);
"""

_ready: set[str] = set()
_lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = config.APP_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    try:
        con.row_factory = sqlite3.Row
        if str(path) not in _ready:
            with _lock:
                con.executescript(SCHEMA)
                _ready.add(str(path))
        with con:
            yield con
    finally:
        con.close()


def _loads(row: sqlite3.Row | None, *fields: str) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for f in fields:
        d[f] = json.loads(d[f]) if d.get(f) else None
    return d


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


# ---------- 任务 ----------

def create_mission(task: str, scene: str) -> str:
    mid = uuid.uuid4().hex[:10]
    with connect() as con:
        con.execute("INSERT INTO missions (id, task, scene, status, created_at, updated_at) VALUES (?, ?, ?, 'planning', ?, ?)",
                    (mid, task, scene, now(), now()))
    return mid


def update_mission(mid: str, only_from: tuple[str, ...] | None = None, **fields) -> bool:
    """更新任务字段；only_from 给出时只有当前状态在其中才更新（防止覆盖已取消的任务）。"""
    sets, params = [], []
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        params.append(_dumps(v) if k in ("brief", "plan", "result") else v)
    sql = f"UPDATE missions SET {', '.join(sets)}, updated_at = ? WHERE id = ?"
    params += [now(), mid]
    if only_from:
        sql += f" AND status IN ({', '.join('?' for _ in only_from)})"
        params += list(only_from)
    with connect() as con:
        return con.execute(sql, params).rowcount > 0


def get_mission(mid: str) -> dict | None:
    with connect() as con:
        return _loads(con.execute("SELECT * FROM missions WHERE id = ?", (mid,)).fetchone(), "brief", "plan", "result")


def list_missions(limit: int = 50) -> list[dict]:
    with connect() as con:
        # 评测脚本创建的记录（scene = 'eval'）不出现在网页的历史列表里
        rows = con.execute("SELECT id, task, scene, status, created_at, updated_at FROM missions WHERE scene != 'eval' "
                           "ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]


def mission_status(mid: str) -> str | None:
    with connect() as con:
        row = con.execute("SELECT status FROM missions WHERE id = ?", (mid,)).fetchone()
    return row["status"] if row else None


def active_missions() -> list[str]:
    with connect() as con:
        return [r["id"] for r in con.execute("SELECT id FROM missions WHERE status IN ('planning', 'awaiting_approval', 'running')")]


# ---------- 事件 ----------

def event(mid: str, role: str, kind: str, message: str, payload: dict | None = None) -> int:
    with connect() as con:
        cur = con.execute("INSERT INTO events (mission_id, role, kind, message, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                          (mid, role, kind, message, _dumps(payload or {}), now()))
        return cur.lastrowid


def events(mid: str, after: int = 0, kinds: tuple[str, ...] | None = None) -> list[dict]:
    sql, params = "SELECT * FROM events WHERE mission_id = ? AND seq > ?", [mid, after]
    if kinds:
        sql += f" AND kind IN ({', '.join('?' for _ in kinds)})"
        params += list(kinds)
    with connect() as con:
        return [_loads(r, "payload") for r in con.execute(sql + " ORDER BY seq", params)]


# ---------- 照片与发现 ----------

def add_photo(mid: str, step: int, kind: str, target: str, camera: str, path: str, meta: dict) -> int:
    with connect() as con:
        return con.execute("INSERT INTO photos (mission_id, step, kind, target, camera, path, meta, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (mid, step, kind, target, camera, path, _dumps(meta), now())).lastrowid


def photos(mid: str) -> list[dict]:
    with connect() as con:
        return [_loads(r, "meta") for r in con.execute("SELECT * FROM photos WHERE mission_id = ? ORDER BY id", (mid,))]


def get_photo(pid: int) -> dict | None:
    with connect() as con:
        return _loads(con.execute("SELECT * FROM photos WHERE id = ?", (pid,)).fetchone(), "meta")


def upsert_finding(mid: str, panel_id: str, **fields) -> None:
    cols = ["status", "type", "confidence", "evidence", "screen_photo", "verify_photo"]
    values = {c: fields.get(c) for c in cols if c in fields}
    with connect() as con:
        exists = con.execute("SELECT 1 FROM findings WHERE mission_id = ? AND panel_id = ?", (mid, panel_id)).fetchone()
        if exists:
            sets = ", ".join(f"{c} = ?" for c in values)
            con.execute(f"UPDATE findings SET {sets}, updated_at = ? WHERE mission_id = ? AND panel_id = ?",
                        [*values.values(), now(), mid, panel_id])
        else:
            con.execute(f"INSERT INTO findings (mission_id, panel_id, {', '.join(values)}, updated_at) VALUES (?, ?, {', '.join('?' for _ in values)}, ?)",
                        [mid, panel_id, *values.values(), now()])


def findings(mid: str) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM findings WHERE mission_id = ? ORDER BY panel_id", (mid,))]


# ---------- 模型用量 ----------

def record_call(mid: str | None, role: str, usage: dict) -> None:
    with connect() as con:
        con.execute("""INSERT INTO model_calls (mission_id, role, model, prompt_tokens, completion_tokens, cache_hit_tokens, images,
                       latency_ms, cost_yuan, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (mid, role, usage.get("model"), usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
                     usage.get("cache_hit_tokens", 0), usage.get("images", 0), usage.get("latency_ms", 0), usage.get("cost_yuan"), now()))


def usage_summary(mid: str | None = None) -> dict:
    where, params = ("WHERE mission_id = ?", [mid]) if mid else ("", [])
    with connect() as con:
        by_role = [dict(r) for r in con.execute(
            f"""SELECT role, COUNT(*) AS calls, SUM(prompt_tokens) AS prompt_tokens, SUM(completion_tokens) AS completion_tokens,
                       SUM(images) AS images, SUM(cost_yuan) AS cost_yuan, ROUND(AVG(latency_ms)) AS avg_ms
                FROM model_calls {where} GROUP BY role ORDER BY role""", params)]
    total = {"calls": sum(r["calls"] for r in by_role), "cost_yuan": round(sum(r["cost_yuan"] or 0 for r in by_role), 5),
             "prompt_tokens": sum(r["prompt_tokens"] or 0 for r in by_role), "images": sum(r["images"] or 0 for r in by_role)}
    return {"by_role": by_role, "total": total}
