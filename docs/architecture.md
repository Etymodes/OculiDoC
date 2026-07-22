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
  - Tobii bridge
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

## 依赖规则

- UI 不直接读取 TCP socket；
- UI 不直接执行 SQL；
- 眼动设备适配器不直接修改 UI；
- 评分算法不依赖 PySide6；
- 手机后台不直接操作设备进程；
- 原始采样与派生评分分开保存。

## 眼动接口

每一种眼动数据源实现：

```text
start()
stop()
read() -> GazeSample | None
```

第一阶段使用 `MockGazeSource`。后续加入旧桥接器、官方 SDK 和回放数据源。

旧桥接器必须处理 TCP 分包、粘包、半包、断连、有限重连、超时、无效 JSON 和时间戳。

## 端口

- 9999：原医院项目 Tobii 桥接端口；
- 8000：OculiDoC 本地管理后台默认端口。

新程序不得抢占仍由旧项目使用的端口。
