# 代码审查报告（独立复审）

> ## 本文件的约定（先读）
>
> **文件名固定为 `docs/reviews/review-independent.md`，每轮审查原地覆盖，不再新建带日期的副本。**
>
> 每轮按这个顺序写，不要改结构：
>
> 1. **§ 上一轮复核结果** —— 上期每条问题逐条给 `已修复 / 未修复 / 部分修复`，附核对方式与证据行号。**这一步先做，没查完不动新代码。**
> 2. **§ 本轮新发现** —— 按 高 / 中 / 低 分组，每条含文件:行、类别、证据、修法。
> 3. **§ 本轮范围与覆盖** —— 起止提交、文件数、覆盖率、跑过的验证命令与结果。
> 4. **§ 诚实说明** —— 没复现的、没跑的、没读的。
>
> 上一轮的历史结论不保留在本文件里；需要追溯请看同目录下带日期的归档（`2026-10-17-review.md`、`2026-10-17-review-7f12596.md`）。

---

## 0. 上一轮复核结果

**本轮是第一轮，无上期条目。下一轮从这里开始逐条核对：**

| # | 上一轮问题 | 位置 | 复核方法 |
|---|---|---|---|
| H1 | `_confirm_altitude` 用 `check_plan` 默认 `start=(0,0,0)`，进场航段未对障碍物校验 | `agents/rescue.py:165-171`、`:211-214` | 看是否传入真实 `start=(f.state.x, f.state.y, f.state.z)`，是否改用 `check_segment` 校验实际航段 |
| M1 | `_apply_defaults` 后台化，`RTL_RETURN_ALT` 可能起飞前未写入 | `flight.py:29,146,148-153` | 看 `RTL_RETURN_ALT` 是否回到 await 路径，或起飞前是否 `await` 参数就绪 |
| M2 | `color_key` 产出的「棕」不在 `COLOR_KEYS` 里，褐色永远匹配不上 | `rescue/roles.py:27,33` | 看 `COLOR_KEYS` 是否含「棕」，或该 replace 是否已删 |
| M3 | `rescue/evaluate.py` 单场景失败后不清理，后续种子连锁 409 | `rescue/evaluate.py` `main()` 异常分支 | 看是否加了 cancel + `/sim/stop` 清理 |
| L1 | `in_water` 直接确认，依赖「只有 target 落水」的隐式不变量 | `agents/rescue.py:197,201` | 看是否加断言/注释/测试固定该不变量 |
| L2 | `_own_shadow` 固定 2.5 米半径会误杀站在影子落点上的人 | `agents/rescue.py:155` | 看半径是否随高度与仰角变化，或是否记录过滤事件 |
| L3 | `get_param` float 超时后回退 int，最坏 24 秒 | `flight.py:318-322` | 看是否先 `except FlightError: raise` 再回退 |
| L4 | `fresh` 重连 `destroy()` 失败被吞，实例已 pop → 泄漏 | `flight.py:120-125` | 看失败时是否记 warning |

---

## 1. 本轮概要

日期：2026-10-17
审查范围：`e3d2e91..7f12596`（救援场景引入 → 晨间报告），**只看后端逻辑与相关前端改动**
审查方式：`ocr delegate preview` / `ocr delegate rule` 定范围与规则；diff 逐行阅读；关键路径回读完整上下文验证
基线验证：`cd backend && uv run python -m pytest -q` → **91 passed in 9.89s**

### 结论

**有 1 个高严重度问题：`_confirm_altitude` 的安全校验查的是错误路径，低空确认的进场航段没有对障碍物做校验。**

其余 2 个中、4 个低。工程纪律（只增不减 safety、真实顺序提交、失败路径有上限）整体保持得很好。

---

## 一、高严重度

### H1 · `_confirm_altitude` 的安全校验用了错误的起点，低空进场段未校验障碍物

- 文件：`backend/src/droneagent/agents/rescue.py`
- 行：165–171（校验），211–214（实际调用）
- 类别：security（安全边界）

```python
def _confirm_altitude(site, item, search_alt):
    for alt in (max(site["min_altitude"], 9.0), 12.0, search_alt):
        step = survey.Step("confirm", ..., (item["x"], item["y"], alt), 0.0, -90.0, "wide")
        if not safety.check_plan(site, [step], battery=100.0, reserve=0.0):
            return alt
    return search_alt
```

`safety.check_plan` 的签名是 `check_plan(site, steps, battery=100.0, reserve=25.0, start=(0.0, 0.0, 0.0))`。这里**没有传 `start`**，所以它校验的是「从坐标原点 → (x, y, alt)」这条段，**不是从飞机当前位置 → 该点**。

具体后果：

1. **起点错误**：飞机当时在搜索高度（通常 18–30 米），校验却从 `(0,0,0)` 出发。原点附近的障碍物分布与实际出发点无关，这个校验既可能漏掉真实障碍，也可能因为原点附近的障碍而**误拒绝一个本来安全的高度**，然后退到 `search_alt`，让低空确认失效。
2. **障碍物完全没查**：`_shoot` 里只做了 `survey.detour(...)`（只避禁飞区），`f.goto(*step.position)` 是 PX4 的直线 3D 飞行。而 `_confirm_altitude` 选的 9 米、12 米**低于代码自己注释里写的「树高可达 12 米」**。进场时如果航段穿过树的位置，就会撞。
3. **出口有防护、入口没有**：代码在拍完后显式爬回搜索高度，注释写「之后不论继续搜索还是返航都是水平飞行，低空水平飞可能撞到树」。**但进场段同样是一次低空水平飞行，没有对应处理。**

**建议**：把真实的 `start=(f.state.x, f.state.y, f.state.z)` 传进去，并且用 `safety.check_segment` 校验**实际航段**而不只是端点：

```python
def _confirm_altitude(site, item, search_alt, here):
    for alt in (max(site["min_altitude"], 9.0), 12.0):
        step = survey.Step("confirm", item.get("id", ""), (item["x"], item["y"], alt), 0.0, -90.0, "wide")
        target = step.position
        if not (safety.check_point(site, target)
                or safety.check_segment(site, here, target, 0)):   # 端点与进场段都要过
            return alt
    return search_alt
```

如果进场段必然穿树，正确做法是先在同高度平飞到目标正上方，再垂直下降（而不是让 PX4 走 3D 直线）。

---

## 二、中严重度

### M1 · `_apply_defaults` 改成后台任务后，`RTL_RETURN_ALT` 可能在起飞前还没写进去

- 文件：`backend/src/droneagent/flight.py`
- 行：29（参数表）、146（创建任务）、148–153（实现）
- 类别：bug（安全回归）

```python
DEFAULT_PARAMS = {"SIM_BAT_DRAIN": 600.0, "SIM_BAT_MIN_PCT": 5.0, "RTL_RETURN_ALT": 30.0}
...
self._tasks.append(asyncio.create_task(self._apply_defaults()))   # 后台，不再 await
```

改动前 `connect()` 里 `await` 完这三个参数才返回；现在立刻返回，**起飞流程不会等参数写完**。

`RTL_RETURN_ALT` 是返航高度。项目 README 明确写过「PX4 默认返航高度 60 米超出场地限高，正常返航改由程序沿校验过的航线执行」——但 `failsafe_return()` 用的正是**飞控自带返航**，它依赖这个参数。参数还没写进去时触发 failsafe，飞机会按 PX4 默认的 60 米返航。

实际风险被时间掩盖（每个参数最多等 3 秒，而起飞前有 `wait_ready` 最长 90 秒和飞行前检查），但这是概率不是保证。

**建议**：把 `RTL_RETURN_ALT` 留在 `connect()` 的 await 路径里，或起飞前显式 `await self._defaults_done`。纯仿真的 `SIM_BAT_*` 可以继续放后台。

### M2 · `color_key` 产出的「棕」不在键集合里，褐色上衣永远匹配不上

- 文件：`backend/src/droneagent/rescue/roles.py`
- 行：27、33
- 类别：bug

```python
COLOR_KEYS = "红橙黄绿蓝紫粉白灰黑"          # 没有「棕」
...
s = s.replace("橘", "橙").replace("咖啡", "棕").replace("银", "灰")
return next((c for c in s if c in COLOR_KEYS), None)
```

`.replace("咖啡", "棕")` 生成的「棕」随后被 `c in COLOR_KEYS` 过滤掉，返回 `None`。模型输出「棕色」「咖啡色」时一律拿不到颜色。

影响：`attributes_match` 首行 `if not t_top or s_top != t_top: return False`，目标上衣是褐色时**永远不匹配**，属性比对这条路径对褐色完全失效（会退回模型自己的 `match` 判断，不会误认，但少了一层保障）。

场景生成当前用的颜色（`COLORS`/`BOTTOMS`）不含棕，所以只在真实用户输入时暴露。

**建议**：`COLOR_KEYS` 加「棕」（并同步提示词里允许的颜色枚举），或删掉这条永远不生效的 replace。

### M3 · `rescue/evaluate.py` 单个场景失败后不清理，会连锁影响后续种子

- 文件：`backend/src/droneagent/rescue/evaluate.py`
- 行：37（`/sim/start`）、`run_one` 的异常路径
- 类别：bug（评测可信度）

`main()` 对每个种子 `try/except`，失败只记一行 `error: ...` 就继续。但 `run_one` 失败时**没有取消可能残留的任务、也没有停仿真**。而 `/sim/start` 在 `runtime.active_mission` 非空或上一个场景还在 `starting` 时返回 409，`raise_for_status()` 会立刻抛错。

于是：**种子 1 卡住 → 种子 2..N 全部 409 失败**。报告里的失败率会远高于真实失败率，看不出真实水平。

**建议**：`main()` 的 `except` 分支里加清理——`POST /missions/{mid}/cancel`（若有）、`POST /sim/stop`，再 sleep 几秒。或在 `run_one` 用 `try/finally` 保证清理。

---

## 三、低严重度

### L1 · `in_water` 直接确认目标，没有身份校验，且与场景生成器的隐式耦合

- 文件：`backend/src/droneagent/agents/rescue.py`
- 行：197、201
- 类别：maintainability

```python
in_water = verdict["person"] == "yes" and verdict["in_water"] == "yes"
if verdict["person"] == "yes" and (verdict["match"] in CONFIRM_MATCH or attrs or in_water):
```

`in_water` 为真就确认，不看衣着、不看年龄。这在当前场景下是安全的——`rescue/scene.py` 里只有 target 会取 `state="water"`，路人只有 walking/sitting。**但这是一条没有写出来的不变量。** 任何未来在场景里给路人加水域状态，都会直接变成误认。

同时 `detect.py` 的 `GUIDE` 新增了「水里有人时 match 至少为 partial」，会进一步推动模型给正分。两个改动叠加，水域的判断实际上只依赖「是不是人」。

**建议**：至少加一行断言/注释把不变量写死，或在场景生成器里显式保证「普通人不进入水域」并加测试。

### L2 · `_own_shadow` 的 2.5 米固定半径会误杀站在影子附近的人

- 文件：`backend/src/droneagent/agents/rescue.py`
- 行：155
- 类别：bug（低概率）

影子偏移约为 `z / tan(仰角)`。9 米高度、40° 仰角时约 10.7 米。一个恰好站在影子落点上的人（离飞机地面投影 10.7 米）会被当成影子过滤掉。概率低，但属于「静默丢候选」，比误报更难发现。

**建议**：把半径改成随高度和太阳仰角变化（例如 `min(2.5, 0.35 * z / tan(仰角))`），或在过滤时记录一条 `shadow_filtered` 事件，事后可查。

### L3 · `get_param` 超时后回退到 int 会再等一个超时

- 文件：`backend/src/droneagent/flight.py`
- 行：318–322
- 类别：performance

```python
try:
    return await _cmd(self.param.get_param_float(name), ...)   # 12 秒超时
except Exception:
    return float(await _cmd(self.param.get_param_int(name), ...))   # 再 12 秒
```

float 读取超时（12 秒）会落到 int 分支再等 12 秒，最坏 24 秒。而且 `_cmd` 抛的是 `FlightError`，这个回退原本只想处理「参数是 int 类型」的失败，把超时也吞了。

**建议**：只对类型不匹配回退，超时直接抛出（`except FlightError: raise` 放在前面）。

### L4 · `fresh` 重连时销毁失败会泄漏一个 MAVSDK 实例

- 文件：`backend/src/droneagent/flight.py`
- 行：120–125
- 类别：maintainability

```python
old = Flight._mavsdk.pop(self.url)
await asyncio.sleep(0.2)
try:
    old.destroy()
except Exception:      # 销毁失败不影响新建
    pass
```

`pop` 已经把旧实例从字典里移除；`destroy()` 失败时没有任何引用保留，但它仍占着 UDP 端口和后台任务。反复重启仿真可能累积。

**建议**：失败时记一条 warning 日志（不打印异常内容以外的东西），便于排查端口占用。

---

## 四、看过但没发现问题的部分

| 文件 | 判断 |
|---|---|
| `frontend/src/scenarios/index.ts` | 把计划卡片渲染下沉到场景注册表，结构清晰；`planRows` 两个实现都处理了空数组 |
| `frontend/src/components/task/PlanCard.vue` | 改为透传 `scenario`，删掉了重复的 POLICY 表，是正确的去重 |
| `frontend/src/views/TaskView.vue` | 只加了一个 prop，无副作用 |
| `api.py` `cancel()` 增加 `queued` 状态 | 与 `runtime.try_claim` 的任务队列设计一致 |
| `agents/runtime.py` 的 `flight()` 加锁超时 | `wait_for(Lock.acquire(), 30)` 超时后未获取锁，不会进入 `finally` 释放，逻辑正确 |
| `rescue/report.py` 新增 `searched == 0` 分支 | 修掉了「没起飞却报搜索了 0 个点」的误导性文案 |
| `rescue/detect.py` `verify` 支持传图片 | 类型收窄正确，`Frame | Image.Image` 两条路径都覆盖 |

---

## 五、覆盖率

| 项 | 数量 |
|---|---|
| OCR 选中的可审文件 | 20 |
| 已审（含额外人工读取的调用上下文） | 20 |
| 跳过 | 0 |
| **覆盖率** | **100%** |

其中 6 个 `backend/eval/results/*.json` 是评测产出数据，只核对了字段结构（确认 `usage.total.cost_yuan` 路径存在），未逐行阅读数值。

## 六、诚实说明

- **没有跑仿真、没有调付费模型、没有改源码、没有提交或推送。**
- 唯一执行的写操作是 `pytest`（生成 `.pytest_cache`，已在 `.gitignore` 中）。
- H1 是通过读 `safety.check_plan` 的默认参数与 `_shoot` 的飞行路径确认的，**没有在仿真里实际制造一次穿树来复现**。如果要升级为「已复现」，需要在场景里种一棵位于进场航段上的树跑一次。
- M1 是**时序概率**问题，没有构造「参数未写完就触发 failsafe」的复现。
- 没有审 10-17 之前的提交（前两轮审查文档已覆盖）。
- 没有做前端浏览器端到端验证，只做了静态阅读与 `pytest`。
