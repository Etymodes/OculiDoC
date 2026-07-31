<p align="center">
  <img src="src/oculidoc/assets/brand_wordmark_blue.png" width="460" alt="OculiDoC">
</p>

<p align="center">
  面向意识障碍患者的眼动仪操作界面与实验数据台
</p>

<p align="center">
  Windows 10/11 · Python 3.11 · 开发版本 v0.1.2
</p>

> **v0.1.1 公开评估许可**
>
> 发布者已确认有权在官方 v0.1.1 源码与发行包中公开分发 76 张刺激图和品牌资源。
> 公众可下载、安装和运行官方 v0.1.1，**仅限非临床工程评估**。本版本不是医疗器械，
> 也不代表医院或科室的官方发布、认证或背书。未明确授予的权利仍予保留，包括拆出或
> 独立复用素材、修改、重新打包、再发布、商业服务及临床使用。完整边界见
> [LICENSE-v0.1.1.txt](LICENSE-v0.1.1.txt) 与 [NOTICE.md](NOTICE.md)。

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
| 3 | 眼动游戏 | 点亮花园、视觉寻宝与自适应星光航线 |
| 4 | 随指令注视 | 目标 AOI、潜伏期、最长连续注视与干扰区表现 |
| 5 | 语音图片选择 | 目标、干扰图、选择位置、反应时间与正确性 |
| 6 | 左右二分问答 | 左右答案、停留确认、错误尝试与评分 |
| 7 | 上下二分问答 | 上下答案、停留确认、错误尝试与评分 |
| 8 | 多选项问答 | 选择、取消、最终集合与手动结束状态 |
| 9 | 屏幕打字 | 高频需求直选、分步拼音输入与最终文本 |

内置图库包含 76 张透明背景刺激图。所有已实现的患者交互任务支持自动语音播报，
任务完成、人工退出、眼动中断和异常状态分别记录，不以模拟数据替代真实设备断流。

### v0.1.2 开发中：星光航线

星星以呼吸发光和缓慢旋转吸引注视。游戏从中央安全区开始，根据有效命中连续升级，
在有效表现降低时回退；每隔若干轮向四侧边界试探，逐步学习患者实际可达区域。
眼动断流或有效采样不足单独记为无效轮次，不作为患者能力下降，也不触发降级。

## OpoinThesis

`OpoinThesis` 是项目内的有意构词：取古希腊语形式 `ὀποῖν θέσις`
（`opoîn thésis`，项目释义为“双眼的位置”），同时在英语听感上近似
`open thesis`，形成“开放研究”的双关。v0.1.1 中它仍是主观眼位检查：不自动评分、
不保存结论、不进入正式患者报告；其接口为未来合规接入智能眼位分析、患者个体化自适应
及 EEG/BCI 同步研究保留扩展位置。

Tobii 官方工具、设备许可、免费 SDK 范围与非破坏性接入顺序见
[Tobii 官方接入边界](TOBII_OFFICIAL_INTEGRATION.md)。OculiDoC 不捆绑 Tobii 软件、
专有 DLL、许可证或密钥。

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

PowerShell 一行启动仍保留作应急方案。以下命令会下载正式安装器及其 SHA-256 文件，
在本机核验一致后再启动图形安装界面：

```powershell
$ErrorActionPreference="Stop"; $b="https://github.com/Etymodes/OculiDoC/releases/latest/download"; $p=[IO.Path]::Combine([IO.Path]::GetTempPath(),"OculiDoC-Setup.exe"); Invoke-WebRequest "$b/OculiDoC-Setup.exe.sha256" -UseBasicParsing -OutFile "$p.sha256"; Invoke-WebRequest "$b/OculiDoC-Setup.exe" -UseBasicParsing -OutFile $p; $e=((Get-Content "$p.sha256" -Raw).Trim() -split "\s+")[0].ToLowerInvariant(); if($e -notmatch "^[0-9a-f]{64}$" -or (Get-FileHash $p -Algorithm SHA256).Hash.ToLowerInvariant() -ne $e){throw "安装包 SHA-256 校验失败"}; Start-Process -FilePath $p
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

请通过仓库的 [GitHub Issues](https://github.com/Etymodes/OculiDoC/issues) 提交公开、
可复现的问题，并附上：

- OculiDoC 版本号和 Windows 版本；
- 使用的眼动设备与数据源；
- 问题发生前后的操作；
- 报错截图或日志；
- 是否涉及真实患者数据（邮件中不得直接附患者身份或原始数据）。

## 使用边界

OculiDoC v0.1.1 是面向非临床工程评估的 pre-1.0 平台，不是医疗器械，不得用于任何
真实患者或临床用途，包括诊断、预后、治疗决策或临床服务。任何未来版本的真实患者研究
都必须另行取得伦理、知情同意、硬件及软件授权，并针对实际电脑、显示器、驱动、病房
环境和操作流程完成独立现场确认。
患者身份、实验记录、眼动轨迹、数据库、日志和导出文件不得提交到 GitHub。

v0.1.1 的实时视线坐标要求眼动仪校准显示器与全屏任务所在显示器为同一固定显示器；
任务进行中不得移动窗口、切换显示器或更改显示缩放。多显示器部署必须先完成现场坐标
确认，不能把单元测试或模拟 gaze 通过视为床旁定位精度通过。

详见 [NOTICE.md](NOTICE.md)。
