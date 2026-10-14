# 第三方组件许可证与约束

本仓库自身代码为 MIT（见 [LICENSE](LICENSE)），但**依赖的模型和库有各自条款**。
分发本项目（尤其是打包成 exe 分发）前请务必阅读本文。

---

## ⚠️ FacePhys 模型 —— 含隐私保护附加条款

- **来源**: https://github.com/KegangWangCCNU/FacePhys-Release
- **文件**: `models/FacePhys.onnx`（由 `tools/download_models.py` 从上游获取）
- **许可证**: MIT **+ Privacy Protection Addendum（隐私保护附加条款）**
- **作者**: Kegang Wang 等
- **完整条款**: 随模型放在 `models/FacePhys-LICENSE.txt`，**再分发必须一并保留**

本项目的 `facephys.py` 是对上游 `inference.py` 中 ONNX 加载部分的精简重写
（上游为 MIT，允许），目的是让项目自包含，使用者不必再去 clone 上游仓库。

### 附加条款的实质约束

1. **本地处理（无例外）** —— 所有生物特征推理与信号处理**必须**在本地设备完成。
   此要求是绝对的，**不可以通过用户同意来豁免**。禁止把推理放到服务器、
   禁止把原始视频上传到任何外部服务。

2. **结果的传输** —— **不得**把衍生生理指标传输/上传/串流到外部服务器，
   *除非*数据主体给出了明确、知情、可撤回的同意，且该同意清楚说明了
   收集什么、发到哪里、如何使用、保留多久。

3. **收集同意** —— 从任何人采集生理数据前需获得明确同意。

4. **再分发** —— 若你再分发本项目，必须保留上述保证，且不得引入隐蔽收集。

5. **模型格式无关** —— 上述要求适用于该仓库的**所有格式**（LiteRT / ONNX /
   JAX-Keras 权重），以及由它们导出的 TensorRT / TensorFlow 构建产物。
   把它编译成服务端运行时**不会**解除本地处理的要求。

6. 该模型基于 RLAP 数据集训练，数据集另有 CC BY-NC-ND 4.0 许可，
   本许可证**不授予**数据集本身的任何权利。

7. FacePhys 是**研究模型，不是医疗器械**。

### 本项目的合规情况

| 要求 | 本项目情况 |
|---|---|
| 推理在本地 | ✅ 全程 ONNX Runtime 本机推理，无网络调用 |
| 不上传原始视频 | ✅ 代码中没有任何上传逻辑 |
| 衍生指标的传输 | ⚠️ 心率通过 OSC (UDP) 发到 `127.0.0.1:9000`，即**本机** VRChat。<br>这本身不是"外部服务器"，但 VRChat 是联网平台 —— 是否构成"传输"，取决于你所在司法辖区与用户预期。**建议在文档中明确告知用户** |
| 采集同意 | ⚠️ 由**使用者**负责。若你在公共场合/为他人部署，需自行获得同意 |

**再分发时你必须保留本文件与 `models/FacePhys-LICENSE.txt`。**

---

## YuNet 人脸检测模型

- **来源**: https://github.com/opencv/opencv_zoo
- **文件**: `models/face_detection_yunet_2023mar.onnx`
- **许可证**: Apache License 2.0（与 OpenCV Zoo 一致）

## OpenCV (opencv-python)

- **许可证**: Apache License 2.0
- **注意**: OpenCV 4.5.4+ 起采用 Apache 2.0；`opencv-python` 的 wheel 内含
  部分 LGPL 组件（如 FFmpeg），商业分发前请自行确认。

## Gradio

- **许可证**: Apache License 2.0

## pyvirtualcam

- **许可证**: GNU General Public License v3.0 (**GPL-3.0**)
- **⚠️ 重要**: GPL-3.0 是**传染性**许可证。若你分发打包了 pyvirtualcam 的
  二进制，需注意 GPL 的兼容性要求。
- **本项目如何处理**: `pyvirtualcam` 只在用户启用「虚拟摄像头输出」时才被
  `import`，属于**可选功能**。若你要以宽松许可证（如 MIT）分发，建议：
  - 要么在文档中明确声明包含 GPL-3.0 组件及其影响
  - 要么提供**不含 pyvirtualcam** 的构建（此时虚拟摄像头功能不可用，
    但核心测心率功能完全正常）
- **虚拟摄像头驱动的另一层依赖**: Windows 上 pyvirtualcam 需要
  [OBS Studio](https://obsproject.com/) 提供 `obs-virtualcam-module64.dll`。
  OBS Studio 为 GPL-2.0。本项目**不打包** OBS，只要求用户自行安装。

## ONNX Runtime

- **许可证**: MIT License

## NumPy / SciPy / Pillow

- NumPy: BSD-3-Clause
- SciPy: BSD-3-Clause
- Pillow: MIT-CMU (HPND)

## Matplotlib

- **许可证**: Matplotlib License（基于 PSF，BSD 兼容）
- 说明: 本项目不直接使用 matplotlib，但 gradio 在导入期会引用它。

---

## 打包分发检查清单

如果你要发布 exe：

- [ ] 在 Release 说明中包含本文件
- [ ] 在 Release 说明中包含 `models/FacePhys-LICENSE.txt`
- [ ] 明确告知用户：心率数据会发送到本机 VRChat（联网平台）
- [ ] 若启用虚拟摄像头功能，画面会暴露给本机其他程序 —— 已在 UI 中提示
- [ ] 若包含 pyvirtualcam，声明 GPL-3.0 组件
- [ ] 不要移除 `models/` 下的许可证文件

> 以上为工程层面的整理，**不构成法律意见**。商业用途请咨询专业人士。
