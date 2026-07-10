# OculiDoC

OculiDoC 是面向意识障碍患者的眼动评估、交互、沟通与训练平台。

联合开发者：

- Etymodes
- TiantanDoC（首都医科大学天坛医院意识障碍团队）

## 当前里程碑

Milestone 0.1 建立：

- PySide6 管理员主窗口；
- 独立患者显示端；
- 七类实验项目注册表；
- 模拟眼动数据源；
- FastAPI 本地健康检查；
- 自动化测试；
- 患者数据与源码仓库隔离规则。

当前版本尚未接入真实 Tobii 数据，不能用于临床决策。

## 安装

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

## 数据安全

患者身份、实验记录、眼动轨迹、数据库、日志和导出文件不得提交到 GitHub。运行数据默认存放于被 Git 忽略的 `var/`。