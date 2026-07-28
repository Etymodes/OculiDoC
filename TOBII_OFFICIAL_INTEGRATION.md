# Tobii 官方工具、SDK 与 OculiDoC 接入边界

复核日期：2026-07-28

本文只依据 Tobii 官方产品页、开发文档、许可页和官方代码库。它记录技术与依赖决策，
不替代 Tobii 的书面授权或法律意见。官网后续变更时，以届时条款为准。

## 结论

“免费下载”不等于“可用于任意设备、研究、医疗场景或数据存储”。对 OculiDoC 而言：

- v0.1.1 不新增 Tobii Python 包，不捆绑 Tobii DLL、应用、许可证或密钥。
- 当前已验收的 Stream Engine/兼容桥接保持原状并继续能力降级，避免破坏既有功能；
  但普通 Tobii Eye Tracker 5 的现行终端许可不能作为保存研究数据的授权依据。
- 正式研究接入优先路线是 Tobii Pro 设备 + Tobii Pro SDK；商业/集成路线是
  Tobii Eye Tracker 5L + Tobii Streams SDK。二者都必须在设备和授权到位后实机实现。
- Tobii Pro Glasses 3 API 可作为未来可穿戴路线，不与当前屏幕式适配器混写。

## 免费或随设备提供的官方能力

| 工具或 SDK | 官方状态与适用范围 | OculiDoC 决策 |
| --- | --- | --- |
| [Tobii Pro SDK](https://www.tobii.com/products/software/applications-and-developer-kits/tobii-pro-sdk) | SDK 免费；面向 Pro Spectrum、Fusion、Spark、Nano 等研究型屏幕眼动仪，可提供逐眼注视、三维 gaze origin、瞳孔、时间戳及部分设备的同步/眼睑信号 | 最高优先级的研究适配路线；当前不安装 |
| [Tobii Pro Eye Tracker Manager](https://www.tobii.com/products/software/applications-and-developer-kits/tobii-pro-eye-tracker-manager) | 免费外部工具；用于 Pro 设备配置、track status、校准、采样模式、固件和诊断 | 未来作为外部配置/校准工具调用，不嵌入、不打包 |
| [Tobii Pro Glasses 3 API](https://www.tobii.com/products/software/applications-and-developer-kits/tobii-pro-sdk/tobii-pro-glasses-3-API) | 免费、仅支持 Glasses 3；使用 HTTP、WebSocket、WebRTC、RTSP，可控制校准/录制并读取实时或离线数据 | 独立的未来可穿戴适配器；不复用屏幕坐标假设 |
| [Glasses 3 Controller](https://www.tobii.com/products/eye-trackers/wearables/tobii-pro-glasses-3/controller-app) | 随 Glasses 3 提供的可下载控制工具，可校准、录制、实时观察和导出 | 仅作为外部操作工具，不作为代码依赖 |
| [Tobii Pro 官方开源仓库](https://github.com/tobiipro) | 含校准验证附加组件、PyGaze/示例和 Glasses 3 客户端；部分仓库已归档 | 只借鉴公开接口和测试思想；不直接引入已归档库 |
| [Tobii Experience 与 Eye Tracker 5 驱动](https://help.tobii.com/hc/en-us/articles/360009325857-Installation-or-setup-issues-for-Tobii-Eye-Tracker-5) | 普通 Eye Tracker 5 的免费安装、显示设置、校准和运行时 | 保持为现场外部前置条件；不能据此推定研究存储授权 |
| [Game Hub 与 Ghost](https://gaming.tobii.com/getstarted/) | 免费游戏/直播附加工具 | 不接入临床或研究数据链 |
| Tobii Eye Tracking Core Software | 旧 EyeX/4C 等设备的免费旧运行时；官方页面注明不兼容 Windows 11 | 不新增接入，仅保留历史现场排障知识 |

## 可获取但不属于当前免费研究接入

| 项目 | 限制 | 决策 |
| --- | --- | --- |
| [Tobii Game Integration API](https://developer.tobii.com/pc-gaming/downloads/) | 面向游戏工作室和消费者游戏体验，不是 DoC 分析 SDK | 不接入 |
| [旧 Eye Tracking/EyeX SDK](https://developer.tobii.com/eyex-sdk-/) | 旧版且限交互/游戏用途；分析、医疗与高风险用途另需许可 | 不接入 |
| [Tobii Streams SDK](https://www.tobii.com/products/integration/tobii-streams-sdk) | 付费开发许可；官方支持的是 5L、Nexus 等集成产品，并明确普通 Eye Tracker 5 不能用于开发 | 获得 5L 和对应许可后复用现有 Stream Engine 适配层扩展 |
| [Tobii Nexus](https://www.tobii.com/products/integration/screen-based-integrations/tobii-nexus/tobii-nexus-free-trial) | 免费内容是 30 天评估，不是长期免费开发或研究许可 | 不写入正式依赖；可在隔离 PoC 中评估 |
| [Tobii Ocumen](https://developer.tobii.com/xr//ocumen/overview/) | 需 Ocumen 许可，主要面向 XR/眼动生物标志物 | 当前不接入 |
| Tobii Pro Lab、Sticky、Glasses Explore | 完整实验/分析产品，不是免费开发依赖 | 不纳入代码依赖 |

## 关键许可边界

Tobii 当前页面对普通 Eye Tracker 5 与 5L 作了明确区分：

- [Streams SDK 产品页](https://www.tobii.com/products/integration/tobii-streams-sdk)说明，
  普通 Eye Tracker 5 是纯游戏设备，不能用于开发；可开发型号是 Eye Tracker 5L。
- [Eye Tracker 5 终端许可](https://gaming.tobii.com/tobii-eye-tracking-end-user-license-use-agreement/)
  将 Interactive Use 限定为交互输入，并排除眼动数据的存储或向其他设备/网络传输。
- [Tobii 研究开发许可](https://www.tobii.com/products/integration/tobii-sdk-license)
  面向低规模、非商业研究，但要求搭配 Tobii Pro 产品。

因此，现有普通 Eye Tracker 5 路径只能保留为不捆绑第三方组件的现场兼容代码；在 Tobii
书面许可或合规研究硬件到位前，不把它扩展为新的研究数据能力，也不把“驱动能返回数据”
解释为“允许保存和分析数据”。

## 依赖写入策略

### v0.1.1：保持零新增 SDK 依赖

OculiDoC 固定使用 Python 3.11，而 Tobii Pro SDK 官方产品页当前列出的 Python 支持为
3.8 和 3.10。虽然官方 Python 文档给出了 `tobii_research` 包名，但现在直接写入
`pyproject.toml` 会把未经验证且版本不匹配的二进制依赖带进所有安装。

所以 v0.1.1 的依赖决策是：

- 不加入 `tobii_research`；
- 不加入 Game Integration、Nexus、Ocumen 或 Glasses 3 客户端；
- 继续仅用 Python 标准库 `ctypes` 发现现场已合法安装的原生库；
- 所有可选数据流继续按“缺失/拒绝即降级”处理；
- `pyproject.toml` 只记录实际被应用导入和测试的依赖。

### 获得 Pro 设备后的第一适配器

新增 `TobiiProSdkDevice`，实现现有 `EyeTrackerDevice` 接口，并保持上层任务、报告和患者
数据库不变：

| Tobii Pro 信号 | 现有 OculiDoC 字段或扩展点 |
| --- | --- |
| 左右眼 gaze point 与有效性 | `EyeTrackerSample` 的逐眼/组合注视字段 |
| gaze origin 3D | `left_eye_position_mm` / `right_eye_position_mm` |
| pupil diameter | 现有左右瞳孔字段 |
| device/system timestamp | `DeviceTimestamp` 与现有同步模块 |
| eye openness、eye image、external signal | 后续可选能力；不得伪造为普通设备已有信号 |

首选实现顺序：

1. 等官方 Python binding 支持 Python 3.11 后，再增加单独的 `tobii-pro` 可选依赖组；
2. 若硬件先到而 Python binding 仍不支持 3.11，使用官方 C binding 加标准库 `ctypes`，
   原生文件由医院单独安装和授权，不进入仓库或发行包；
3. 只有适配器、无硬件单元测试、实机 smoke test 和许可复核同时完成，才进入自动检测。

### Glasses 3 与 EEG/BCI

Glasses 3 应作为网络设备适配器，把 HTTP/WebSocket 数据先规范化到
`EyeTrackerSample`，再交给 OpoinThesis 或任务层。只有适配器真正实现时才加入明确的
WebSocket 客户端依赖，不能依赖 Uvicorn 的传递依赖。

EEG/BCI 不直接耦合进 Tobii 驱动。眼动和 EEG 都写入各自时间戳与同步事件，由现有同步层
对齐。OpoinThesis 只提供当前眼位状态和未来模型输入接口，不自行作意识诊断。

## OpoinThesis 在路线中的位置

`OpoinThesis` 是项目内的有意构词：古希腊语形式 `ὀποῖν θέσις`
（`opoîn thésis`，项目释义“双眼的位置”）与英语听感 `open thesis`
（开放研究）构成双关。

当前 v0.1.1 只做主观眼位显示、短时丢失补偿、头部倾角和可用时的距离参考；不评分、
不持久化、不进入报告。未来经合规设备提供真实信号后，再按同一扩展缝加入：

1. 智能眼位质量与遮挡分析；
2. 患者可视区域、边界与疲劳的个体化自适应；
3. 跨日个体模型；
4. 与 EEG/BCI 的同步特征和联合研究接口。
