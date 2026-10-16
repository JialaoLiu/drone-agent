#!/usr/bin/env bash
# 一键启动：仿真场景 + 后端接口（8881）+ 网页（4790，局域网可访问）。
# 用法：scripts/start.sh [场景名，默认 solar_1]；停止：scripts/start.sh stop
set -eo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RELATED_DIR=${DRONE_AGENT_RELATED:-$(cd "$PROJECT_DIR/.." && pwd)/drone-agent-related}
DATA_DIR=${DRONE_AGENT_DATA:-$RELATED_DIR/data}
LOG_DIR="$DATA_DIR/logs"
SCENE=${1:-solar_1}

if [ "$SCENE" = "stop" ]; then
	pkill -f "uvicorn droneagent.api" || true
	pkill -f "vite --host 0.0.0.0 --port 4790" || true
	"$PROJECT_DIR/scripts/sim.sh" stop
	exit 0
fi

if [ ! -d "$(dirname "$DATA_DIR")" ]; then
	echo "数据目录的上级不存在：$DATA_DIR（drone-agent-related 目录是否在仓库旁边？）" >&2
	exit 1
fi
mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR/backend"
if [ ! -f "$DATA_DIR/worlds/$SCENE/site.json" ]; then
	seed="${SCENE#solar_}"
	PYTHONPATH=src uv run python -m droneagent.sim.scene --seed "$seed" --name "$SCENE"
fi
# 先停掉可能残留的仿真与服务（例如录制结束后仍在运行的仿真），否则两个 Gazebo 会抢同一个 PX4 实例
pkill -f "uvicorn droneagent.api" || true
pkill -f "vite --host 0.0.0.0 --port 4790" || true
"$PROJECT_DIR/scripts/sim.sh" stop
"$PROJECT_DIR/scripts/sim.sh" "$SCENE"
PYTHONPATH=src nohup uv run uvicorn droneagent.api:app --host 127.0.0.1 --port 8881 > "$LOG_DIR/api.log" 2>&1 &
cd "$PROJECT_DIR/frontend"
[ -d node_modules ] || npm install --silent
nohup npx vite --host 0.0.0.0 --port 4790 > "$LOG_DIR/web.log" 2>&1 &
sleep 3
IP=$(ipconfig getifaddr en0 2>/dev/null || echo "本机 IP")
echo "已启动。浏览器打开 http://localhost:4790 （同一局域网内：http://$IP:4790）"
echo "仿真约 30 秒后就绪；日志在 $LOG_DIR"
