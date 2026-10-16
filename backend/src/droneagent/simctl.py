"""仿真进程管理：启动、停止、查询当前场景。供网页接口和评测脚本调用。"""
from __future__ import annotations

import json
import re
from pathlib import Path
import subprocess
import time

import httpx

from . import config
from .sim import scene

SCRIPT = config.PROJECT_DIR / "scripts" / "sim.sh"


def scenes() -> list[dict]:
    """场景列表，新生成的在前（网页按此排序、分页）。"""
    out = []
    for d in config.WORLDS_DIR.glob("*/site.json"):
        site = json.loads(d.read_text(encoding="utf-8"))
        out.append({"name": site["name"], "seed": site["seed"], "panels": len(site["panels"]), "arrays": len(site["arrays"]),
                    "scenario": site.get("scenario", "solar"),
                    "location": (site.get("location") or {}).get("name"), "ground": (site.get("ground") or {}).get("source", "procedural"),
                    "created_at": d.stat().st_mtime})
    return sorted(out, key=lambda x: -x["created_at"])


def current_scene() -> str | None:
    """从相机桥进程的命令行读出正在运行的场景。"""
    r = subprocess.run(["pgrep", "-fl", "camera_bridge.py"], capture_output=True, text=True)
    m = re.search(r"--world (\S+)", r.stdout)
    return m.group(1) if m else None


def bridge_ready() -> bool:
    try:
        r = httpx.get(f"{config.CAMERA_BRIDGE_URL}/health", timeout=1.5)
        return r.status_code == 200 and bool(r.json().get("cameras"))
    except (httpx.HTTPError, ValueError):
        return False


def px4_ready() -> bool:
    log = config.DATA_DIR / "logs" / "px4.log"
    return log.exists() and "Startup script returned successfully" in log.read_text(errors="ignore")


def status() -> dict:
    name = current_scene()
    return {"scene": name, "camera": bridge_ready() if name else False, "px4": px4_ready() if name else False}


def stop() -> None:
    subprocess.run([str(SCRIPT), "stop"], capture_output=True, timeout=40)
    time.sleep(2)


def missing_textures(name: str) -> list[str]:
    """场景文件引用、但磁盘上不存在的贴图（例如迁移目录后仍指向旧位置）。"""
    sdf = config.WORLDS_DIR / name / f"{name}.sdf"
    if not sdf.exists():
        return []
    return [p for p in re.findall(r"<albedo_map>([^<]+)</albedo_map>", sdf.read_text(encoding="utf-8")) if not Path(p).exists()]


def start(name: str, wait: float = 120.0, on_stage=None, cancelled=None) -> dict:
    """启动指定场景（先停止正在运行的仿真）。场景不存在时按名称中的种子生成。
    on_stage(文字)：报告当前阶段，供网页显示进度；cancelled()：返回真时放弃等待（用户中途停止）。"""
    stage = on_stage or (lambda _text: None)
    if not (config.WORLDS_DIR / name / "site.json").exists():
        m = re.fullmatch(r"solar_(\d+)", name)
        if not m:
            raise ValueError(f"场景 {name} 不存在")
        scene.generate(int(m.group(1)), name)
    missing = missing_textures(name)
    if missing:
        # 贴图缺失时 Gazebo 不报错，光伏板会渲染成一片灰色，视觉判断全部失真（DR-054），所以启动前拦下
        raise RuntimeError(f"场景 {name} 引用的 {len(missing)} 个贴图文件不存在（例如 {missing[0]}），请重新生成该场景")
    stage("停止旧的仿真进程")
    stop()
    log = config.DATA_DIR / "logs" / "px4.log"
    log.unlink(missing_ok=True)
    stage("启动飞控（PX4）与仿真器（Gazebo）")
    r = subprocess.run([str(SCRIPT), name], capture_output=True, timeout=120)
    if r.returncode != 0:
        output = (r.stderr or r.stdout).decode("utf-8", errors="replace").strip()
        raise RuntimeError(output or f"启动脚本返回 {r.returncode}")
    deadline = time.time() + wait
    stage("等待飞控启动完成")
    while time.time() < deadline:
        if cancelled and cancelled():
            raise RuntimeError("启动已取消")
        px4, cam = px4_ready(), bridge_ready()
        if px4 and cam:
            return status()
        stage("飞控已启动，等待相机" if px4 else "等待飞控启动完成")
        time.sleep(2)
    raise TimeoutError("仿真在规定时间内没有就绪，请查看日志 " + str(config.DATA_DIR / "logs"))
