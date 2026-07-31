# OculiDoC v0.1.1

v0.1.1 是 OculiDoC 的 Windows pre-1.0 正式轻发布。

## 本版内容

- 整合患者工作台、测试编排、独立患者显示端、手机局域网控制端、实验历史与报告链。
- 提供 0–9 共十个功能入口：眼动采集与复核、视觉偏好、追踪球、眼动游戏、随指令注视、
  语音图片选择、左右／上下二分问答、多选项问答和屏幕打字。
- 为 1–9 号患者交互任务加入可配置实时视线光标，并修正全屏 gaze、任务局部坐标和
  AOI 记录之间的映射。
- 保留 OpoinThesis 主观眼位辅助和 Tobii 可选数据源兼容；OpoinThesis 本版不自动评分、
  不保存结论，也不进入正式患者报告。
- 新增标准安装、便携包、安全更新、桌面／开始菜单快捷方式和卸载入口，并修复中文
  PowerShell 脚本在 Windows PowerShell 5.1 下的解析问题。

## 下载

- `OculiDoC-Setup.exe`：Windows 图形安装器，支持在线安装最新版本与离线安装当前版本。
- `OculiDoC-v0.1.1-windows-x64-portable.zip`：免安装便携版。
- 同名 `.sha256`：对应文件的 SHA-256 完整性校验。
- `OculiDoC_release_manifest.json`：发行包版本、大小和内容计数。
- `OculiDoC_bundle_signing_inventory.json`：冻结目录的 Authenticode 状态与逐文件 SHA-256
  清单。
- `LICENSE-v0.1.1.txt`、`NOTICE.md` 与 `THIRD_PARTY_NOTICES.md`：本版有限许可、
  使用边界与第三方归属说明；安装器和便携包内也包含这些文件。

## v0.x 轻发布信任边界

本次资产由 `Etymodes/OculiDoC` 的 GitHub Actions 构建，并使用 GitHub/Sigstore
构建来源证明和 SHA-256 校验。它们可以验证文件完整性与构建来源，但不等同于医院主体的
Windows Authenticode 可信发布者签名。本渠道不代表医院或科室的官方发布、认证或背书。

发布者已确认有权在官方 v0.1.1 源码与发行包中公开分发 76 张刺激图和品牌资源。
公众可下载、安装和运行官方 v0.1.1，仅限非临床工程评估；不得用于任何真实患者或临床
用途。这不授予拆出或独立复用素材、
修改、重新打包、再发布、商业服务或临床使用的权利。完整有限许可见 `NOTICE.md`。

安装 GitHub CLI 后，可在下载目录运行
`gh attestation verify OculiDoC-Setup.exe -R Etymodes/OculiDoC`
核验安装包的 GitHub/Sigstore 构建来源证明。

因此 Windows 可能显示“未知发布者”，SmartScreen 或 Smart App Control 也可能阻止
运行。不要关闭 Windows 安全策略来绕过系统提示。医院级可信发布者签名与对应门禁顺延至
v1.0 及以后。

## 使用边界

- 当前版本仅供非临床工程评估，不是医疗器械，不得用于任何真实患者或临床用途，包括
  诊断、预后、治疗决策或临床服务。
- Tobii SDK、运行时、驱动、许可证及专有 DLL 不随 OculiDoC 分发；使用者必须自行确认
  具体设备与用途的适用授权。
- 本次自动化验证不等同于真实 Tobii 设备验收，也不等同于意识障碍患者床旁定位精度或
  临床有效性验收。
- 实时视线坐标要求眼动仪校准显示器与全屏任务所在显示器为同一固定显示器；任务期间
  不得移动窗口、切换显示器或更改显示缩放。

完整安装、功能和数据边界见仓库 `README.md`、`NOTICE.md` 与
`TOBII_OFFICIAL_INTEGRATION.md`。
