# 版权与许可说明

## 版权

```
vrc-heartrate —— 摄像头无接触心率 → VRChat
Copyright (C) 2026 njsgdd10086
```

## 许可证

本项目采用 **GNU 通用公共许可证第 3 版或更高版本（GPL-3.0-or-later）**。
完整条款见 [LICENSE](LICENSE)。

```
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
```

## 为什么是 GPL-3.0

本项目依赖的组件对许可证版本的要求**相互矛盾**，只有一个版本能全部容纳：

| 组件 | 许可证 | 与 MIT 项目 | 与 GPL-2.0-only | 与 GPL-3.0 |
|---|---|---|---|---|
| [pyvirtualcam](https://github.com/letmaik/pyvirtualcam) | GPL-2.0 | ✗ **冲突** | ✓ | ✓ |
| [OpenCV](https://opencv.org/) | Apache-2.0 | ✓ | ✗ **不兼容** | ✓ |
| [gradio](https://gradio.app/) | Apache-2.0 | ✓ | ✗ **不兼容** | ✓ |
| onnxruntime / scipy / numpy / Pillow | MIT / BSD | ✓ | ✓ | ✓ |

- **MIT**：与 GPL-2.0 的 pyvirtualcam 冲突
- **GPL-2.0-only**：与 Apache-2.0 的 OpenCV / gradio 不兼容
  （Apache-2.0 的专利终止与赔偿条款属于 GPLv2 第 6 条禁止的"进一步限制"，
  FSF 对此有明确认定）—— 改成 GPL-2.0 只是把问题换了个地方
- **GPL-3.0**：同时兼容以上全部，因此是唯一可行选择

## 关于 LICENSE 文件为何没有版权声明

GPL 的完整条款文本本身**不允许修改**。而 GitHub 的许可证识别工具按标准
结构匹配 LICENSE 文件 —— 若在其中前置版权声明，仓库侧边栏会把许可证显示成
`NOASSERTION / Other` 而非 GPL-3.0。所以：

- `LICENSE` = 纯 GPL-3.0 全文（不改一字）
- 版权声明与本说明 = 本文件

## 第三方组件

模型与库各有自己的条款，其中 FacePhys 模型的许可证含**隐私保护附加条款**。
分发前请阅读 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。
