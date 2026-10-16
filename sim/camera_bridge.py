"""相机桥：订阅 Gazebo 的相机图像和实体位姿，通过本机 HTTP 提供“最新一帧 + 拍摄时的相机位姿”。

必须用 PX4 的 Gazebo 环境（pixi）里的 Python 运行：gz.transport13 / gz.msgs10 只在那里可用；
后端（uv 环境）通过 HTTP 取图，两边的 Python 版本互不影响。只用标准库。

GET /health                     -> {"cameras": {...最近一帧的仿真时间}}
GET /frame?camera=wide|zoom     -> 原始 RGB 字节；响应头带宽、高、相机位姿（世界坐标）与仿真时间

救援场景另有 --crowd <crowd.json>：按路线移动人物（每 0.2 秒调用一次 set_pose_vector），
人物（名字以 person_ 开头）的位姿也附在每一帧里，作为“拍照那一刻人在哪里”的标准答案。
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import math

from gz.msgs10.boolean_pb2 import Boolean
from gz.msgs10.image_pb2 import Image
from gz.msgs10.pose_v_pb2 import Pose_V
from gz.transport13 import Node

LOCK = threading.Lock()
FRAMES: dict[str, dict] = {}
POSES: dict[str, dict] = {}


def _pose(p) -> dict:
    return {"position": [p.position.x, p.position.y, p.position.z],
            "orientation": [p.orientation.w, p.orientation.x, p.orientation.y, p.orientation.z]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--world", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--port", type=int, default=8874)
    parser.add_argument("--crowd", help="救援场景：人物路线文件")
    args = parser.parse_args()
    node = Node()
    base = f"/world/{args.world}/model/{args.model}/link/camera_link/sensor"

    def on_image(name):
        def cb(msg: Image):
            with LOCK:
                FRAMES[name] = {"width": msg.width, "height": msg.height, "data": msg.data,
                                "sim_time": msg.header.stamp.sec + msg.header.stamp.nsec / 1e9,
                                "pose": dict(POSES)}
        return cb

    def on_pose(msg: Pose_V):
        with LOCK:
            for p in msg.pose:
                if p.name in (args.model, "camera_link") or p.name.startswith("person_"):
                    POSES[p.name] = _pose(p)

    for name in ("wide", "zoom"):
        node.subscribe(Image, f"{base}/camera_{name}/image", on_image(name))
    node.subscribe(Pose_V, f"/world/{args.world}/pose/info", on_pose)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/health":
                with LOCK:
                    body = json.dumps({"cameras": {k: v["sim_time"] for k, v in FRAMES.items()},
                                       "poses": POSES}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
                return
            if url.path == "/frame":
                camera = parse_qs(url.query).get("camera", ["wide"])[0]
                with LOCK:
                    frame = FRAMES.get(camera)
                if frame is None:
                    self.send_response(503)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("X-Width", str(frame["width"]))
                self.send_header("X-Height", str(frame["height"]))
                self.send_header("X-Sim-Time", str(frame["sim_time"]))
                self.send_header("X-Poses", json.dumps(frame["pose"]))
                self.end_headers()
                self.wfile.write(frame["data"])
                return
            self.send_response(404)
            self.end_headers()

    if args.crowd:
        threading.Thread(target=_crowd_loop, args=(node, args.world, args.crowd), daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"camera bridge on 127.0.0.1:{args.port}, topics under {base}", flush=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    while True:
        time.sleep(3600)


def _along(path: list, dist: float) -> tuple[float, float, float]:
    """沿折线往返行走 dist 米后的位置与朝向。"""
    segs = [(path[i], path[i + 1], math.dist(path[i], path[i + 1])) for i in range(len(path) - 1)]
    total = sum(s[2] for s in segs)
    if total <= 0:
        return path[0][0], path[0][1], 0.0
    d = dist % (2 * total)
    forward = d <= total
    d = d if forward else 2 * total - d
    for a, b, length in segs:
        if d <= length or (a, b, length) == segs[-1]:
            t = min(1.0, d / length) if length else 0.0
            x, y = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
            yaw = math.atan2(b[1] - a[1], b[0] - a[0]) + (0 if forward else math.pi)
            return x, y, yaw
        d -= length
    return path[-1][0], path[-1][1], 0.0


def _crowd_loop(node: Node, world: str, crowd_file: str) -> None:
    with open(crowd_file, encoding="utf-8") as f:
        crowd = json.load(f)
    service = f"/world/{world}/set_pose_vector"
    t0 = None
    while True:
        with LOCK:
            sim_time = max((v["sim_time"] for v in FRAMES.values()), default=None)
        if sim_time is None:            # 仿真还没出第一帧：等
            time.sleep(0.5)
            continue
        t0 = sim_time if t0 is None else t0
        elapsed = sim_time - t0          # 按仿真时间走，仿真变慢时人也同步变慢
        req = Pose_V()
        for p in crowd:
            x, y, yaw = _along(p["path"], p["speed"] * elapsed)
            pose = req.pose.add()
            pose.name = p["model"]
            pose.position.x, pose.position.y, pose.position.z = x, y, p.get("z", 0.0)
            pose.orientation.w, pose.orientation.z = math.cos(yaw / 2), math.sin(yaw / 2)
        try:
            node.request(service, req, Pose_V, Boolean, 1000)
        except Exception as error:      # noqa: BLE001  移动失败不影响取图
            print(f"crowd: {error}", flush=True)
        time.sleep(0.2)


if __name__ == "__main__":
    main()
