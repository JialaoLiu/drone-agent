"""HTTP 接口。

GET  /scenes                      场景列表；POST /scenes 生成新场景
GET  /sim                         仿真状态与遥测；POST /sim/start、/sim/stop
GET  /site/{scene}                场地几何（不含标准答案）；GET /site/{scene}/truth 标准答案（评测与对照用）
POST /missions                    下达巡检任务；GET /missions、/missions/{id}
POST /missions/{id}/approve       确认或拒绝计划；POST /missions/{id}/cancel
WS   /missions/{id}/events        事件推送（先落库再推送，断线按序号补发）与实时遥测
GET  /photos/{id}                 照片（可叠加光伏板框与发现）
GET  /missions/{id}/report        巡检报告（HTML）
GET  /usage                       模型用量

设置 DRONE_AGENT_ACCESS_TOKEN 后所有接口都要带访问令牌（请求头 X-Access-Token，WebSocket 用 token 参数）。
"""
from __future__ import annotations

import asyncio
import threading
import hmac
import io
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from PIL import ImageDraw
from pydantic import BaseModel, Field, StringConstraints

from . import config, scoring, settings, simctl, store, vision
from . import health as preflight_check
from .agents import mission, runtime, troubleshoot
from .agents import queue as task_queue
from .sim import scene
from .flight import FlightError
from .sim.camera import Frame

@asynccontextmanager
async def lifespan(_: FastAPI):
    recover()
    yield


app = FastAPI(title="Drone Inspection Agent", lifespan=lifespan)


def _authorized(token: str | None) -> bool:
    expected = os.environ.get("DRONE_AGENT_ACCESS_TOKEN")
    return not expected or (token is not None and hmac.compare_digest(token, expected))


@app.middleware("http")
async def require_token(request: Request, call_next):
    # JSON 接口用请求头；照片、裁切图、报告由浏览器直接加载，读取同源 cookie（深度审查 M01）
    token = request.headers.get("x-access-token") or request.cookies.get("drone_access_token")
    if request.url.path not in ("/", "/health") and not _authorized(token):
        return JSONResponse({"detail": "需要访问令牌"}, status_code=401)
    return await call_next(request)


def recover() -> None:
    """服务重启时，飞行中的任务无法接续（飞控连接已断开），标记为失败；等待确认的任务保留。"""
    config.ensure_dirs()
    settings.load()
    for mid in store.active_missions():
        if store.mission_status(mid) in ("running", "planning"):
            store.update_mission(mid, status="failed")
            store.event(mid, "system", "error", "服务重启，任务中断。飞控在失去地面站连接后会按失联保护自动返航。")


@app.get("/")
def root() -> dict:
    return {"message": "无人机巡检 Agent 后端。网页界面：在 frontend 目录运行 npm run dev。", "docs": "/docs"}


@app.get("/health")
def health() -> dict:
    return {"ok": True}


# ---------- 场景与仿真 ----------

class Place(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    address: str = ""
    city: str = ""
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    source: str = ""


class NewScene(BaseModel):
    seed: int | None = Field(None, ge=1, le=100000)   # 不给时随机选一个布局编号
    defect_rate: float = Field(0.12, ge=0.02, le=0.4)
    location: Place | None = None         # 真实场地（来自 /places 的检索结果）
    scenario: str = Field("solar", pattern="^(solar|rescue)$")


@app.get("/scenes")
def get_scenes() -> list[dict]:
    return simctl.scenes()


def _unique_name(base: str) -> str:
    """场景名不能覆盖已有场景（不同地点用同一个布局编号时会同名，负责人测试 1.28）。"""
    name, n = base, 2
    while (config.WORLDS_DIR / name / "site.json").exists():
        name, n = f"{base}_{n}", n + 1
    return name


@app.post("/scenes")
def create_scene(body: NewScene) -> dict:
    import random
    seed = body.seed or random.randint(1, 100000)
    if body.scenario == "rescue":
        from .rescue import scene as rescue_scene
        out = rescue_scene.generate(seed, _unique_name(f"rescue_{seed}"), body.location.model_dump() if body.location else None)
        return {"name": out.name, "seed": seed, "ground": scene.load_site(out.name).get("ground")}
    if body.location:
        out = scene.generate(seed, _unique_name(f"site_{seed}"), body.defect_rate, location=body.location.model_dump())
    else:
        out = scene.generate(seed, _unique_name(f"solar_{seed}"), body.defect_rate)
    return {"name": out.name, "seed": seed, "ground": scene.load_site(out.name).get("ground")}


@app.get("/places/suggest")
def places_suggest(q: Annotated[str, Query(min_length=1, max_length=60)]) -> list[dict]:
    """地点联想：国内用百度，境外用 OpenStreetMap（Photon）。坐标均为 WGS-84。"""
    from . import maps
    return maps.suggest_place(q)


@app.get("/places")
def places(q: Annotated[str, Query(min_length=1, max_length=60)], city: str = "全国") -> list[dict]:
    """真实地点检索（百度地点检索，坐标已换算为 WGS-84）。"""
    from . import maps
    try:
        return maps.search_place(q, city or "全国")
    except maps.MapError as error:
        raise HTTPException(502, str(error)) from error


@app.get("/sim")
def sim_status() -> dict:
    runtime.reconcile(store.mission_status)       # 已结束的任务仍占用飞机时自动释放（DR-058）
    task_queue.dispatch_next()                    # 空闲时启动排队的任务
    status = simctl.status()
    if status["px4"] and not runtime.telemetry:
        runtime.submit(runtime.flight())          # 仿真就绪后建立连接，开始接收遥测
    return {**status, "telemetry": runtime.telemetry if status["scene"] else {}, "active_mission": runtime.active_mission,
            "job": {k: v for k, v in _sim_job.items() if k != "gen"}}


_health_cache: dict = {}


@app.get("/sim/health")
async def sim_health() -> dict:
    """飞行前检查（与安全员起飞前执行的是同一套规则）。网页顶部的状态指示用；结果缓存 10 秒。"""
    if _sim_job["state"] == "starting":
        return {"ok": None, "items": [], "advice": "仿真启动中", "checked_at": time.time()}
    if not simctl.status()["px4"]:
        return {"ok": None, "items": [], "advice": "仿真未启动", "checked_at": time.time()}
    if _health_cache and time.time() - _health_cache["checked_at"] < 10:
        return _health_cache

    async def check():
        return await preflight_check.preflight(await runtime.flight())
    try:
        result = await asyncio.wait_for(asyncio.wrap_future(runtime.submit(check())), 20)
    except (asyncio.TimeoutError, FlightError) as error:
        return {"ok": False, "items": [], "advice": f"无法完成检查：{str(error) or '超时'}", "checked_at": time.time()}
    _health_cache.clear()
    _health_cache.update(result)
    return result


class SimStart(BaseModel):
    scene: str


# 仿真启动在后台线程进行，请求立刻返回；网页从 /sim 的 job 字段读取阶段与结果（DR-059）
_sim_job: dict = {"state": "idle", "scene": None, "stage": "", "error": "", "started_at": 0.0, "gen": 0}
_sim_job_lock = threading.Lock()


def _run_sim_start(scene_name: str, gen: int) -> None:
    def stage(text: str) -> None:
        with _sim_job_lock:
            if _sim_job["gen"] == gen:
                _sim_job["stage"] = text
    try:
        simctl.start(scene_name, on_stage=stage, cancelled=lambda: _sim_job["gen"] != gen)
        with _sim_job_lock:
            if _sim_job["gen"] != gen:           # 已作废的启动不能清理新一代的遥测和检查结果（审查 H10）
                return
            _sim_job.update(state="idle", stage="", error="")
        runtime.telemetry.clear()
        _health_cache.clear()
        runtime.submit(runtime.flight())
    except Exception as error:     # noqa: BLE001  启动失败要让网页看到原因
        with _sim_job_lock:
            if _sim_job["gen"] == gen:
                _sim_job.update(state="error", error=str(error) or type(error).__name__, stage="")


@app.post("/sim/start")
def sim_start(body: SimStart) -> dict:
    if runtime.active_mission:
        raise HTTPException(409, "有任务正在执行，不能切换场景")
    if not (config.WORLDS_DIR / body.scene / "site.json").exists():
        raise HTTPException(404, "场景不存在")
    with _sim_job_lock:
        if _sim_job["state"] == "starting":
            raise HTTPException(409, f"场景 {_sim_job['scene']} 正在启动，请等待或先停止")
        _sim_job.update(state="starting", scene=body.scene, stage="准备启动", error="", started_at=time.time(), gen=_sim_job["gen"] + 1)
        gen = _sim_job["gen"]
    _health_cache.clear()
    threading.Thread(target=_run_sim_start, args=(body.scene, gen), daemon=True, name="sim-start").start()
    return {**simctl.status(), "job": dict(_sim_job)}


_view_cache: dict = {}


@app.get("/sim/view")
def sim_view(camera: str = Query("wide", pattern="^(wide|zoom)$")) -> Response:
    """机载相机的实时画面（网页“实时视角”每 2 秒请求一次）。同一相机 1.5 秒内复用上一帧，避免多个页面同时请求时压垮相机桥。"""
    from .sim import camera as cam
    hit = _view_cache.get(camera)
    if hit and time.time() - hit[0] < 1.5:
        return Response(hit[1], media_type="image/jpeg", headers={"Cache-Control": "no-store"})
    try:
        frame = cam.capture(camera, timeout=3.0)
    except cam.CameraUnavailable as error:
        raise HTTPException(503, str(error)) from error
    img = frame.image.copy()
    img.thumbnail((720, 720))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=75)
    _view_cache[camera] = (time.time(), buf.getvalue())
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/sim/return")
def sim_return() -> dict:
    """一键返航：任何时候都可以调用；有任务在执行时同时取消该任务。"""
    if runtime.active_mission:
        store.update_mission(runtime.active_mission, status="cancelled", only_from=("running",))
    message = runtime.submit(mission.emergency_return(runtime.active_mission)).result(timeout=15)
    return {"message": message}


@app.post("/sim/stop")
async def sim_stop() -> dict:
    if runtime.active_mission:
        raise HTTPException(409, "有任务正在执行，不能停止仿真")
    with _sim_job_lock:
        _sim_job.update(state="idle", stage="", error="", gen=_sim_job["gen"] + 1)    # 启动中途停止：放弃那次启动
    await asyncio.to_thread(simctl.stop)
    runtime.telemetry.clear()
    _health_cache.clear()
    return {**simctl.status(), "job": dict(_sim_job)}


def _site(name: str) -> dict:
    try:
        return scene.load_site(name)
    except FileNotFoundError as error:
        raise HTTPException(404, "场景不存在") from error


@app.get("/site/{name}")
def get_site(name: str) -> dict:
    site = _site(name)
    for p in site["panels"]:
        p.pop("defect", None)
        p.pop("distractor", None)
    for key in ("people", "decoys", "brief"):       # 救援场景：人员的真实位置与目标特征属于标准答案（太阳方向保留）
        site.pop(key, None)
    return site


@app.get("/site/{name}/basemap")
def get_basemap(name: str) -> dict:
    """网页百度卫星底图的参数：只对国内的真实场地提供。百度浏览器端 AK 由后端运行时下发（受百度 Referer 白名单保护），
    不写进前端代码与仓库。所有要素的 BD-09 换算都用这里返回的同一组参数。"""
    from . import maps
    site = _site(name)
    ak = config.env("BAIDU_MAP_BROWSER_AK")
    if not site.get("location"):
        return {"available": False, "reason": "不是真实场地（使用 PX4 默认坐标）"}
    frame = maps.baidu_frame(site["origin"])
    if not frame["in_china"]:
        return {"available": False, "reason": "境外场地：百度底图数据不可靠，不提供"}
    if not ak:
        return {"available": False, "reason": "没有配置 BAIDU_MAP_BROWSER_AK"}
    return {"available": True, "ak": ak, **frame}


@app.get("/site/{name}/truth")
def get_truth(name: str) -> dict:
    site = _site(name)
    if site.get("scenario") == "rescue":
        return {"defects": [], "people": site["people"], "decoys": site.get("decoys", []), "brief": site.get("brief")}
    return {"defects": [{"panel_id": p["id"], **p["defect"]} for p in site["panels"] if p["defect"]]}


# ---------- 任务 ----------

class NewMission(BaseModel):
    task: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=300)]
    scene: str | None = None
    auto_approve: bool = False


@app.post("/missions")
def create_mission(body: NewMission) -> dict:
    status = simctl.status()
    name = body.scene or status["scene"]
    if not name:
        raise HTTPException(409, "仿真没有运行，请先启动一个场景")
    if status["scene"] != name or not status["px4"]:
        raise HTTPException(409, f"当前仿真场景是 {status['scene']}，与任务场景 {name} 不一致或尚未就绪")
    mid = store.create_mission(body.task, name)
    store.event(mid, "system", "created", f"收到任务：{body.task}")
    if task_queue.busy(exclude=mid):
        # 一架飞机同一时间只执行一个任务：执行中再下达的任务排队，按顺序执行（上下文彼此独立）
        ahead = task_queue.enqueue(mid, body.auto_approve)
        return {"id": mid, "queued": True, "ahead": ahead}
    mission.start(mid, body.task, name, body.auto_approve)
    return {"id": mid, "queued": False}


@app.get("/missions")
def get_missions() -> list[dict]:
    return store.list_missions()


@app.get("/missions/{mid}")
def get_mission(mid: str) -> dict:
    m = store.get_mission(mid)
    if m is None:
        raise HTTPException(404, "任务不存在")
    return {**m, "findings": store.findings(mid), "photos": [{k: v for k, v in p.items() if k != "path"} for p in store.photos(mid)],
            "usage": store.usage_summary(mid)}


class Decision(BaseModel):
    approved: bool


@app.post("/missions/{mid}/approve")
def approve(mid: str, body: Decision) -> dict:
    if store.mission_status(mid) != "awaiting_approval":
        raise HTTPException(409, "任务不在等待确认状态")
    if body.approved and runtime.active_mission:
        raise HTTPException(409, "另一个任务正在执行")
    # 条件更新成功的请求才恢复执行；并发的第二个请求返回 409（深度审查 H05）
    if not store.update_mission(mid, status="planning" if body.approved else "cancelled", only_from=("awaiting_approval",)):
        raise HTTPException(409, "任务已被确认或取消")
    mission.resume(mid, body.approved)
    return {"status": store.mission_status(mid)}


class Reply(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    auto_approve: bool = False


@app.post("/missions/{mid}/reply")
def reply(mid: str, body: Reply) -> dict:
    """回复调度员的追问：在同一个任务里继续（不新开任务，不丢上下文）。"""
    m = store.get_mission(mid)
    if m is None:
        raise HTTPException(404, "任务不存在")
    if m["status"] != "clarify":
        raise HTTPException(409, "这个任务没有在等待补充信息")
    status = simctl.status()
    if status["scene"] != m["scene"] or not status["px4"]:
        raise HTTPException(409, f"当前仿真场景是 {status['scene']}，与任务场景 {m['scene']} 不一致或尚未就绪")
    task = mission.prepare_reply(mid, body.text)
    if task_queue.busy(exclude=mid):
        ahead = task_queue.enqueue(mid, body.auto_approve)
        return {"id": mid, "queued": True, "ahead": ahead}
    mission.restart(mid, task, m["scene"], body.auto_approve)
    return {"id": mid, "queued": False}


CANCEL_GRACE = 15.0      # 取消后给任务自行停下的时间（秒）


@app.post("/missions/{mid}/cancel")
def cancel(mid: str) -> dict:
    store.update_mission(mid, status="cancelled", only_from=("queued", "planning", "awaiting_approval", "running"))
    store.event(mid, "system", "cancel", "用户取消任务；飞行中会立即返航")

    def watchdog():
        # 任务卡在某个等待里（例如连接飞控）时不会检查取消标志：超过宽限时间、飞机又在地面，就强制结束（DR-058）
        if runtime.job_running(mid) and not runtime.telemetry.get("in_air"):
            if runtime.force_stop(mid):
                store.event(mid, "system", "force_stop", f"取消后 {CANCEL_GRACE:.0f} 秒任务仍未停止，已强制结束并释放飞机")
        elif not runtime.job_running(mid):
            runtime.reconcile(store.mission_status)
    threading.Timer(CANCEL_GRACE, watchdog).start()
    return {"status": store.mission_status(mid)}


@app.websocket("/missions/{mid}/events")
async def mission_events(websocket: WebSocket, mid: str, after: int = 0, token: str | None = None) -> None:
    if not _authorized(token):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    try:
        while True:
            for e in await asyncio.to_thread(store.events, mid, after):
                await websocket.send_json({"type": "event", **e})
                after = e["seq"]
            status = await asyncio.to_thread(store.mission_status, mid)
            await websocket.send_json({"type": "telemetry", "status": status, "telemetry": runtime.telemetry})
            if status not in ("queued", "planning", "awaiting_approval", "running") and not await asyncio.to_thread(store.events, mid, after):   # 排队不是终态（审查 M03）
                await websocket.send_json({"type": "end", "status": status})
                break
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        return
    await websocket.close()


@app.get("/photos/{pid}")
def get_photo(pid: int, overlay: bool = Query(False), width: int = Query(960, ge=160, le=1280)) -> Response:
    p = store.get_photo(pid)
    if p is None:
        raise HTTPException(404, "照片不存在")
    frame = Frame.load(Path(p["path"]).with_suffix(""))
    img = frame.image
    if overlay:
        m = store.get_mission(p["mission_id"])
        site = scene.load_site(m["scene"])
        status = {f["panel_id"]: f["status"] for f in store.findings(p["mission_id"])}
        colors = {"confirmed": (230, 40, 40), "suspect": (255, 170, 0), "unverified": (255, 170, 0), "cleared": (60, 160, 255)}
        draw = ImageDraw.Draw(img)
        for panel in site["panels"]:
            pts = [frame.pose.project(tuple(c)) for c in panel["corners"]]
            if all(pts) and all(-20 < u < frame.pose.width + 20 and -20 < v < frame.pose.height + 20 for u, v in pts):
                s = status.get(panel["id"])
                draw.polygon(pts, outline=colors.get(s, (80, 220, 120)), width=4 if s else 1)
    img = img.copy()
    img.thumbnail((width, width))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return Response(buf.getvalue(), media_type="image/jpeg")


class Troubleshoot(BaseModel):
    symptom: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=300)]
    fault: str | None = None          # 仅演示与评测：先在仿真中注入一个已知故障


class SettingsBody(BaseModel):
    vision_thinking: str | None = None
    segment_screen: bool | None = None
    recheck_thinking: bool | None = None


@app.get("/settings")
def get_settings() -> dict:
    return settings.current()


@app.put("/settings")
def put_settings(body: SettingsBody) -> dict:
    try:
        return settings.update({k: v for k, v in body.model_dump().items() if v is not None})
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get("/troubleshoot/faults")
def troubleshoot_faults() -> list[dict]:
    return [{"id": k, "label": v["label"], "symptom": v["symptom"]} for k, v in troubleshoot.FAULTS.items()]


@app.post("/troubleshoot")
def start_troubleshoot(body: Troubleshoot) -> dict:
    status = simctl.status()
    if not status["px4"]:
        raise HTTPException(409, "仿真没有运行，请先启动一个场景")
    if task_queue.busy():
        raise HTTPException(409, "飞机正在执行其他任务；诊断需要实际试飞，请等任务结束后再开始")
    if body.fault and body.fault not in troubleshoot.FAULTS:
        raise HTTPException(422, "未知的故障场景")
    mid = store.create_mission(body.symptom, status["scene"])
    store.update_mission(mid, brief={"type": "troubleshoot", "fault": body.fault})
    store.event(mid, "system", "created", f"收到排障请求：{body.symptom}")
    runtime.submit(troubleshoot.job(mid, body.symptom, body.fault), mid)
    return {"id": mid}


@app.get("/missions/{mid}/score")
def get_score(mid: str) -> dict:
    """与标准答案对照（仿真场景才有答案）：只统计实际巡检过的列。"""
    m = store.get_mission(mid)
    if m is None:
        raise HTTPException(404, "任务不存在")
    result = m.get("result") or {}
    if result.get("scenario") == "rescue":
        from .rescue import scoring as rescue_scoring
        found = result.get("found") or {}
        photo = store.get_photo(found["photo"]) if found.get("photo") else None
        return rescue_scoring.score(scene.load_site(m["scene"]), result, (photo or {}).get("meta"))
    arrays = result.get("inspected_arrays")
    if not arrays:
        raise HTTPException(409, "任务还没有巡检结果")
    reported = {f["panel_id"]: f["type"] for f in store.findings(mid) if f["status"] == "confirmed"}
    return scoring.score(scene.load_site(m["scene"]), reported, arrays)


@app.get("/photos/{pid}/panel/{panel_id}")
def get_panel_tile(pid: int, panel_id: str) -> Response:
    """照片中某块板的正视图（裁切并透视校正），用于发现卡片的缩略图。"""
    p = store.get_photo(pid)
    if p is None:
        raise HTTPException(404, "照片不存在")
    site = scene.load_site(store.get_mission(p["mission_id"])["scene"])
    panel = next((x for x in site["panels"] if x["id"] == panel_id), None)
    if panel is None:
        raise HTTPException(404, "没有这块板")
    tile = vision.rectify(Frame.load(Path(p["path"]).with_suffix("")), panel, (vision.CLOSE_W // 2, vision.CLOSE_H // 2))
    if tile is None:
        raise HTTPException(404, "这块板不在照片中")
    buf = io.BytesIO()
    tile.image.save(buf, "JPEG", quality=88)
    return Response(buf.getvalue(), media_type="image/jpeg")


@app.get("/missions/{mid}/report")
def get_report(mid: str) -> FileResponse:
    path = mission.photo_dir(mid) / "report.html"
    if not path.exists():
        raise HTTPException(404, "报告尚未生成")
    return FileResponse(path, media_type="text/html")


@app.get("/usage")
def usage() -> dict:
    return store.usage_summary()


@app.get("/usage/detail")
def usage_detail(days: int = Query(30, ge=1, le=365)) -> dict:
    return store.usage_detail(days)


@app.get("/stats")
def stats() -> dict:
    return store.overview_stats()


EVAL_RESULTS = config.BACKEND_DIR / "eval" / "results"


@app.get("/evals")
def list_evals() -> list[dict]:
    """评测报告列表（backend/eval/results/*.json），每种评测只返回摘要。"""
    out = []
    for path in sorted(EVAL_RESULTS.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"name": path.stem, "suite": data.get("suite", "gold"), "summary": data.get("summary"),
                    "scenes": data.get("scenes"), "repeat": data.get("repeat"), "model": data.get("model"),
                    "summary_rows": data.get("rows") if data.get("suite") in ("triage", "twin") else None})
    return out


@app.get("/evals/{name}")
def get_eval(name: str) -> dict:
    path = EVAL_RESULTS / f"{name}.json"
    if not path.resolve().is_relative_to(EVAL_RESULTS.resolve()) or not path.exists():
        raise HTTPException(404, "评测报告不存在")
    return json.loads(path.read_text(encoding="utf-8"))
