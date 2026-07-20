# OculiDoC

OculiDoC 是面向意识障碍患者的眼动评估、交互、沟通与训练平台。

联合开发者：

- Etymodes
- TiantanDoC（首都医科大学天坛医院意识障碍团队）

## 当前里程碑：M3D11C3

M3D11C3 已完成患者显示端统一状态机。在原有 engineering validation build
基础上，当前能力包括：

- PySide6 管理员主窗口和独立患者显示端；
- 患者、实验会话与结构化任务记录；
- 眼动采集与复核工作台；
- 追踪球和左右二分问答；
- Tobii Eye Tracker 5 原生 Stream Engine 数据源；
- 模拟眼动和兼容桥接数据源；
- FastAPI 本地后台基础入口；
- 追踪球与左右二分问答共享、版本化、原子保存的任务配置；
- 手机端保存设置并直接启动任务，桌面端校验配置版本；
- 设置冲突返回最新版本，不静默覆盖另一端修改；
- 患者显示端常驻，并与桌面端、手机端共享原子状态文件；
- 任务在管理员确认设置后显示 READY 与 3 秒倒计时，再进入 RUNNING；
- 任务完成、取消和异常分别进入 RESULT、IDLE 和 ERROR；
- 手机端与桌面端均可投送患者大字提示，运行中状态不可被普通投屏覆盖；
- Windows PyInstaller 打包和自动化测试；
- 患者数据与源码仓库隔离规则。

当前版本已经完成真实 Tobii 工程验证，但仍不是医疗器械或临床正式版本，
不能作为单独的诊断、预后或治疗依据。

## 安装

新电脑开发安装前需已有 Git 和 Python 3.11。在 PowerShell 中一行完成克隆、
创建虚拟环境和安装开发依赖：

```powershell
git clone --branch feature/gaze-tasks-mvp --single-branch https://github.com/Etymodes/OculiDoC.git OculiDoC; if ($LASTEXITCODE -ne 0) { throw "克隆失败" }; Set-Location OculiDoC; py -3.11 -m venv .venv; if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败" }; & .\.venv\Scripts\python.exe -m pip install --upgrade pip; if ($LASTEXITCODE -ne 0) { throw "升级 pip 失败" }; & .\.venv\Scripts\python.exe -m pip install -e ".[dev]"; if ($LASTEXITCODE -ne 0) { throw "安装 OculiDoC 失败" }
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
