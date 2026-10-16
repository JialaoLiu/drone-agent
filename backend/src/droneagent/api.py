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

from . import config, scoring, simctl, store, vision
from . import health as preflight_check
from .agents import mission, runtime, troubleshoot
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
    seed: int = Field(ge=1, le=100000)
    defect_rate: float = Field(0.12, ge=0.02, le=0.4)
    location: Place | None = None         # 真实场地（来自 /places 的检索结果）


@app.get("/scenes")
def get_scenes() -> list[dict]:
    return simctl.scenes()


@app.post("/scenes")
def create_scene(body: NewScene) -> dict:
    if body.location:
        out = scene.generate(body.seed, f"site_{body.seed}", body.defect_rate, location=body.location.model_dump())
    else:
        out = scene.generate(body.seed, f"solar_{body.seed}", body.defect_rate)
    return {"name": out.name, "ground": scene.load_site(out.name).get("ground")}


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
    status = simctl.status()
    if status["px4"] and not runtime.telemetry:
        runtime.submit(runtime.flight())          # 仿真就绪后建立连接，开始接收遥测
    return {**status, "telemetry": runtime.telemetry, "active_mission": runtime.active_mission}


_health_cache: dict = {}


@app.get("/sim/health")
async def sim_health() -> dict:
    """飞行前检查（与安全员起飞前执行的是同一套规则）。网页顶部的状态指示用；结果缓存 10 秒。"""
    if not simctl.status()["px4"]:
        return {"ok": None, "items": [], "advice": "仿真未启动"}
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


@app.post("/sim/start")
async def sim_start(body: SimStart) -> dict:
    if runtime.active_mission:
        raise HTTPException(409, "有任务正在执行，不能切换场景")
    try:
        status = await asyncio.to_thread(simctl.start, body.scene)
    except (ValueError, RuntimeError, TimeoutError) as error:
        raise HTTPException(500, str(error)) from error
    runtime.telemetry.clear()
    runtime.submit(runtime.flight())
    return status


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
    await asyncio.to_thread(simctl.stop)
    runtime.telemetry.clear()
    return simctl.status()


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


@app.get("/site/{name}/ground")
def get_ground(name: str):
    """真实场地的卫星影像（用作网页底图）；程序生成的贴图不提供。"""
    site = _site(name)
    if (site.get("ground") or {}).get("source") != "satellite":
        raise HTTPException(404, "该场景没有卫星影像")
    return FileResponse(config.WORLDS_DIR / site["name"] / "textures" / "ground.png", media_type="image/png")


@app.get("/site/{name}/truth")
def get_truth(name: str) -> dict:
    site = _site(name)
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
    if runtime.active_mission:
        raise HTTPException(409, "另一个任务正在执行")
    mid = store.create_mission(body.task, name)
    store.event(mid, "system", "created", f"收到任务：{body.task}")
    mission.start(mid, body.task, name, body.auto_approve)
    return {"id": mid}


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


@app.post("/missions/{mid}/cancel")
def cancel(mid: str) -> dict:
    store.update_mission(mid, status="cancelled", only_from=("planning", "awaiting_approval", "running"))
    store.event(mid, "system", "cancel", "用户取消任务；飞行中会立即返航")
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
            if status not in ("planning", "awaiting_approval", "running") and not await asyncio.to_thread(store.events, mid, after):
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


@app.get("/troubleshoot/faults")
def troubleshoot_faults() -> list[dict]:
    return [{"id": k, "label": v["label"], "symptom": v["symptom"]} for k, v in troubleshoot.FAULTS.items()]


@app.post("/troubleshoot")
def start_troubleshoot(body: Troubleshoot) -> dict:
    status = simctl.status()
    if not status["px4"]:
        raise HTTPException(409, "仿真没有运行，请先启动一个场景")
    if runtime.active_mission:
        raise HTTPException(409, "另一个任务正在执行")
    if body.fault and body.fault not in troubleshoot.FAULTS:
        raise HTTPException(422, "未知的故障场景")
    mid = store.create_mission(body.symptom, status["scene"])
    store.update_mission(mid, brief={"type": "troubleshoot", "fault": body.fault})
    store.event(mid, "system", "created", f"收到排障请求：{body.symptom}")
    runtime.submit(troubleshoot.job(mid, body.symptom, body.fault))
    return {"id": mid}


@app.get("/missions/{mid}/score")
def get_score(mid: str) -> dict:
    """与标准答案对照（仿真场景才有答案）：只统计实际巡检过的列。"""
    m = store.get_mission(mid)
    if m is None:
        raise HTTPException(404, "任务不存在")
    arrays = (m.get("result") or {}).get("inspected_arrays")
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
                    "summary_rows": data.get("rows") if data.get("suite") == "triage" else None})
    return out


@app.get("/evals/{name}")
def get_eval(name: str) -> dict:
    path = EVAL_RESULTS / f"{name}.json"
    if not path.resolve().is_relative_to(EVAL_RESULTS.resolve()) or not path.exists():
        raise HTTPException(404, "评测报告不存在")
    return json.loads(path.read_text(encoding="utf-8"))
