"""仿真进程管理：启动、停止、查询当前场景。供网页接口和评测脚本调用。"""
from __future__ import annotations

import json
import re
import subprocess
import time

import httpx

from . import config
from .sim import scene

SCRIPT = config.PROJECT_DIR / "scripts" / "sim.sh"


def scenes() -> list[dict]:
    out = []
    for d in sorted(config.WORLDS_DIR.glob("*/site.json")):
        site = json.loads(d.read_text(encoding="utf-8"))
        out.append({"name": site["name"], "seed": site["seed"], "panels": len(site["panels"]), "arrays": len(site["arrays"]),
                    "location": (site.get("location") or {}).get("name"), "ground": (site.get("ground") or {}).get("source", "procedural")})
    return out


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


def start(name: str, wait: float = 120.0) -> dict:
    """启动指定场景（先停止正在运行的仿真）。场景不存在时按名称中的种子生成。"""
    if not (config.WORLDS_DIR / name / "site.json").exists():
        m = re.fullmatch(r"solar_(\d+)", name)
        if not m:
            raise ValueError(f"场景 {name} 不存在")
        scene.generate(int(m.group(1)), name)
    stop()
    log = config.DATA_DIR / "logs" / "px4.log"
    log.unlink(missing_ok=True)
    r = subprocess.run([str(SCRIPT), name], capture_output=True, timeout=120)
    if r.returncode != 0:
        output = (r.stderr or r.stdout).decode("utf-8", errors="replace").strip()
        raise RuntimeError(output or f"启动脚本返回 {r.returncode}")
    deadline = time.time() + wait
    while time.time() < deadline:
        if px4_ready() and bridge_ready():
            return status()
        time.sleep(2)
    raise TimeoutError("仿真在规定时间内没有就绪，请查看日志 " + str(config.DATA_DIR / "logs"))
