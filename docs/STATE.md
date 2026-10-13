# 项目状态

更新：2026-10-13

## 运行

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| 网页 | http://localhost:4790 ，局域网 http://192.168.1.112:4790 | Vite，`--host 0.0.0.0` 开放给局域网 |
| 后端 | http://127.0.0.1:8881 | FastAPI，只监听本机，网页经 /api 代理访问 |
| 相机桥 | http://127.0.0.1:8874 | 在 Gazebo 的 Python 环境中运行，提供照片与相机位姿 |
| 仿真 | PX4 SITL + Gazebo 8.10（无窗口） | MAVLink UDP 14540，同一时间只能有一个程序占用 |

一键启动：`scripts/start.sh [场景名]`；停止：`scripts/start.sh stop`。运行前需挂载外置硬盘上的 `/Volumes/LJL4T/DevData.sparsebundle`。

## 数据落在哪里

| 位置 | 内容 | 是否提交 |
| --- | --- | --- |
| `/Volumes/DevData/px4/PX4-Autopilot` | PX4 源码、编译产物、Gazebo 环境（约 8 GB） | 否 |
| `/Volumes/DevData/drone-agent/worlds/<场景>/` | 生成的场景：SDF、贴图、`site.json`（含标准答案） | 否（按种子可重建） |
| `/Volumes/DevData/drone-agent/photos/<任务>/` | 任务照片、位姿、HTML 报告 | 否 |
| `/Volumes/DevData/drone-agent/captures/<场景>/` | 评测用的完整录制（每列整列照片 + 每块板近照） | 否 |
| `/Volumes/DevData/drone-agent/app.db` | 任务、事件、照片记录、发现、模型调用 | 否 |
| `/Volumes/DevData/drone-agent/checkpoints.db` | LangGraph 检查点（等待确认的任务可恢复） | 否 |
| `backend/eval/results/` | 评测报告与明细 | 是 |
| `backend/.env` | 模型密钥 | 否 |

## 当前评测

- 巡检（3 场景 × 2 轮）：全部复核时检出 93.3%、精确 98.2%；只复核疑似时精确 100%、检出 75%；只初筛时精确 66.7%。
- 任务理解与规划：20 题，2 轮均 20/20。
- 排障：4 个场景，两次运行均 4/4；规则基线 0/4。

详见 README 与 `backend/eval/results/`。

## 已完成

- 仿真环境：PX4 SITL + Gazebo（macOS 无窗口）；双相机巡检机型；按种子生成的光伏场景，含缺陷、干扰与标准答案
- 多 Agent 协同（LangGraph）：
  - 调度员、规划员、安全员（确定性规则，拥有否决权）
  - 人工确认（中断与恢复）
  - 飞手、巡检员、报告员、诊断员
  - 多架次换电池；执行中按电量调整
  - 取消、一键返航、异常自动返航
- 视觉：相机位姿投影、透视校正、编号拼图；初筛与复核的裁决规则（实时与评测共用）
- 排障：飞行日志指标；与已知良好配置对比；对照实验；试飞中的动作超时记为观察结果
- 网页：场地平面图、实时位置与遥测、计划确认、协作记录、照片与标注、发现与标准答案对照、排障数据、HTML 报告
- MCP 服务；离线测试 24 个；CI（后端测试、前端构建）
- 评测：场景录制与离线回放、任务理解与规划、排障（含规则基线）

## 待办

1. 用真实巡检照片评测视觉部分（例如公开的绝缘子、光伏缺陷数据集，需确认许可证）
2. 对比 OpenAI 视觉模型（需要 OpenAI Key）
3. 传感器硬件故障（GPS、气压计）的注入与排障（依赖仿真器支持，需验证）
4. 部署：Docker 镜像与登录、多用户
