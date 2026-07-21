# OculiDoC

OculiDoC 是面向意识障碍患者的眼动评估、交互、沟通与训练平台。

联合开发者：

- Etymodes
- TiantanDoC（首都医科大学天坛医院意识障碍团队）

## 当前里程碑：M3D12B

M3D12B 已完成上下二分问答 demo，并保留 M3D12A 的屏幕打字与自动语音播报。
在原有 engineering validation build 基础上，当前能力包括：

- PySide6 管理员主窗口和独立患者显示端；
- 患者、实验会话与结构化任务记录；
- 眼动采集与复核工作台；
- 追踪球、左右/上下二分问答和分阶段拼音屏幕打字；
- 上下二分问答复用左右任务的问题库、字体、停留、计时、评分和语音设置，仅把选项与中性区改为纵向；
- 上下任务使用视线纵坐标判定顶部/底部选项，独立保存版本化配置，并记录上下 AOI 与布局方向；
- 屏幕打字按“声母 → 确认 → 韵腹/组合韵母 → 确认 → 韵尾 → 确认 → 可选声调 → 确认”输入；
- 上半屏保留已输入文字，下半屏使用大尺寸选项，并提供删除、空格、朗读和清空；
- 屏幕打字的停留阈值、任务时长、声调步骤和三类字号可在电脑端或手机端调整；
- 输入过程、最终文本和每个阶段的眼动 AOI 均进入患者实验记录；
- 输入结果通过患者显示状态同步到独立患者端和手机端；
- 所有已实现眼动任务默认自动语音播报，手机端可发送“重播语音”命令；
- 追踪球开始时播报易理解的跟随提示，运行中不反复打扰患者；
- Tobii Eye Tracker 5 原生 Stream Engine 数据源；
- 模拟眼动和兼容桥接数据源；
- FastAPI 本地后台基础入口；
- 追踪球、左右/上下二分问答与屏幕打字共享版本化、原子保存的任务配置；
- 手机端保存设置并直接启动任务，桌面端校验配置版本；
- 设置冲突返回最新版本，不静默覆盖另一端修改；
- 患者显示端常驻，并与桌面端、手机端共享原子状态文件；
- 任务在管理员确认设置后显示 READY 与 3 秒倒计时，再进入 RUNNING；
- 任务完成、取消和异常分别进入 RESULT、IDLE 和 ERROR；
- 手机端与桌面端均可投送患者大字提示，运行中状态不可被普通投屏覆盖；
- 软件内选择并原子保存眼动源、Stream Engine DLL 与 3–10 秒预检策略；
- 自动发现 Tobii Stream Engine DLL，并提供 Tobii Experience 校准入口；
- 任务前强制采样预检，有效率低于阈值时阻止任务且不回退 mock；
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

患者身份、实验记录、眼动轨迹、数据库、日志和导出文件不得提交到 GitHub。运行数据默认存放于被 Git 忽略的 `var/`。
