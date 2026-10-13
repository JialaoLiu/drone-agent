"""MCP 服务：让 Claude Code、Cursor 等 Agent 通过标准协议下达巡检任务、查询结果。

运行（stdio）：PYTHONPATH=src uv run python -m droneagent.mcp_server
在 Claude Code 中注册：
  claude mcp add drone-agent -- env PYTHONPATH=<backend>/src uv --directory <backend> run python -m droneagent.mcp_server

工具通过后端接口执行（后端持有唯一的飞控连接），后端需要先启动。任务同样经过安全员校验；
为安全起见，通过 MCP 下达的任务默认需要在网页上人工确认后才会起飞，除非显式传入 auto_approve。
"""
from __future__ import annotations

import os

import httpx
from mcp.server.mcpserver import MCPServer

API = os.environ.get("DRONE_AGENT_API", "http://127.0.0.1:8881")
TOKEN = os.environ.get("DRONE_AGENT_ACCESS_TOKEN", "")

server = MCPServer(
    name="drone-agent",
    instructions="无人机光伏巡检（仿真）。先用 sim_status 确认仿真已运行，再用 start_inspection 下达任务，"
                 "用 get_mission 查看进度与发现；任务需要人工确认时会停在 awaiting_approval。",
)


def _call(method: str, path: str, **kwargs) -> dict:
    r = httpx.request(method, f"{API}{path}", headers={"X-Access-Token": TOKEN}, timeout=30, **kwargs)
    if r.status_code >= 400:
        detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text
        raise ValueError(f"{r.status_code}：{detail}")
    return r.json()


@server.tool(description="查看仿真状态：当前场景、飞控与相机是否就绪、飞机遥测、是否有任务在执行。")
def sim_status() -> dict:
    return _call("GET", "/sim")


@server.tool(description="查看场地信息：光伏板列、禁飞区、障碍物、电子围栏与限高。")
def site_info(scene: str = "") -> dict:
    name = scene or _call("GET", "/sim")["scene"]
    if not name:
        raise ValueError("仿真没有运行")
    site = _call("GET", f"/site/{name}")
    return {k: site[k] for k in ("name", "geofence", "max_altitude", "min_altitude", "no_fly_zones", "obstacles", "arrays")}


@server.tool(description="用中文下达巡检任务，例如“检查 A、C 两列有没有破损”。auto_approve 为 false 时需要在网页上确认计划。")
def start_inspection(task: str, auto_approve: bool = False) -> dict:
    return _call("POST", "/missions", json={"task": task, "auto_approve": auto_approve})


@server.tool(description="查看任务状态、计划、发现（confirmed 为确认缺陷）与报告摘要。")
def get_mission(mission_id: str) -> dict:
    m = _call("GET", f"/missions/{mission_id}")
    result = m.get("result") or {}
    return {"status": m["status"], "task": m["task"], "plan": m.get("plan"),
            "findings": [{k: f[k] for k in ("panel_id", "status", "type", "confidence", "evidence")} for f in m["findings"]],
            "summary": result.get("summary"), "skipped_arrays": result.get("skipped_arrays"),
            "report": f"{API}/missions/{mission_id}/report" if result.get("report_path") else None}


@server.tool(description="确认或拒绝一个等待确认的任务计划。")
def approve_mission(mission_id: str, approved: bool) -> dict:
    return _call("POST", f"/missions/{mission_id}/approve", json={"approved": approved})


@server.tool(description="取消任务；飞行中会立即返航。")
def cancel_mission(mission_id: str) -> dict:
    return _call("POST", f"/missions/{mission_id}/cancel")


@server.tool(description="飞行排障：描述问题（例如“悬停时抖动”），诊断员会在仿真中试飞和做对照实验，用 get_mission 查看结论。")
def troubleshoot(symptom: str) -> dict:
    return _call("POST", "/troubleshoot", json={"symptom": symptom})


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
