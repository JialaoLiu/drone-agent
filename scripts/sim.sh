#!/usr/bin/env bash
# 启动仿真：Gazebo 服务端（无窗口）+ PX4 SITL（巡检机型 x500_inspect）+ 相机桥。
# 用法：
#   scripts/sim.sh <场景名>     场景由 droneagent.sim.scene 生成，位于 $DRONE_AGENT_DATA/worlds/<场景名>/
#   scripts/sim.sh stop
# 注意（macOS）：
#   - Gazebo 组播发现会报 “No route to host”，所以固定 GZ_IP=127.0.0.1；
#   - 不要加 --render-engine-server-api-backend metal，会卡住相机渲染；
#   - 日志中的 “Unable to load Ogre Plugin … Rendering will not be possible” 可以忽略，相机正常。
set -eo pipefail   # 不用 -u：PX4 的环境脚本引用了未定义的变量

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PX4_DIR=${PX4_DIR:-/Volumes/DevData/px4/PX4-Autopilot}
DATA_DIR=${DRONE_AGENT_DATA:-/Volumes/DevData/drone-agent}
LOG_DIR="$DATA_DIR/logs"
WORLD=${1:-solar_1}

if [ "$WORLD" = "stop" ]; then
	pkill -f "bin/px4 -d" || true
	pkill -f "gz sim" || true
	pkill -f "camera_bridge.py" || true
	exit 0
fi

WORLD_FILE="$DATA_DIR/worlds/$WORLD/$WORLD.sdf"
if [ ! -f "$WORLD_FILE" ]; then
	echo "找不到场景 $WORLD_FILE，请先运行：uv run python -m droneagent.sim.scene --seed <种子> --name $WORLD" >&2
	exit 1
fi

mkdir -p "$LOG_DIR"
ulimit -S -n 4096
# shellcheck disable=SC1091
source "$PX4_DIR/.venv/bin/activate"
# shellcheck disable=SC1091
source "$PX4_DIR/build/px4_sitl_default/gz_env.sh"
export GZ_IP=127.0.0.1
# 本项目的机型目录优先；机型里引用的 x500、gimbal 网格仍从 PX4 目录解析。
# 飞机由场景文件直接放置（名为 x500_inspect_0），PX4 只负责连接，不再自己生成机型。
export GZ_SIM_RESOURCE_PATH="$PROJECT_DIR/sim/models:$GZ_SIM_RESOURCE_PATH"

gz sim -s -r --verbose="${GZ_VERBOSE:-1}" "$WORLD_FILE" > "$LOG_DIR/gz.log" 2>&1 &
for _ in $(seq 1 60); do
	gz topic -l 2>/dev/null | grep -q "^/world/$WORLD/clock" && break
	sleep 1
done

# PX4 日志（.ulg）写到数据目录，便于排障模块读取
cd "$PX4_DIR/build/px4_sitl_default"
PX4_GZ_STANDALONE=1 PX4_GZ_WORLD="$WORLD" PX4_GZ_MODEL_NAME=x500_inspect_0 PX4_SYS_AUTOSTART=4019 \
	nohup ./bin/px4 -d > "$LOG_DIR/px4.log" 2>&1 &

"$CONDA_PREFIX/bin/python" "$PROJECT_DIR/sim/camera_bridge.py" --world "$WORLD" --model x500_inspect_0 \
	> "$LOG_DIR/camera_bridge.log" 2>&1 &
echo "仿真已启动：场景 $WORLD，日志 $LOG_DIR"
