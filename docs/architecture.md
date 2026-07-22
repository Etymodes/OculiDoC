# OculiDoC 架构基线

## 用户角色

### 测试医师／管理员

负责患者管理、实验配置、题库、实验控制、结果查看、设备状态和紧急退出。

### 意识障碍患者／被试

患者端只显示当前实验刺激和必要提示，不显示患者列表、后台配置或复杂菜单。

## 分层

```text
PySide6 管理员端 / 患者端 / 手机后台
                  |
                  v
         Application Use Cases
                  |
                  v
        Domain Models and Scoring
                  |
                  v
Infrastructure Adapters
  - Tobii Stream Engine / generic gaze bridge
  - GazeCollect JSON / JustNeedToSee DLL compatibility
  - hardware auto-detection
  - mock gaze source
  - SQLite
  - Parquet
  - TTS
  - local API
```

## 屏幕打字与语音

- `ScreenKeyboardTask` 复用统一眼动采样、任务前预检、全屏计时和 `RecordedTaskRuntime`，不创建第二套任务运行时。
- 拼音阶段状态只存在于任务进程；共享任务设置继续通过 `task_configs.json` 做版本校验和原子保存。
- 最终文字与当前拼音通过现有 `LanControlStateStore` 的 RUNNING 状态同步，患者显示端和手机端不直接访问任务对象。
- 自动播报由任务子进程调用 Qt 系统语音；手机端仅写入版本化 `speech_replay.json` 请求，任务进程轮询后重播最近一句，不开放公网语音接口。

## 横向与纵向二分问答

- `BinaryQuestionTask` 通过 `layout=horizontal/vertical` 复用同一套问题、停留、评分和记录逻辑，不维护两份任务代码。
- 横向任务使用 `gaze_x_normalized` 与左右 AOI；纵向任务使用 `gaze_y_normalized` 与上下 AOI。
- `binary_horizontal` 与 `binary_vertical` 使用独立配置修订号和患者会话，但共享问题库与配置模型，便于横纵协议比较。
- 布局方向、显示位置、逻辑选项映射和 AOI 都写入结构化记录；旧版 `selected_side` 字段继续保留兼容，同时新增 `selected_position`。

## 多选项问答

- `MultipleChoiceTask` 复用统一预检、全屏计时、语音重播、患者会话和 `RecordedTaskRuntime`，只维护选择集合与停留切换状态。
- 2–6 个逻辑选项与显示位置分离；随机化仅改变位置映射，不改变稳定的 `option_1` 至 `option_6` 标识。
- 一次停留切换选择，再次停留取消；注视锁存要求视线离开后才能再次触发同一选项，避免静止注视连续反转。
- 该任务没有正确答案和自动完成信号；多选集合持续保留到管理员退出、手机终止或达到最长时长。
- 选择集合通过 `LanControlStateStore` 同步，问题由任务进程自动播报；手机端只写入重播请求或终止命令。
- 记录选择/取消事件、显示位置、随机化种子、首次选择反应时间和每个选项 AOI；报告明确标为不评分。

## 随指令注视

- `InstructionFixationTask` 复用统一预检、语音重播、患者显示状态、全屏计时、患者会话和 `RecordedTaskRuntime`。
- 目标与干扰刺激使用稳定的九宫格位置标识；随机化只改变试次顺序、目标位置和干扰位置，不改变条件语义。
- 目标存在时记录首次进入、持续注视与稳定获得；无目标试次只记录干扰区稳定注视，不自动推断“正确抑制”或意识状态。
- 每个试次把目标 AOI 标为 `target`、干扰 AOI 标为 `incorrect_option`，并保存条件、指令、位置和阈值元数据。
- 任务设置继续通过 `task_configs.json` 做版本校验和原子保存，电脑端与手机端不维护两套协议定义。

## 完整患者数据迁移

- `patient_transfer` 只生成一个 UTF-8 CSV；患者、会话与清单使用结构化 JSON 单元格，会话目录内的所有普通文件使用 24,000 字节 Base64 分块。
- 导出只接受终态实验，写入临时文件并在完成后原子替换；导入先把全部文件暂存并核对分块顺序、大小和 SHA-256，完整验证成功后才写入患者与会话服务。
- 导入保留原患者、会话和清单标识；患者编号已存在时整名跳过其会话和文件，避免重复实验。系统生成的 `session.json` 由导入后的会话模型重新写入。
- CSV 是受控迁移包而不是 Git 工件；其中可能包含 Parquet、图像、视频和患者敏感信息。

## 实验记录人工维护

- 人工状态纠正只接受 `completed`、`aborted`、`failed` 三种终态；未知的实际结束时间保持为空，避免制造虚假实验时长。
- 主窗口把仍由当前进程持有的任务会话告知历史页，活动会话不可人工修改或删除。
- 删除以会话为聚合边界：先把专属目录原子移动到 `deleted_sessions/`，再在同一 SQLite 事务中删除文件清单与会话；数据库失败时目录回滚原位。

## 眼动传感器自动检测

- `AutoDetectEyeTrackerDevice` 复用现有 `EyeTrackerDevice` 协议，依次尝试 Stream Engine、通用 TCP NDJSON 桥接，以及已配置帮助程序的医院桥接。
- 候选接口必须完成连接、启动并产出一个眼动样本才算检测成功；失败候选会停止并断开，所有硬件均失败时明确报错，禁止切换到模拟源。
- 通用桥接接受第三方或自制传感器程序输出的逐行 JSON，至少包含归一化 `x`、`y` 与 `valid`；也支持带屏幕尺寸的像素坐标。USB/COM 设备枚举本身不等于视线数据。

## 医院旧版 Tobii 兼容

- `GazeCollectLegacyDevice` 只读取 HPF 新写入的 `*_gaze.json`；HPF 由管理员手动启动，OculiDoC 不加载其私有 DLL，也不结束既有进程。
- `JustNeedToSeeBundleDevice` 复用其目录内的 `tobii_stream_engine.dll`，但不读取鼠标位置；正式采样时必须关闭 `JustNeedToSee.exe`。
- EyePosition 只作为外部摆位检查入口，不实现 `EyeTrackerDevice`，也不进入自动探测候选。
- 两个兼容采样源必须由管理员显式选择并通过 3–10 秒任务前预检，避免与原生 Stream Engine 同时订阅设备。

## 患者报告

- 单次报告从患者仓储解析姓名与患者编号；UUID 仅保留在 JSON 和文件索引中，不在普通界面或 HTML 报告展示。
- 患者综合报告复用既有会话历史和单次采样解析，遍历全部任务并生成综合热力图、质量、追踪与问答趋势。
- 综合热力图以实际视线密度叠加各任务目标位置，不把不同会话错误连接成一条连续轨迹。
- 所有报告区块使用规则化中文摘要解释“图上看到了什么”，不自动推断意识状态、疗效或预后。

## 依赖规则

- UI 不直接读取 TCP socket；
- UI 不直接执行 SQL；
- 眼动设备适配器不直接修改 UI；
- 评分算法不依赖 PySide6；
- 手机后台不直接操作设备进程；
- 原始采样与派生评分分开保存。

## 眼动接口

每一种眼动设备实现：

```text
connect()
start_stream()
read_sample() -> EyeTrackerSample
stop_stream()
disconnect()
```

自动模式只组合真实硬件适配器；`SimulatedEyeTrackerDevice` 保留为显式工程测试源。通用桥接必须处理 TCP 分包、粘包、半包、断连、超时、无效 JSON 和时间戳。

## 端口

- 9999：原医院项目 Tobii 桥接端口；
- 8000：OculiDoC 本地管理后台默认端口。

新程序不得抢占仍由旧项目使用的端口。
