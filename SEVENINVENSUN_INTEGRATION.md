# 七鑫易维 aSee 眼镜直连与本机桥契约

状态：已实现 aSeeGlasses Plus 的 P0 直采探针和独立三点场景校准器；A8 明确不在本阶段
支持范围。两者直接加载完整厂商 SDK，不需要启动 aSeeStudio。未经验证的场景坐标仍不会进入
OculiDoC 患者屏任务。

P0 是仓库内的独立、一次性硬件验收进程，不是已由 Qt 主程序托管的常驻桥。每次 SDK 会话
必须启动全新的探针进程并由进程退出完成 DLL 卸载；禁止在 OculiDoC Qt 主进程中直接实例化
SDK host，也禁止在一个进程中切换 Studio、A8 或其他 aSee 运行时。

## 已确认的 SDK 边界

本次取得的完整 `aSeeGlassesPlusUserSDK` 包含 `_7i_sdk.h`、`_7i_types.h`、x64 导入库、
运行时和 Python/C++ 示例。其公开入口是 `aSeeX.dll`，Windows 调用约定为 `__stdcall`；
扩展眼动回调结构 `_7i_eye_data_ex_t` 为 1376 字节，包含双眼 gaze、pupil、eye-extra、
fixation/saccade 状态和 IMU。Studio 自带的运行时（包括其 `USERSDK.dll`）属于另一套版本，
不能与公开 SDK 包混用。

P0 对完整 SDK 的 18 个 `bin/*.dll` 和 6 个配置文件建立 24 项精确 allowlist，并在构造时
及 `LoadLibrary` 前校验 SHA-256，同时拒绝 `bin` 中的额外 DLL。两个主要版本锚点是：

- `bin/aSeeX.dll`：`8216ba265fbfe073276b7d09ee2eb9622dbace5de48f2e3d5c04fbebc423b0eb`
- `bin/USERSDK.dll`：`ccba07dd68fd04d67d825fb98b97312aa1758514fe3e017846a4cd400760b08e`

厂商 SDK、授权口令、UKey 信息、序列号和校准系数都属于外部现场资产，不提交到仓库，
也不复制进 OculiDoC 安装包。SDK 在独立探针进程中运行，避免未知设备异常拖垮 Qt 主进程。
原始 `bin/config/config.ini` 也在 allowlist 内，必须保持空口令模板不变；实际授权口令只经
隐藏提示传给 `_7i_device_connect`。头文件声明的版权与许可正文保存在发行许可目录中。

## 直接硬件探针

先关闭 aSeeStudio，连接 aSeeGlasses Plus 到 USB 3.0，然后在 OculiDoC 仓库中运行 ABI 自检：

```powershell
.\.venv\Scripts\python.exe scripts\check_seveninvensun_glasses_live.py `
  --sdk-root "C:\VendorSDK\aSeeGlassesPlusUserSDK" `
  --abi-only
```

通过 wheel/pip 安装 OculiDoC 后也可使用等价命令 `oculidoc-asee-glasses-probe`；仓库脚本
只是这个包内 helper 的薄包装，不会经 `oculidoc.devices` 导入 OpenCV/NumPy 或 Qt。当前
PyInstaller `OculiDoC-Setup.exe` 尚未生成独立探针 EXE，P0 现场验收应使用仓库虚拟环境或
wheel；安装包接入与 Qt supervisor 属于下一阶段。

ABI 通过后进行 30 秒直接采集。授权口令只通过隐藏提示输入，不允许作为命令行参数：

```powershell
.\.venv\Scripts\python.exe scripts\check_seveninvensun_glasses_live.py `
  --sdk-root "C:\VendorSDK\aSeeGlassesPlusUserSDK" `
  --duration 30 `
  --prompt-authorization `
  --output-dir "D:\OculiDoCResearchData\hardware_acceptance"
```

需要复用本校准器生成的系数时，禁止传入裸 `left/right` 文件；必须同时使用
`--calibration-bundle` 和同一患者的 `--profile-id`。探针会在任何 `_7i_set_data` 前核对患者、
眼镜 MPU、完整 24 项运行时、SDK 版本、环境、分辨率和系数哈希。输出包括：

- `asee_glasses_raw_*.ndjson`：每个已消费 SDK 回调一行，含有效位、已定义字段和主机双时间戳；
- `asee_glasses_report_*.json`：采样率、字段覆盖率、丢帧数、图像尺寸和 IMU 时间戳变化；
- ABI 模式标记 `OCULIDOC_SEVENINVENSUN_GLASSES_ABI=PASS|FAIL`，并明确
  `OCULIDOC_SEVENINVENSUN_GLASSES_DIRECT=NOT_RUN`；
- 真机模式仅在回调接收/写入/队列/丢弃计数守恒，且采样率、时间戳、核心字段、三路图像
  元数据、IMU 和清理结果都满足门槛时输出 `OCULIDOC_SEVENINVENSUN_GLASSES_DIRECT=PASS`，
  否则为 `REVIEW_REQUIRED` 或 `FAIL`。

真机模式强制显式填写 `--output-dir`。原始文件含生物特征数据，只能放在受控研究数据目录，
不应提交 Git；场景到任务屏映射验收完成前，只能使用健康志愿者/测试账户，不得采集真实患者。
原生 DLL 若使探针进程直接崩溃，来不及生成 JSON 报告；进程级 supervisor/超时重启属于
后续托管桥阶段。

## 三点场景校准

校准器是另一个一次性 child process，不会把厂商 DLL 加载进 OculiDoC 主 Qt 进程，也不导入
OpenCV 或 NumPy。它只接受厂商当前 SDK 支持的正式三点流程：每一点都用同一场景坐标分别
启动左眼和右眼，只有两眼 finish callback 均返回 `0` 才进入下一点。超时取消会等待左右眼各自
的终态回调；若取消终态不完整，则本进程失败退出，不会在未知原生状态上重试。

先关闭 aSeeStudio，插入 UKey，连接并佩戴 aSeeGlasses Plus。`--profile-id` 必须使用
OculiDoC 已有的 canonical opaque patient UUID；不得使用姓名、病历号或 `User1` 等低熵别名：

```powershell
.\.venv\Scripts\python.exe scripts\calibrate_seveninvensun_glasses.py `
  --sdk-root "C:\VendorSDK\aSeeGlassesPlusUserSDK" `
  --output-dir "D:\OculiDoCResearchData\calibration" `
  --profile-id "12345678-1234-4234-9234-123456789abc"
```

安装 wheel 后等价入口为 `oculidoc-asee-glasses-calibrate`。当前
`OculiDoC-Setup.exe` 不包含该校准器，需使用源码虚拟环境或重新安装含此入口的 wheel。
授权口令在 Qt 密码框中输入，不是命令行参数，也不会写入报告。只使用厂商明确交付的 UKey
login password；不能根据 `Sn:` 文本推定密码。

在场景画面中依次选择左上、右上、下方中央三个相互分离的现实目标。佩戴者注视目标，操作者
在场景画面中点击同一目标。校准器先把 Qt 等比例缩放/留黑边后的点击反算回场景原生像素，
再按以下厂商实际坐标约定提交：

```text
x_centered = x_top_left - scene_width / 2
y_centered = y_top_left - scene_height / 2
```

因此原点位于场景中央，`+X` 向右、`+Y` 向下。公开头文件把该参数误写成 normalized；厂商
手册的公式、示意图和 Qt/MFC/Python 三套示例实际都使用中心原点像素。场景回调同样以实际
示例为准：只把通道 `13` 的 H.265 载荷交给 `H265Decode.dll`；仅当返回值严格等于
`scene_width × scene_height × 3` 时才发布完整 BGR888 帧。手册写的通道 `10` 只记录元数据，
没有样例证明其可送入 H.265 解码器，因此不会作为校准画面。

三点均成功后，校准器从 `biLeft`、`biRight` 各取得 1024 字节，调用 `_7i_complete_calibration`
结束校准，再用本 SDK 示例采用的 `_7i_set_data` 路径临时应用。随后丢弃校准前队列并采集 5 秒
新鲜回调。技术复验只检查：至少 30 条新回调、recommended/left/right 各至少 10 条且不少于
20% 的有限帧内 gaze、设备时间戳严格递增、回调计数守恒、无队列丢弃/残留，并确认校准完成、
结束前 MPU 指纹仍与开始时一致、SDK stop、设备 disconnect 和解码器 release 成功。`stop()`
会与完整 gaze 拷贝/入队临界区建立屏障：已进入的回调必须结束，屏障后的回调只计数、不入队，
避免停止后的尾部回调绕过计数守恒。现场已观测到成功 `_7i_stop()==0` 后
`_7i_device_disconnect()==-22`（Device not found），且厂商 Qt/MFC/Python 示例在 stop 后不再调用
disconnect；因此仅在本轮授权已成功且 stop 明确返回 `0` 时，把原始 `-22` 记录为
`already_absent_after_successful_stop`。其他 disconnect 非零值仍阻止发布。该门槛只是输出有效性
保护，不是临床准确度或校准精度评分。

仅当复验为 `PASS` 时，输出目录才会用同目录临时文件和一次 `os.replace` 发布：

- `asee_glasses_<完整患者指纹>.aseecal`：一个 JSON bundle，内含 base64 编码的两份
  1024 字节不透明系数、各自哈希、三点坐标、复验统计，以及患者 UUID、MPU、完整运行时、
  SDK 版本、环境和分辨率的域分隔 SHA-256 绑定；
- 终端标记 `OCULIDOC_SEVENINVENSUN_GLASSES_CALIBRATION=PASS`。

同一患者的新 `PASS` 会原子替换其旧 bundle；发布失败或 `REVIEW_REQUIRED` 时旧 bundle 不变。
正常失败会删除本次临时文件；下次启动只清理由本工具命名且已超过 24 小时的残留临时文件，
不会删除可能仍在并发写入的新文件。报告不保存原始 UUID、MPU ID、授权口令、场景图或逐样本
gaze；SHA-256 指纹是绑定标识，不应被称为匿名化。系数属于眼部生物特征材料，只能留在受控
研究数据目录，不上传 Git 或公开共享。

校准通过只表示 SDK 能在眼镜场景相机坐标中输出有效 gaze。它不建立场景相机与患者副屏之间
的单应性，仍不得驱动 OculiDoC 患者任务。可用生成的绑定 bundle 再次运行 30 秒探针：

```powershell
.\.venv\Scripts\python.exe scripts\check_seveninvensun_glasses_live.py `
  --sdk-root "C:\VendorSDK\aSeeGlassesPlusUserSDK" `
  --duration 30 `
  --prompt-authorization `
  --calibration-bundle "D:\OculiDoCResearchData\calibration\asee_glasses_<指纹>.aseecal" `
  --profile-id "12345678-1234-4234-9234-123456789abc" `
  --output-dir "D:\OculiDoCResearchData\hardware_acceptance"
```

## Studio 导出列与直采字段

| Studio 列族 | P0 直采状态 | 来源或限制 |
| --- | --- | --- |
| Recording Time Stamp、Gaze Point Index | 部分直接 | SDK 原始时间戳单位尚未注明；序号由接收端严格递增 |
| Validity Eye/Left/Right | 直接 | 分别来自 recom/left/right gaze validity bit 0 |
| Pupil Position L/R | 直接 | SDK 归一化瞳孔中心；有眼图尺寸后同时给出像素值 |
| Pupil Diameter L/R px、mm | 直接 | 归一化长轴乘眼图宽度得到 px；mm 为 SDK 原值 |
| Openness、Eyelid Distance | 部分直接 | 开眼度与上下眼睑位置为 SDK 原值；距离 px 可由眼图高度计算，mm 不可得 |
| Gaze Point、Left/Right Gaze Point | 直接但仅场景坐标 | 中心原点场景图像像素；不得冒充患者屏坐标 |
| Gaze Origin、Gaze Direction | 直接原样 | SDK 有独立有效位；Studio 把 origin 列标为 mm，但公开头文件未承诺单位/坐标架 |
| Fixation/Saccade | 部分直接 | SDK 给在线状态、中心、持续时间与点数；Studio 的 index、速度、距离仍为派生 |
| Blink | 部分直接 | SDK 给每眼开/闭状态；blink index/duration 需 OculiDoC 后处理 |
| Quat、Gyro、Accel、Mag | 直接原样 | SDK 无有效位；时间单位、四元数顺序和各向量单位待现场核验 |
| IPD、gaze velocity、invalid 段、Valid Ratio | 不冒充原始值 | 需要定义并版本化派生算法 |
| Serial/TTL/UDP、Hot key、Annotation | 不属于眼动回调 | 属于 Studio/实验事件层，后续由 OculiDoC 事件系统产生 |
| Record Name、User Name、Time of Day、Video Time | 不属于设备原始值 | 由 OculiDoC 会话和主机时钟产生 |

缺失字段用 `null` 加 validity mask 表达，不使用 Studio 导出中的 `-1` 哨兵值。

## OculiDoC 入口

管理员在“眼动设备设置”中选择：

`七鑫易维 aSee（本机 SDK 桥）`

该入口只连接回环地址，默认沿用设置页的 `127.0.0.1:9999`。这个端口属于 OculiDoC
定义的本机桥，不是对厂商协议的推断。未运行桥时自检会明确失败，不会回退到模拟眼动源，
也不会影响现有 Tobii 或第三方兼容入口。

## 本机 NDJSON 契约

设备侧薄桥每行输出一个 UTF-8 JSON 对象：

```json
{
  "protocol": "oculidoc-seveninvensun-v1",
  "bridge_status": "ready",
  "device_mode": "screen_bar",
  "device_model": "现场准确型号",
  "serial_number": "现场序列号",
  "calibration_status": "calibrated",
  "sample": {
    "coordinate_space": "screen_normalized",
    "sequence": 1,
    "source_timestamp_ns": 123456789,
    "gaze_x_normalized": 0.5,
    "gaze_y_normalized": 0.5,
    "left_eye_valid": true,
    "right_eye_valid": true,
    "left_pupil_diameter_mm": 3.1,
    "right_pupil_diameter_mm": 3.0
  }
}
```

眼动眼镜将 `device_mode` 设为 `wearable_glasses`，并且只有在设备侧完成场景到当前任务
屏幕的映射后才能同时输出：

```json
{
  "mapping_status": "mapped",
  "sample": {"coordinate_space": "screen_normalized"}
}
```

`scene_normalized`、世界射线或未映射数据会被拒绝。桥可用 `sdk_missing`、
`device_not_found`、`unsupported_model`、`calibration_required`、
`scene_mapping_required` 报告状态，OculiDoC 会显示对应原因。

## 仍需现场确认

1. 设备准确子型号、固件、标称/实际采样率以及当前 SDK 是否为该设备授权版本。
2. `_7i_eye_data_ex_t.timestamp`、gyro timestamp 的单位、时钟源、回绕和同步方式。
3. 四元数分量顺序，gyro/accel/mag 的单位、轴向与坐标系。
4. 眼图实际尺寸、瞳孔位置/直径换算能否逐项复现 Studio 导出。
5. SDK 再分发许可、UKey 使用规则以及断线重连要求。
6. 佩戴者校准系数跨设备、MPU、分辨率、环境和 SDK 版本的有效范围；当前只允许在生成时的
   同一现场组合中使用。
7. 场景相机到患者屏幕的标记/单应性方法，以及头动或显示器变化后的失效条件。

三点校准真机通过后，下一阶段由同一独立进程增加场景到患者屏的单应性映射，再把已映射数据
输出到现有 `oculidoc-seveninvensun-v1` 回环契约。现有任务、预检和报告链继续只接收
`screen_normalized`，因此不会误用原始场景 gaze。
