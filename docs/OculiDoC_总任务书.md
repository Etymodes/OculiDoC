# OculiDoC 总任务书（持续更新版）

> 用途：作为开发者、临床合作者和大语言模型恢复项目上下文时的唯一总入口。
> 当前稳定基线：`feature/gaze-tasks-mvp`，M3D11B HEAD `5d9a7eaedebacafdab62b9c59ca765dc491016f9`。
> 当前版本：`0.1.0.dev0`，Windows engineering validation build。
> 更新时间：2026-07-19。

---

## 0. 一句话定义

OculiDoC 是面向意识障碍患者的眼动评估、交互、沟通与训练平台，采用“管理员控制端 + 患者显示端 + 本地局域网移动控制端 + 真实眼动设备 + 结构化实验记录”的多端架构。

项目当前已经完成真实 Tobii Eye Tracker 5 原生 Stream Engine 采样、追踪球与左右二分问答、患者与实验会话持久化、Windows 打包及工程验收；下一阶段的重点是把真实设备状态、局域网控制、患者显示端状态机和剩余实验模块补齐为可运行 demo，再逐轮完善。

---

## 1. 当前已验证基线

### 1.1 Git 与构建

- 仓库：`Etymodes/OculiDoC`
- 分支：`feature/gaze-tasks-mvp`
- M3D11A HEAD：`a9c37398323ed43eb1ff823d2ca99d49d4f2e326`
- 远端跟踪引用已核验与本地 HEAD 一致。
- Windows PyInstaller onedir 构建通过。
- EXE：`dist/windows/OculiDoC/OculiDoC.exe`
- 构建验证：`dist/windows/OculiDoC_build_verification.json`
- 工程验收包：`OculiDoC-v0.1.0.dev0-win-x64-engineering-ecd5e019.zip`
- 工程包 SHA256：`8e421b99ced17a9ef9c78f4aadc648aa7dec35d84c0c120928c615601df1a1da`
- 发布包不含患者原始数据。

### 1.2 Tobii Eye Tracker 5

当前实现通过 `ctypes` 直接加载系统安装的：

```text
C:\Program Files\Tobii\Tobii EyeX\tobii_stream_engine.dll
```

不依赖 `tobii_research` 或第三方 Python Tobii 包。

已验证设备：

```text
DEVICE_NAME=Tobii Eye Tracker 5
DEVICE_URL=tobii-prp://IS50F-100200446653
SOURCE_CLOCK_ID=tobii-stream-engine
```

已持久化用户级配置：

```text
OCULIDOC_GAZE_SOURCE=tobii_stream_engine
OCULIDOC_TOBII_STREAM_ENGINE_DLL=C:\Program Files\Tobii\Tobii EyeX\tobii_stream_engine.dll
```

工程验收数据：

| 项目 | 总样本 | 有效样本 | 有效率 |
|---|---:|---:|---:|
| 校准后20秒探测 | 661 | 653 | 98.79% |
| 追踪球真实任务 | 865 | 859 | 99.31% |
| 左右二分问答真实任务 | 97 | 92 | 94.85% |
| 发布前10秒实时探测 | 330 | 329 | 99.70% |

### 1.3 已完成能力

- PySide6 管理员主窗口。
- 独立患者显示端窗口。
- 患者登记、修改、启用、切换。
- SQLite 持久化。
- 实验会话创建、启动、完成、失败和文件登记。
- 眼动采集与人工复核工作台。
- 追踪球任务。
- 左右二分问答。
- 常用问题库：内置问题、自定义问题、JSON 原子保存。
- 视线事件 Parquet 记录。
- 任务事件 JSONL。
- 任务配置、布局、屏幕上下文、结果和 manifest。
- 患者实验历史。
- 患者趋势报告基础能力。
- 原生 Tobii Stream Engine 适配器。
- mock 模拟眼动源。
- Tobii legacy/hospital bridge 框架。
- Windows Logo、图标、版本信息和 PyInstaller 打包。
- 冻结程序任务子进程路由。
- 无患者数据工程验收包。

---

## 2. 当前界面已确认的问题

### 2.1 底部眼动源状态不真实

主窗口底部仍硬编码显示：

```text
眼动源：模拟数据源
```

即使实际配置已经是 `tobii_stream_engine`。该状态必须改为从当前 `Settings` 和运行时设备状态生成，禁止硬编码。

目标显示：

```text
眼动源：Tobii Eye Tracker 5 · 已连接 · 33 Hz · 有效率 99%
```

最低 demo：

```text
眼动源：Tobii Eye Tracker 5 · 原生 Stream Engine
```

状态分级：

- 绿色：已连接且预检通过。
- 黄色：已连接但有效率不足。
- 红色：设备未连接或采样错误。
- 灰色：模拟模式。

### 2.2 本地后台状态不真实

底部目前显示：

```text
本地后台：未启动 · http://127.0.0.1:8000
```

当前桌面程序不会自动启动 FastAPI，只存在独立命令：

```powershell
python -m oculidoc.api
```

目标：

- 桌面程序启动后可自动启动或显式启动本地后台。
- 后台状态实时更新。
- 后台监听地址区分本机地址和局域网地址。
- 鼠标悬停或点击底部后台状态时弹出配对卡片：局域网控制地址、QR 二维码、短期配对码、复制地址、重新生成令牌、后台启停按钮。

### 2.3 问题类型交互不符合需求

当前“问题类型”使用下拉框。必须改为从左到右的横向单选按钮：

```text
○ 是否题    ○ 问答题    ● 询问题    ○ 其他
```

选中类型后：

- 是否题、问答题：选项1显示“正确选项”，选项2显示“错误选项”，结果可评分。
- 询问题、其他：显示“选项1 / 选项2”，不自动判定正确或错误。

### 2.4 已保存常用问题不可编辑

当前只能从下拉框加载、添加新常用问题。必须增加：

- 保存修改；
- 内置问题另存为自定义；
- 自定义问题原位更新并保留 `template_id`；
- 后续增加删除、排序、标签和导入导出。

推荐布局：

```text
常用问题：[选择常用问题…… ▼] [保存修改/另存为] [添加新问题]
```

规则：

- 内置问题不可直接覆盖，点击修改时另存为自定义。
- 自定义问题可原位修改。
- 有未保存修改时切换问题需提醒。
- 保存操作继续使用临时文件 + 原子替换。

---

## 3. 下一代多端架构

### 3.1 四个逻辑端

#### A. 管理员桌面端

职责：患者管理、设备管理、实验参数设置、启动暂停停止任务、查看实时质量、查看记录与报告、管理患者显示端、管理局域网移动端配对。

#### B. 患者显示端

患者显示端不应只在点击后临时打开，而应具有持续状态：

```text
CLOSED → IDLE → READY → PREVIEW → RUNNING → PAUSED → RESULT → ERROR → IDLE
```

非测试期间应一直显示患者端状态，例如：等待管理员、当前患者已就绪、正在连接眼动设备、请保持头部位置、即将开始任务、任务已结束、请休息、连接异常。

未来所有任务都必须能投送到患者显示端，而不是各自随意创建窗口。

#### C. 局域网移动管理员端

手机在同一局域网扫描二维码后打开网页。

第一轮 demo 支持：

- 查看后台在线状态。
- 查看眼动设备状态。
- 查看患者显示端状态。
- 查看可用任务列表。
- 修改测试题目和基本参数。
- 把文本、图片或任务预览投到患者显示端。
- 启动、暂停、继续、终止任务。
- 触发紧急返回待机页。

后续支持患者切换、实验模板、实时质量、简化实验结果、权限分级和审计日志。

**最终目标不是“简化投屏遥控器”，而是管理员端的局域网远程镜像。**

手机网页最终必须覆盖管理员桌面端的全部日常操作：

- 查看并修改各实验模块的全部设置；
- 编辑问题、选项、刺激材料、时长、停留阈值、随机化和评分规则；
- 选择当前患者和实验模板；
- 推送预览到患者显示端；
- 正式启动、暂停、继续、终止和紧急复位任务；
- 查看实时眼动质量、任务状态、当前选择和简化结果；
- 手机修改后桌面端立即同步，桌面修改后手机端立即同步。

实现原则：桌面进程仍是任务执行与安全校验的权威控制者；手机端发送结构化命令，
桌面端确认并执行，不允许 FastAPI 绕过桌面状态机直接重复启动任务。

#### D. 本地后台

采用 FastAPI，桌面端为权威控制者。

建议接口：

```text
GET  /api/v1/health
GET  /api/v1/runtime
GET  /api/v1/devices/gaze
POST /api/v1/devices/gaze/probe
GET  /api/v1/patient-display
POST /api/v1/patient-display/state
GET  /api/v1/modules
POST /api/v1/tasks/preview
POST /api/v1/tasks/start
POST /api/v1/tasks/pause
POST /api/v1/tasks/resume
POST /api/v1/tasks/stop
GET  /api/v1/events
WS   /api/v1/ws
```

配对与安全：

- 默认只允许私有局域网。
- QR 中携带短期配对令牌，不直接包含患者身份。
- 控制令牌过期。
- 所有控制动作写审计日志。
- 默认不通过移动端返回完整患者病历。
- 后台启动后必须显示绑定 IP、端口和认证状态。
- 不允许公网自动暴露。

---

## 4. 下一阶段里程碑

### M3D11A：界面纠错与真实状态

状态：已完成并推送，提交 `a9c37398323ed43eb1ff823d2ca99d49d4f2e326`。

目标提交：

```text
Improve question setup and runtime status
```

内容：

- 主窗口眼动源文字根据 `Settings.gaze_source` 显示。
- 问题类型下拉框替换为横向单选按钮。
- 增加“保存修改/另存为自定义”按钮。
- `CommonQuestionStore` 增加按 `template_id` 更新能力。
- 内置问题保持不可变。
- 增加 UI 和持久化测试。
- README 更新“已接入真实 Tobii”的过时文字。
- 将本总任务书加入 `docs/`。

验收：

- Tobii 模式不再显示“模拟数据源”。
- 四类问题可直接单击切换。
- 自定义常用问题修改后重启仍存在。
- 修改内置问题会生成新的自定义副本。
- 全部 Ruff 和 pytest 通过。

### M3D11B：局域网后台与二维码 demo

状态：已完成并推送，提交 `5d9a7eaedebacafdab62b9c59ca765dc491016f9`；
人工验收已确认二维码、手机打开、文字投屏、待机恢复和任务预览可用。

目标提交：

```text
Add LAN control pairing demo
```

第一轮 demo：

- 桌面程序启动本地 FastAPI 子进程。
- 监听 `0.0.0.0`，同时保留 `127.0.0.1`。
- 自动发现首选私有 IPv4。
- 底部后台状态真实显示。
- 悬停或点击显示 QR 配对卡。
- 手机页面显示在线状态、当前眼动源、患者显示端状态、文本投屏输入和返回待机按钮。
- 暂不开放完整患者数据。
- 后台退出时桌面端回收子进程。

### M3D11B.1：局域网交互体验修正

目标提交：

```text
Refine LAN pairing and patient display UX
```

内容：

- 悬停打开的二维码卡在鼠标离开按钮和卡片后自动收起。
- 点击底部后台状态可固定二维码卡，再次点击关闭。
- 配对卡增加“刷新IP/二维码”。
- Wi-Fi 或网卡切换后可重新发现私有 IPv4，不需要重启程序。
- 患者显示端文字改为 40–84 px 自适应大字。
- 明确手机网页最终是管理员端完整远程镜像，而不是仅有投屏功能。

### M3D11C：患者显示端状态总线 demo

目标提交：

```text
Add patient display control state
```

内容：

- 患者显示端常驻。
- 桌面端、移动端和患者端共享统一状态模型。
- 手机或管理员端可把文本状态投到患者端。
- 任务启动前显示 READY 和倒计时。
- 任务结束返回 IDLE。
- 异常显示 ERROR，不静默关闭。

### M3D11D：设备设置与任务前预检

目标提交：

```text
Add Tobii device settings and preflight
```

内容：

- 软件内发现 Stream Engine DLL。
- 软件内保存眼动源配置。
- 显示设备 URL、采样率、实时有效率。
- 3至10秒任务前预检。
- 有效率不足时阻止任务。
- 正式模式禁止静默回退 mock。
- mock 必须有明显灰色“模拟模式”标记。
- 支持打开 Tobii Experience 和校准提示。

---

## 5. 剩余任务模块：先全部做 demo，再轮询完善

统一原则：第一轮能打开、显示、接收真实眼动和结束；第二轮统一记录；第三轮评分与报告；第四轮临床参数化与异常处理；第五轮患者分层和正式验证。

### M3D12A 屏幕打字 demo

- 6至12个大尺寸字符格。
- 停留选择。
- 删除、空格、朗读、清空。
- 文字结果同步到管理员端和移动端。
- 保存输入过程与最终文本。
- 后续增加词频预测、拼音/字词模式、Yes/No 快捷区、防误触与 DoC 低反应模式。

### M3D12B 上下二分问答 demo

- 复用左右二分问答。
- 布局改为上下。
- 共享问题库、计时、停留和评分。
- 记录上下 AOI。
- 允许横向和纵向协议比较。

### M3D12C 多选项问答 demo

- 2×2、2×3、环形布局。
- 2至6个选项。
- 停留选择。
- 支持有答案与无答案问题。
- 保存选项位置随机化。

### M3D12D 图片选择 demo

- 左右或四宫格图片。
- 文字/语音指令。
- 图片预加载。
- 支持正确目标标注。
- 保存反应时间、目标和实际选择。

### M3D12E 随指令注视 demo

- 屏幕预定义 AOI。
- 播放文字或语音指令。
- 记录指令后首次进入目标 AOI 的时间。
- 记录是否持续注视。
- 增加无目标和干扰目标条件。

### M3D12F 统一实验模板

每个任务的设置可保存为实验模板，包含任务类型、刺激、字体、AOI、停留阈值、时长、随机化种子、评分规则和设备要求。模板可由桌面端和移动端选择。

---

## 6. 记录、报告与临床方向

### M3D13A 统一运行时协议

所有任务必须统一：

```text
prepare → preflight → ready → countdown → running → paused → completed/aborted/failed → persist → return_to_idle
```

禁止各任务自行实现互不兼容的启动和退出逻辑。

### M3D13B 统一事件模型

至少包括：`task_created`、`preflight_started`、`preflight_passed`、`preflight_failed`、`stimulus_presented`、`aoi_entered`、`aoi_exited`、`dwell_started`、`dwell_reset`、`selection_committed`、`task_paused`、`task_resumed`、`task_completed`、`task_aborted`、`device_disconnected`、`recording_failed`。

### M3D13C 眼动质量指标

- 总样本数、有效样本数、有效率。
- 连续无效区间。
- 采样间隔分布。
- 坐标越界比例。
- 瞬时跳变比例。
- 视线丢失次数。
- 每眼有效性。
- 头位/距离提示。
- 任务内有效率随时间变化。

### M3D13D DoC 临床指标

需要分清：行为是否出现、是否可重复、是否与指令一致、是否超过偶然水平、设备质量是否足以解释结果、结果是否支持进一步复核。

禁止仅因一次命中就自动输出“意识改善”。建议输出描述性结果、数据质量、命令响应证据、重复性、不确定性和需要临床复核的理由。

### M3D13E 报告

- 单次任务报告。
- 同患者纵向趋势。
- 不同任务比较。
- 设备质量附录。
- CSV、Parquet、JSON、PDF。
- 结果中明确标注工程验证、非独立诊断、数据缺失、模拟数据和人工操作。

---

## 7. 临床与产品安全约束

- 当前 Eye Tracker 5 版本仅为 engineering validation。
- 不标记为临床正式版。
- 不作为单独诊断、预后或治疗依据。
- Tobii Eye Tracker 5 的分析、健康评估和数据记录许可需另行确认。
- 患者身份、会话、轨迹、数据库、日志不得提交 Git。
- 移动控制端默认不显示完整身份信息。
- 所有远程控制行为必须可审计。
- 设备失联不得静默回落至 mock。
- mock 模式必须强提示。
- 任务开始前必须有设备预检。
- 紧急退出始终最高优先级。
- 患者显示端不能因后台错误停留在误导画面。
- 任何“改善/恶化”结论必须保留人工复核。

---

## 8. 历次主要报错复盘

### 8.1 Windows BAT 出现 `锘緻echo off`

原因：`.bat` 使用带 BOM 的 UTF-8，旧式 `cmd.exe` 把 BOM 解读为命令字符。

解决：BAT 使用 UTF-8 无 BOM 或 ANSI；生成脚本时显式控制编码；第一行必须实际是 `@echo off`。

后续规则：所有 `.bat` 生成后检查前三个字节，优先用 PowerShell 或 Python 启动器。

### 8.2 启动后强制校准和强制语义预测

原因：启动入口直接串联业务模块，没有主功能面板状态机。

解决：一键启动只打开常驻功能面板；校准、任务和患者管理必须由用户选择；每个模块有返回和退出。

后续规则：启动程序不得隐式运行临床任务，任务必须通过显式命令启动。

### 8.3 PowerShell 多次 `Missing closing ')'`

出现形式：新行开头使用 `+` 或 `/`，跨行表达式依赖不稳定续行规则，字符串拼接和括号嵌套过深。

最终解决：PowerShell 只保留极薄启动壳；复杂诊断、变更、探测和打包逻辑放进 Python；Python 程序先编译检查，再写入临时文件运行。

后续规则：

```text
PowerShell：找文件、设置执行策略、调用 Python。
Python：全部复杂逻辑。
```

严禁：

```powershell
$value = (
    $a
    + $b
)
```

应写成 `$value = $a + $b` 或移入 Python。

### 8.4 `$MyInvocation.MyCommand.Path` 为 null

原因：在嵌套 `& { ... }` scriptblock 中读取，作用域不再对应脚本文件。

解决：`$scriptDirectory = $PSScriptRoot`。

后续规则：文件脚本定位统一使用 `$PSScriptRoot`。

### 8.5 Python 正则 `global flags not at the start`

原因：`(?mx)` 前存在换行或空白。

解决：使用 `re.compile(pattern, re.MULTILINE | re.VERBOSE)`；固定结构优先逐行匹配。

### 8.6 `.spec` 文件存在但 `git status` 不显示

原因：`.gitignore` 通配规则忽略 `.spec`，恢复脚本把磁盘存在和 Git 可见混为一谈。

解决：单独检查磁盘文件，并使用 `git add -f -- packaging/windows/OculiDoC.spec`。

后续规则：可见脏文件、被忽略文件、暂存文件分别验证。

### 8.7 `Start-Process -ArgumentList` 拆分含空格路径

原因：`C:\Program Files\...` 被拆成多个参数。

解决：PowerShell 直接调用并展开参数数组 `& $pythonCommand @arguments`，或 Python `subprocess.run([...])`。

后续规则：所有外部进程参数都使用列表，不手工拼命令字符串。

### 8.8 Python 通过 stdin 载入后 `input()` 触发 EOF

原因：Python 源码本身占用了 stdin，随后 `input()` 无法读取用户输入。

解决：先写入 `%TEMP%/*.py`，再执行临时文件，完成后删除。

后续规则：需要交互输入的 Python 不得通过 pipe/stdin 载入源码。

### 8.9 GitHub SSH `Permission denied (publickey)`

原因：当前 PowerShell 会话未获得 SSH agent 或密钥；发布验收错误地把联网 fetch 设为硬阻断条件。

解决：本地工程验收使用本地分支、HEAD、干净工作区和已有远端跟踪引用；联网 fetch 作为独立发布步骤。

后续规则：代码一致性检查与网络可用性解耦，push/fetch 只在专门步骤执行。

### 8.10 Tobii 已连接但 `VALID=0`

原因分两类：首次未完成 Display Setup/Profile/Calibration；发布脚本在操作者未坐好时立即开始采样。

最终解决：校准后20秒探测；发布前明确提示操作者准备；10秒探测最多三轮；达到阈值才继续。

后续规则：连接成功与有效视线是两个不同状态，任何人工相关设备检查必须有“准备好后按 Enter”。

### 8.11 实际 Tobii 已启用，界面仍显示模拟源

原因：数据源配置和任务子进程均正确，主窗口底部文字是硬编码。

解决计划：M3D11A 改为真实状态绑定，未来由设备状态服务统一提供。

后续规则：UI 状态不得复制业务状态，必须从单一运行时状态源派生。

---

## 9. 后续开发的防错与提效协议

### 9.1 每个补丁的固定流程

1. 确认分支、HEAD、工作区。
2. 收集相关文件、测试和引用关系。
3. 在内存中构造全部修改。
4. 先对 Python 文件执行 `compile()`。
5. 再写入磁盘。
6. 运行 `ruff format`。
7. 运行 `ruff check`。
8. 运行聚焦测试，使用 `--maxfail=0`。
9. 运行全仓库测试，使用 `--maxfail=0`。
10. `git diff --check`。
11. 核验只修改预期文件。
12. 暂存并核验暂存文件集合。
13. 小步提交。
14. 提交后确认工作区干净。
15. push 作为独立步骤。

### 9.2 诊断与修改分离

诊断脚本默认只读，收集所有独立失败，生成 `%TEMP%` 报告，不暂存、不提交、不 push。

修复脚本必须有精确 branch/HEAD guard、精确脏文件 guard、mutation fail-fast；失败后不得重复运行旧版本，必须针对当前真实状态生成恢复版。

### 9.3 脚本技术约束

- PowerShell 保持薄壳。
- Python 承载复杂逻辑。
- 所有路径使用 `Path` 或参数数组。
- 不拼接 shell 命令字符串。
- 不在新行开头放二元运算符。
- 交互 Python 使用临时文件。
- 固定结构优先逐行解析。
- 不使用脆弱的 Ruff 格式化后长文本替换，除非 HEAD 精确匹配、工作区干净、目标片段数量精确为1且替换后先编译。

### 9.4 测试规则

- 全仓库测试中的眼动源强制仅对子进程设置 `OCULIDOC_GAZE_SOURCE=mock`。
- 不修改父进程环境。
- 真实 Tobii 测试单独执行，不纳入普通 CI。
- 所有任务至少有配置、UI、数据记录、无效样本、中止和冻结启动测试。

### 9.5 发布规则

发布分级：`development` → `engineering_validation` → `research_validation` → `clinical_investigation` → `clinical_release`。

当前只能使用 `engineering_validation`。

每个发布包必须包含 commit、产品版本、构建验证、设备验证、任务验证、SHA256、是否包含患者信息、许可与临床限制、目标机器前置条件。

---

## 10. 代码结构恢复索引

```text
src/oculidoc/app.py
    Qt 应用、数据库和管理员窗口构造

src/oculidoc/config.py
    环境变量与本机配置

src/oculidoc/ui/main_window.py
    管理员主界面、模块入口、患者显示端和任务子进程

src/oculidoc/ui/patient_window.py
    当前患者显示端基础壳

src/oculidoc/api/app.py
    当前仅有 health 的 FastAPI

src/oculidoc/api/__main__.py
    独立 uvicorn 启动入口

src/oculidoc/modules/registry.py
    八类模块注册和状态

src/oculidoc/tasks/binary_question.py
    左右二分问答、设置页和问题库 UI

src/oculidoc/tasks/question_bank.py
    常用问题模型、内置问题和 JSON 原子持久化

src/oculidoc/tasks/tracking_ball.py
    追踪球设置和任务

src/oculidoc/tasks/gaze_stream.py
    眼动源工厂和后台线程

src/oculidoc/devices/tobii_stream_engine.py
    Tobii Stream Engine ctypes 原生适配器

src/oculidoc/experiments/task_runtime.py
    统一实验运行记录

src/oculidoc/process_launch.py
    源码与冻结程序任务启动路由

packaging/windows/OculiDoC.spec
scripts/build_windows.ps1
    Windows 冻结构建
```

---

## 11. 下一轮执行顺序

严格按以下顺序推进，但采用“每个功能先 demo 一轮”的策略：

```text
M3D11A 真实眼动状态 + 问题类型单选 + 常用问题修改
M3D11B 本地后台自动启动 + LAN 地址 + QR 配对卡 + 手机控制页 demo
M3D11C 患者显示端状态机 + 文本投屏 + 手机/桌面同步 demo
M3D11D 设备设置页 + Tobii 自动发现 + 任务前预检
M3D12A 屏幕打字 demo
M3D12B 上下二分问答 demo
M3D12C 多选项问答 demo
M3D12D 图片选择 demo
M3D12E 随指令注视 demo
M3D12F 统一实验模板 demo
```

然后回到 M3D11A 开始第二轮完善，再按相同顺序轮询。

---

## 12. 当前立即行动

当前下一提交应聚焦 M3D11A，不把 QR、后台、患者状态总线和全部新任务一次塞入同一提交。

建议提交：

```text
Improve question setup and runtime status
```

预期修改：

```text
src/oculidoc/ui/main_window.py
src/oculidoc/tasks/binary_question.py
src/oculidoc/tasks/question_bank.py
tests/test_task_settings_upgrade.py
tests/test_main_window.py 或新增聚焦测试
README.md
docs/OculiDoC_总任务书.md
```

M3D11A 完成后立即进入 M3D11B，先做可扫描、可打开、可投文字到患者显示端的最小 LAN demo。
