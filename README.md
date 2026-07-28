<p align="center">
  <img src="src/oculidoc/assets/brand_wordmark_blue.png" width="460" alt="OculiDoC">
</p>

<p align="center">
  面向意识障碍患者的眼动仪操作界面与实验数据台
</p>

<p align="center">
  Windows 10/11 · Python 3.11 · 当前版本 v0.1.0
</p>

> **权属与使用限制**
>
> OculiDoC 为**首都医科大学天坛医院意识障碍病区所有**。未经书面授权，不得擅自复制、
> 传播、改作、再发布或用于商业及临床服务。需要使用本软件或报告问题，请联系
> [he_jianghong@sina.cn](mailto:he_jianghong@sina.cn?cc=peterpig123456@gmail.com&subject=OculiDoC)
> 并抄送 [peterpig123456@gmail.com](mailto:peterpig123456@gmail.com)。

## 软件界面

| 区域 | 用途 |
| --- | --- |
| 患者工作台 | 选择、停用和管理患者；查看当前测试进度、下一任务与最近结果 |
| 编排本次测试 | 按意识状态进阶顺序选择任务；眼动采集与复核为可选项 |
| 患者显示端 | 独立显示刺激、倒计时、任务状态与大字提示 |
| 手机控制端 | 局域网内设置任务、直接启动、重播语音和投送提示 |
| 设备设置 | 选择原生、兼容或模拟眼动来源；执行实时视线自检与任务前预检 |
| 实验历史与报告 | 保存逐试次结果、热力图、轨迹、数据质量和患者跨次趋势 |

总设置可在默认“患者工作台”和原有“经典皮肤”之间切换，不改变既有数据与任务逻辑。

## 十个功能入口

| 编号 | 功能 | 主要记录 |
| --- | --- | --- |
| 0 | 眼动采集与复核（可选） | 摄像头画面、双眼区域与人工复核结果 |
| 1 | 视觉偏好 | 成对换边后的图片关注与固定侧偏 |
| 2 | 追踪球 | 注视时长、有效率与视线—目标轨迹匹配 |
| 3 | 眼动游戏 | 点亮花园与视觉寻宝两种模式 |
| 4 | 随指令注视 | 目标 AOI、潜伏期、最长连续注视与干扰区表现 |
| 5 | 语音图片选择 | 目标、干扰图、选择位置、反应时间与正确性 |
| 6 | 左右二分问答 | 左右答案、停留确认、错误尝试与评分 |
| 7 | 上下二分问答 | 上下答案、停留确认、错误尝试与评分 |
| 8 | 多选项问答 | 选择、取消、最终集合与手动结束状态 |
| 9 | 屏幕打字 | 高频需求直选、分步拼音输入与最终文本 |

内置图库包含 76 张透明背景刺激图。所有已实现的患者交互任务支持自动语音播报，
任务完成、人工退出、眼动中断和异常状态分别记录，不以模拟数据替代真实设备断流。

## 安装

### Windows 安装包（推荐）

适用于没有 Git、Python 或开发环境的 Windows 10/11 电脑。直接下载并双击
[OculiDoC-Setup.exe](https://github.com/Etymodes/OculiDoC/releases/latest/download/OculiDoC-Setup.exe)。
安装界面可以选择：

- **在线安装最新版本**：下载并核验 GitHub 最新正式安装包；
- **离线安装当前版本**：使用安装包内置文件，不需要联网。

安装器会识别已有标准安装和旧便携版默认目录，显示并沿用原路径；也可以自选路径。
安装完成后自动创建桌面及开始菜单快捷方式，并可在 Windows“已安装的应用”中卸载。

### 0 依赖便携版

不希望登记安装信息时，可从 [Releases](https://github.com/Etymodes/OculiDoC/releases)
下载 `OculiDoC-*-windows-x64-portable.zip`，解压后运行 `OculiDoC.exe`。

旧式 PowerShell 一行安装仍保留作应急方案。以下命令不包含易受路径空格和括号影响的
本地路径拼接，并会安装到当前用户目录、创建桌面快捷方式：

```powershell
irm "https://github.com/Etymodes/OculiDoC/releases/latest/download/Install-OculiDoC.ps1" | iex
```

### 已克隆源码版

在仓库根目录执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

安装脚本固定使用仓库内 `.venv`，不会读写旧 `ops` 环境，也不会删除 `data` 或 `var`。

## 一键检查与更新

源码版完整自检：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1
```

源码版安全更新：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\update.ps1
```

更新仅接受官方仓库 `main` 分支的干净工作区和快进合并；发现本地修改或分叉时会停止。
安装版更新请重新运行安装器并选择“在线安装最新版本”；便携版可重新运行应急命令。

## 上报 Bug

请发送邮件至 [he_jianghong@sina.cn](mailto:he_jianghong@sina.cn?cc=peterpig123456@gmail.com&subject=OculiDoC%20Bug)
并 **CC** [peterpig123456@gmail.com](mailto:peterpig123456@gmail.com)，附上：

- OculiDoC 版本号和 Windows 版本；
- 使用的眼动设备与数据源；
- 问题发生前后的操作；
- 报错截图或日志；
- 是否涉及真实患者数据（邮件中不得直接附患者身份或原始数据）。

## 使用边界

OculiDoC 当前是内部科研与工程平台，不是医疗器械，不能单独用于诊断、预后或治疗决策。
首次真实患者使用前，必须使用具有适用授权的合规硬件，并针对实际电脑、显示器、驱动、
病房环境和操作流程完成独立现场确认。患者身份、实验记录、眼动轨迹、数据库、日志和导出
文件不得提交到 GitHub。

详见 [NOTICE.md](NOTICE.md)。
