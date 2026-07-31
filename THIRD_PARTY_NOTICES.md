# Third-Party Notices

OculiDoC 自有代码、文档和素材的权属与使用限制见 [NOTICE.md](NOTICE.md)。以下许可仅
适用于所列第三方组成部分，不改变 OculiDoC 其他内容的权属或使用条件。

## Windows 冻结包

官方 Windows 安装器和便携包包含 CPython 3.11 解释器以及由 `pyproject.toml` 声明的
第三方 Python 包。每次正式构建都会在发行目录内生成 `THIRD_PARTY_LICENSES.json`，
记录构建环境中已安装包的名称、精确版本、上游网址、许可证元数据以及可获得的许可证和
NOTICE 正文。该清单可能保守地列出构建或测试工具；列入清单不表示对应组件一定被加载。

冻结包根目录同时包含本文件、`NOTICE.md`、`LICENSE-v0.1.1.txt`、
`QT_SOURCE_OFFER.md`，以及 `licenses/` 内的完整许可文本。CPython 的许可证从实际用于
冻结发行包的官方 CPython 3.11.9 源码标签中逐字复制，并由构建门禁锁定解释器版本。
OpenCV 等 wheel 内附带的第三方归属正文由 `THIRD_PARTY_LICENSES.json` 一并保留。

## PySide6 / Qt

OculiDoC v0.1.1 使用未修改的 PySide6 / Qt 6.11 动态库，并选择其 LGPL-3.0 许可路径；
Qt/PySide 库在安装目录的 `_internal/PySide6` 下保持为可替换的独立文件。GNU LGPL
3.0 与 GPL 3.0 完整文本分别见 `licenses/LGPL-3.0-only.txt` 和
`licenses/GPL-3.0-only.txt`。Qt/PySide 6.11.1 官方源码标签中的开源许可文本及精确
上游提交记录保存在 `licenses/qt-6.11.1/`。

本版不再嵌入 Qt WebEngine/Chromium；构建门禁也拒绝 Qt 6.11 中未被应用使用的
GPL-only 模块（Qt Canvas Painter、Qt CoAP、Qt Graphs、Qt GRPC、Qt HTTP Server、
Qt Lottie Animation、Qt MQTT、Qt Network Authorization、Qt Qml Compiler、
Qt Quick 3D/Physics、Qt Quick Timeline、Qt Virtual Keyboard 与 Qt Wayland Compositor）
进入发行包。对应源代码提供方式和替换库说明见
[QT_SOURCE_OFFER.md](QT_SOURCE_OFFER.md)。

Qt and the Qt logo are trademarks of The Qt Company Ltd. PySide is provided by
the Qt for Python project. OculiDoC is not endorsed by The Qt Company.

## Inno Setup

Windows 安装器由 Inno Setup 构建。仓库内
`packaging/windows/languages/ChineseSimplified.isl` 同步自
`jrsoftware/issrc` 提交 `095bb0bcbe30c5d52f51f6e278128a8b2e96cf09`，
由 Zhenghan Yang（Kira）维护翻译。

Inno Setup License
==================

Except where otherwise noted, all of the documentation and software included in the Inno
Setup package is copyrighted by Jordan Russell.

Copyright (C) 1997-2026 Jordan Russell. All rights reserved.
Portions Copyright (C) 2000-2026 Martijn Laan. All rights reserved.

This software is provided "as-is," without any express or implied warranty. In no event shall
the author be held liable for any damages arising from the use of this software.

Permission is granted to anyone to use this software for any purpose, including commercial
applications, and to alter and redistribute it, provided that the following conditions are met:

1. All redistributions of source code files must retain all copyright notices that are currently
   in place, and this list of conditions without modification.

2. All redistributions in binary form must retain all occurrences of the above copyright notice
   and web site addresses that are currently in place (for example, in the About boxes).

3. The origin of this software must not be misrepresented; you must not claim that you wrote
   the original software. If you use this software to distribute a product, an acknowledgment
   in the product documentation would be appreciated but is not required.

4. Modified versions in source or binary form must be plainly marked as such, and must not
   be misrepresented as being the original software.

Jordan Russell

jr-2020 AT jrsoftware.org

https://jrsoftware.org/
