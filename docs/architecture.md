# OculiDoC 架构基线

## 用户角色

### 测试医师／管理员

负责患者管理、实验配置、题库、实验控制、结果查看、设备状态和紧急退出。

### 意识障碍患者／被试

患者端只显示当前实验刺激和必要提示，不显示患者列表、后台配置或复杂菜单。

## 分层

```text
PySide6 管理员端 / 患者端 / 手机后台
                  |
                  v
         Application Use Cases
                  |
                  v
        Domain Models and Scoring
                  |
                  v
Infrastructure Adapters
  - Tobii bridge
  - mock gaze source
  - SQLite
  - Parquet
  - TTS
  - local API
```

## 依赖规则

- UI 不直接读取 TCP socket；
- UI 不直接执行 SQL；
- 眼动设备适配器不直接修改 UI；
- 评分算法不依赖 PySide6；
- 手机后台不直接操作设备进程；
- 原始采样与派生评分分开保存。

## 眼动接口

每一种眼动数据源实现：

```text
start()
stop()
read() -> GazeSample | None
```

第一阶段使用 `MockGazeSource`。后续加入旧桥接器、官方 SDK 和回放数据源。

旧桥接器必须处理 TCP 分包、粘包、半包、断连、有限重连、超时、无效 JSON 和时间戳。

## 端口

- 9999：原医院项目 Tobii 桥接端口；
- 8000：OculiDoC 本地管理后台默认端口。

新程序不得抢占仍由旧项目使用的端口。