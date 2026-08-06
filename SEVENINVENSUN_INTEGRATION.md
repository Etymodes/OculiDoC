# 七鑫易维 aSee 眼动源预留契约

状态：`v0.1.3` 已预留，等待 Tieying 现场取得准确型号和厂商 SDK 后补设备侧薄桥。

## 已确认与未确认

七鑫易维官网把桌面眼动条和穿戴式眼动眼镜列为不同产品形态。公开开发资料说明其 SDK
可提供注视点坐标、时间戳、双眼相关数据和校准结果，但没有公开足以安全绑定此次到院设备的
Windows ABI、DLL 名、架构、许可证、设备发现和坐标定义。因此 OculiDoC 不猜端口、不猜 DLL，
也不把眼镜的场景坐标当作屏幕坐标。

官方依据：

- [aSee Pro 桌面眼动仪](https://www.7invensun.com/aseepro)
- [aSee Glasses Elite 眼动眼镜](https://www.7invensun.com/elite)
- [眼动分析产品系列](https://www.7invensun.com/analysis)
- [aSee Mobile 开发者接口字段示例](https://www.7invensun.com/productinfo/3048337.html)

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

## 到院时必须向厂商确认

1. 眼动条和眼镜的完整型号、序列号、固件版本、标称与实际采样率。
2. Windows SDK 名称、版本、x64 支持、许可证、运行时/DLL 依赖及可再分发边界。
3. 官方 C++ 或 C# 连续取样示例，以及启动、停止、断线重连和设备发现接口。
4. 坐标原点、方向、单位、范围、越界规则；条形设备是否直接对应当前显示器。
5. 时间戳单位、时钟来源、回绕规则和是否能与 Windows 单调时钟对齐。
6. 左右眼有效标记、合并注视点、瞳孔字段、校准分数及重校准触发条件。
7. 眼镜的场景相机分辨率、场景坐标、屏幕/平面映射 API，以及头动后的映射有效性。

完成上述确认后，只需实现厂商 SDK 到该 NDJSON 契约的本机薄桥；OculiDoC 的任务、预检、
样本质量和报告链无需绑定厂商私有 ABI。
