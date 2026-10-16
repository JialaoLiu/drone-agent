"""项目路径与运行配置。所有可调项都来自环境变量（backend/.env），代码里不写死本机路径。"""
from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent


def _load_env_file() -> None:
    """读取 backend/.env（不覆盖已存在的环境变量）。不依赖第三方库。"""
    path = BACKEND_DIR / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env_file()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


# 仿真：PX4 源码目录、日志目录（大文件放外置盘映像）
# 不上传仓库的内容（PX4、运行数据、数据集）放在仓库旁边的 drone-agent-related 目录
RELATED_DIR = Path(env("DRONE_AGENT_RELATED", str(Path(__file__).resolve().parents[3].parent / "drone-agent-related")))
PX4_DIR = Path(env("PX4_DIR", str(RELATED_DIR / "px4" / "PX4-Autopilot")))
DATA_DIR = Path(env("DRONE_AGENT_DATA", str(RELATED_DIR / "data")))
SIM_DIR = PROJECT_DIR / "sim"
MODELS_DIR = SIM_DIR / "models"
WORLDS_DIR = DATA_DIR / "worlds"          # 生成的巡检场景（SDF、贴图、标准答案）
PHOTOS_DIR = DATA_DIR / "photos"
APP_DB = Path(env("DRONE_AGENT_DB", str(DATA_DIR / "app.db")))
CHECKPOINT_DB = DATA_DIR / "checkpoints.db"

# MAVLink：PX4 SITL 的 Onboard 链路
MAVLINK_URL = env("MAVLINK_URL", "udpin://0.0.0.0:14540")
CAMERA_BRIDGE_URL = env("CAMERA_BRIDGE_URL", "http://127.0.0.1:8874")

# 模型：规划与对话用文字模型，看图用视觉模型；接口均为 OpenAI 兼容格式
LLM_API_KEY = env("LLM_API_KEY")
LLM_BASE_URL = env("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = env("LLM_MODEL", "deepseek-chat")
VISION_PROVIDER = env("VISION_PROVIDER", "deepseek")          # deepseek | openai
# 视觉模型的推理过程：on 初筛与复核都开；off 都关；screen 只在初筛开（复核关）。关闭可大幅减少输出 Token
VISION_THINKING_MODE = env("VISION_THINKING", "off")         # 2026-10-16 对比实验：关闭后费用省 75%–86%，复核后的检出率、精确率不变
VISION_THINKING = VISION_THINKING_MODE != "off"                 # 初筛是否开启推理
VISION_THINKING_VERIFY = VISION_THINKING_MODE == "on"           # 复核是否开启推理
# 初筛用变焦分段照片（每块板约 200 像素）还是只用广角整列照片（约 52 像素）。segments 评测：初筛覆盖 84.5% → 100%
SEGMENT_SCREEN = env("SEGMENT_SCREEN", "1") == "1"
SCREEN_SHEET = int(env("SCREEN_SHEET", "10"))                    # 初筛每张拼图的板数
SCREEN_EVIDENCE = env("SCREEN_EVIDENCE", "1") == "1"             # 初筛是否逐块写证据
# “再看一次”时是否开启推理。2026-10-16 recheck 评测第一版显示开推理多纠正 2 块，但那 3 块都是一眼可见的缺失，
# 实时任务里直接确认、不做近照复核；按实时流程重算，三种做法结论完全相同，所以默认关闭（工程日志 37）
VISION_THINKING_RECHECK = env("VISION_THINKING_RECHECK", "off") != "off"
TRIAGE_ENABLED = env("TRIAGE_ENABLED", "0") == "1"             # 初筛前用本地分类器过滤明显正常的板（需先运行 evaluate triage 训练）
VISION_MODEL = env("VISION_MODEL", "deepseek-v4-flash-vision-exp")
OPENAI_API_KEY = env("OPENAI_API_KEY")
OPENAI_BASE_URL = env("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_VISION_MODEL = env("OPENAI_VISION_MODEL", "gpt-4.1-mini")


def ensure_dirs() -> None:
    for path in (DATA_DIR, WORLDS_DIR, PHOTOS_DIR):
        path.mkdir(parents=True, exist_ok=True)
