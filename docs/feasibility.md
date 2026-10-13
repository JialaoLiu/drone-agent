# 可行性验证（2026-10-13）

本机：Apple M4、16 GB、macOS，无 Docker。大文件在外置盘 APFS 映像 `/Volumes/DevData`。

| 项目 | 结果 | 说明 |
| --- | --- | --- |
| PX4 源码 | 通过 | v1.18.0-beta1（main），含子模块 2.8 GB，`/Volumes/DevData/px4/PX4-Autopilot` |
| 工具链 | 通过 | Homebrew 安装构建依赖（跳过只用于真机的 arm-gcc）；需额外安装 `pkgconf`，否则 CMake 找不到 PkgConfig |
| Gazebo | 通过 | 8.10（Harmonic 系列），由 PX4 的 pixi 锁定环境安装，2.5 GB；无需 XQuartz（不开窗口） |
| SITL 编译 | 通过 | `make px4_sitl` 首次 73 秒 |
| Gazebo 与 PX4 互相发现 | 需配置 | macOS 上组播发现报 “No route to host”，需 `GZ_IP=127.0.0.1` |
| 相机渲染 | 通过（有坑） | 日志里的 “Unable to load Ogre Plugin … Rendering will not be possible” 可忽略，图像正常；**不能**加 `--render-engine-server-api-backend metal`，会导致相机和整个仿真卡住 |
| 仿真速度 | 通过 | 实时（3 秒墙钟推进 2.97 秒仿真时间） |
| MAVSDK | 通过 | Python 包 v4 已改为原生绑定，API 与 v3 不同（`mavsdk.asyncio`，插件由 `ActionAsync(system)` 等构造） |
| 飞行闭环 | 通过 | 连接、等待可解锁、起飞到 15 m、云台俯仰（-90°/-30°）、取 1280×720 图像、飞往目标点、返航，共约 32 秒 |
| 取图 | 通过 | Gazebo 自带 Python 绑定 `gz.transport13`（只在 pixi 环境的 Python 中可用），订阅 `/world/<世界>/model/x500_gimbal_0/link/camera_link/sensor/camera/image` |
| 视觉模型 | 通过 | `deepseek-v4-flash-vision-exp` 在合成的光伏阵列图上正确指出裂纹所在行列，2.6 秒，约 800 Token |

启动与停止：`scripts/sim.sh [世界名]`、`scripts/sim.sh stop`。

待做：默认世界是空白灰色地面，需要建巡检场景（地面纹理、光伏板、杆塔、带缺陷贴图）；起落架会进入画面边缘，报告图片需裁切或调整云台角度。
