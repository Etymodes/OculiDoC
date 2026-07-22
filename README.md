# OculiDoC

OculiDoC 是面向意识障碍患者的眼动评估、交互、沟通与训练平台。

联合开发者：

- Etymodes
- TiantanDoC（首都医科大学天坛医院意识障碍团队）

## 当前里程碑：M3D12G

M3D12G 已实现患者资料与全部实验文件的单 CSV 迁移，并在眼动源中加入硬件自动检测。
在原有 engineering validation build 基础上，当前能力包括：

- PySide6 管理员主窗口和独立患者显示端；
- 患者、实验会话与结构化任务记录；
- 眼动采集与复核工作台；
- 追踪球、左右/上下二分问答和分阶段拼音屏幕打字；
- 图片选择按任意多选类别与风格形成候选池，每次随机目标、干扰图、正确选项和左右位置；患者屏幕只显示大图，不显示图片名称；
- 图片库支持上传、修改元数据和删除自定义图片；上传文件复制到 OculiDoC 数据目录，原文件移动后仍可使用；
- 随指令注视提供九个预定义屏幕 AOI，可组合仅目标、目标伴干扰和无目标试次并随机平衡位置；
- 随指令注视的目标描述、形状、颜色、大小、干扰物、试次数、持续注视阈值和单试次时长可由电脑端或手机端设置；
- 每个试次记录首次进入目标 AOI、稳定注视潜伏期、最长连续注视、干扰区稳定注视及有效样本率，并进入患者历史与 HTML 报告；
- 无目标试次仅描述是否出现干扰区稳定注视，不自动输出意识判断；
- 左右/上下二分问答内置管理员工作簿中的 66 条原题，可直接修改保存，并可设置随机抽题数与随机题序；
- 连续二分题答错重试、答对后显示勾，按空格或 Enter 手动进入下一题；每题独立随机左右/上下位置；
- 连续文字题与图片题按每题时长计算总时限，并将逐题答案、正确性和错误尝试写入报告；
- 2–6 个文字选项的多选项问答，支持自动宫格和环形排列；
- 多选项可用注视停留逐项选择，同一选项再次选择即可取消，且不会因选择自动结束；
- 多选项没有固定正确答案或自动评分，可由管理员持续提问并通过任务窗口或手机端手动终止；
- 多选项问题、数量、文字、布局、停留阈值、最长时长、字号和位置随机化可在电脑端或手机端调整；
- 多选项选择/取消、显示位置、首次选择反应时间、AOI 和最终选择集合进入患者实验记录与报告；
- 多选项改用浅色高对比界面，并增高、上移选项区域；
- 上下二分问答复用左右任务的问题库、字体、停留、计时、评分和语音设置，仅把选项与中性区改为纵向；
- 上下任务使用视线纵坐标判定顶部/底部选项，独立保存版本化配置，并记录上下 AOI 与布局方向；
- 屏幕打字按“声母 → 确认 → 韵腹/组合韵母 → 确认 → 韵尾 → 确认 → 可选声调 → 确认”输入；
- 上半屏保留已输入文字，下半屏使用大尺寸选项，并提供删除、空格、朗读和清空；
- 屏幕打字的停留阈值、任务时长、声调步骤和三类字号可在电脑端或手机端调整；
- 输入过程、最终文本和每个阶段的眼动 AOI 均进入患者实验记录；
- 输入结果通过患者显示状态同步到独立患者端和手机端；
- 所有已实现眼动任务默认自动语音播报，手机端可发送“重播语音”命令；
- 追踪球开始时播报易理解的跟随提示，运行中不反复打扰患者；
- 追踪球可从共享默认图片库选择目标，也可按格式、大小和比例指引上传并持久保存新图片；
- 水平追踪轨迹可设在屏幕上/中/下，垂直轨迹可设在左/中/右；
- Tobii Eye Tracker 5 原生 Stream Engine 数据源；
- 自动检测会依次验证 Tobii 原生驱动及第三方/自制传感器兼容桥接，只有收到真实眼动样本才判定成功，且绝不回退模拟数据；
- 第三方/自制传感器可通过可配置地址的 TCP NDJSON 桥接提供归一化 `x`、`y`、`valid` 数据；
- 模拟眼动和兼容桥接数据源；
- FastAPI 本地后台基础入口；
- 追踪球、左右/上下二分问答、屏幕打字、图片选择和随指令注视共享版本化、原子保存的任务配置；
- 手机端保存设置并直接启动任务，桌面端校验配置版本；
- 设置冲突返回最新版本，不静默覆盖另一端修改；
- 患者显示端常驻，并与桌面端、手机端共享原子状态文件；
- 任务在管理员确认设置后显示 READY 与 3 秒倒计时，再进入 RUNNING；
- 任务完成、取消和异常分别进入 RESULT、IDLE 和 ERROR；
- 手机端与桌面端均可投送患者大字提示，运行中状态不可被普通投屏覆盖；
- 软件内选择并原子保存眼动源、Stream Engine DLL 与 3–10 秒预检策略；
- 自动发现 Tobii Stream Engine DLL，并提供 Tobii Experience 校准入口；
- 任务前强制采样预检，默认最低有效率由 60% 调整为 35%，低于阈值时仍阻止任务且不回退 mock；
- 任务结束与失败提示为非阻塞提示，显示 6 秒后自动关闭，避免覆盖下一任务设置；
- 手机任务下拉框仅在选项实际变化时重建，不再被每秒状态轮询反复刷新；
- 设备设置可直接打开 Tobii Experience 和 Tobii Ghost，分别用于校准与实时视线检查；
- 管理员端提供“检查更新”，只允许官方仓库的干净工作区进行快进更新；
- 患者管理可把全部患者资料、终态实验会话、清单及会话目录中的 Parquet、JSON、图片和视频一次导出为单个 UTF-8 CSV；
- 实验文件以小于常见表格单元格上限的 Base64 分块写入，导入前逐文件核对大小与 SHA-256；运行中的实验会阻止导出；
- 一键导入保留患者、会话和清单 UUID，按患者编号整名跳过重复数据，并继续兼容旧版基本资料 JSON；
- 桌面端与手机端显示设备 URL、实时采样率和有效率，mock 始终以灰色模拟模式标记；
- Windows PyInstaller 打包和自动化测试；
- 患者数据与源码仓库隔离规则。

当前版本已经完成真实 Tobii 工程验证，但仍不是医疗器械或临床正式版本，
不能作为单独的诊断、预后或治疗依据。

## 安装

新电脑只需 Windows 10/11、网络和系统自带的 `winget`（App Installer）。即使没有
Git、Python 或 pip，也可在 PowerShell 中用下面一整行自动补齐环境、克隆仓库并安装。
若提示找不到 `winget`，请先在 Microsoft Store 安装或更新“应用安装程序”，再重新执行：

```powershell
$ErrorActionPreference="Stop"; $Winget=(Get-Command winget -ErrorAction SilentlyContinue); if (-not $Winget) { throw "未找到 winget。请先在 Microsoft Store 安装或更新应用安装程序 (App Installer)，再重试。" }; $WingetExe=$Winget.Source; if (-not (Get-Command git -ErrorAction SilentlyContinue)) { & $WingetExe install --id Git.Git -e --source winget --silent --accept-source-agreements --accept-package-agreements --disable-interactivity; if ($LASTEXITCODE -ne 0) { throw "Git 自动安装失败" } }; $PyReady=$false; $PyCommand=(Get-Command py -ErrorAction SilentlyContinue); if ($PyCommand) { $PyProbe=$PyCommand.Source; & $PyProbe -3.11 -c "import sys" 2>$null; $PyReady=($LASTEXITCODE -eq 0) }; if (-not $PyReady) { & $WingetExe install --id Python.Python.3.11 -e --source winget --silent --accept-source-agreements --accept-package-agreements --disable-interactivity; if ($LASTEXITCODE -ne 0) { throw "Python 3.11 自动安装失败" } }; $env:Path=[Environment]::GetEnvironmentVariable("Path","Machine")+";"+[Environment]::GetEnvironmentVariable("Path","User"); $Git=(Get-Command git -ErrorAction Stop).Source; $Py=(Get-Command py -ErrorAction Stop).Source; & $Py -3.11 -c "import sys; assert sys.version_info[:2] == (3, 11)"; if ($LASTEXITCODE -ne 0) { throw "Python 3.11 核验失败" }; $Root=Join-Path ([Environment]::GetFolderPath("MyDocuments")) "OculiDoC-Development"; $Repo=Join-Path $Root "OculiDoC"; New-Item -ItemType Directory -Force -Path $Root | Out-Null; if (-not (Test-Path (Join-Path $Repo ".git"))) { if (Test-Path $Repo) { throw "目标目录已存在但不是 Git 仓库：$Repo" }; & $Git clone --branch feature/gaze-tasks-mvp --single-branch https://github.com/Etymodes/OculiDoC.git $Repo; if ($LASTEXITCODE -ne 0) { throw "克隆 OculiDoC 失败" } }; Set-Location $Repo; & $Py -3.11 -m venv .venv; if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败" }; $VenvPython=Join-Path $Repo ".venv\Scripts\python.exe"; & $VenvPython -m ensurepip --upgrade; if ($LASTEXITCODE -ne 0) { throw "在虚拟环境中补齐 pip 失败" }; & $VenvPython -m pip install --upgrade pip; if ($LASTEXITCODE -ne 0) { throw "升级 pip 失败" }; & $VenvPython -m pip install -e ".[dev]"; if ($LASTEXITCODE -ne 0) { throw "安装 OculiDoC 失败" }; Write-Host "OculiDoC 安装完成：$Repo" -ForegroundColor Green
```

已有 Python 环境且仓库已经克隆时：

```powershell
python -m pip install -e . --no-deps
```

## 测试

```powershell
python -m pytest
```

## 启动桌面程序

```powershell
python -m oculidoc
```

## 启动本地后台

```powershell
python -m oculidoc.api
```

健康检查：

```text
http://127.0.0.1:8000/health
```

API 文档：

```text
http://127.0.0.1:8000/docs
```

桌面程序会自动启动局域网后台。鼠标悬停或点击主界面底部的
“本地后台”状态，可显示带短期配对令牌的二维码。手机与电脑
连接同一局域网后扫码，可进行文字投屏、待机恢复和任务预览。

局域网控制默认不返回完整患者身份，不应将端口暴露到公网。

## 数据安全

患者身份、实验记录、眼动轨迹、数据库、日志和导出 CSV 不得提交到 GitHub。完整 CSV 可能包含视频等大文件，也包含敏感患者数据，应只保存到受控存储并按医院制度传输。运行数据默认存放于被 Git 忽略的 `var/`。
