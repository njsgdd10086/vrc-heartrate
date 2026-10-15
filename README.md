# VRChat 无接触心率

用**普通摄像头**无接触测量心率，通过 **OSC** 发送给 VRChat 的 Avatar 参数，可选显示在聊天框（Chatbox）。

基于 [FacePhys](https://github.com/KegangWangCCNU/FacePhys-Release)（轻量 rPPG 模型）+ YuNet 人脸检测，**全部推理在本机完成**，不上传任何数据。

> English: [README_EN.md](README_EN.md)

---

## 功能

- **无接触测心率** —— 普通 USB 摄像头即可，不需要手环、胸带
- **网页界面** —— 浏览器里选摄像头、看实时画面与心跳波形、改参数，无需命令行
- **摄像头选择指引** —— 自动列出设备名称，标出哪个能看到人脸
- **三种输出**
  - Avatar 参数（默认 `/avatar/parameters/VRCOSC/Heartrate/Value`）
  - 聊天框播报（`/chatbox/input`，可自定义文字模板与频率）
  - **虚拟摄像头** —— 把画面转发出去，让面补等其他程序共用同一颗摄像头
- **信号质量控制** —— 双算法交叉校验（自相关 + 频谱），信号不可信时拒绝输出而不是编造数值

---

## 快速开始

### 方式一：下载打包好的 exe（推荐普通用户）

1. 到 [Releases](../../releases) 下载 `VRCHeartRate-*.zip` 并解压到**任意文件夹**
2. 双击 `VRCHeartRateWeb.exe` 即可，浏览器会自动打开
3. VRChat 里打开 **设置 → OSC → Enabled**
4. 选摄像头（列表里带 `★有人脸` 的就是对着你的那个）→ 点「开始」→ 正对镜头保持不动
5. 约 10~15 秒后出心率

> **重要**：exe 必须和 `_internal` 文件夹一起使用，不能只把 exe 单独拷出来。

### 方式二：从源码运行

需要 Python 3.10+。

```bash
git clone https://github.com/njsgdd10086/vrc-heartrate
cd vrc-heartrate

# 下载模型权重（约 3.3 MB）
python tools/download_models.py

# 安装依赖
pip install -r requirements.txt

# 启动
python osc_heartrate_cam.py
```

浏览器会自动打开 `http://127.0.0.1:7860`。

模型文件较大（3 MB），没有放进 git 仓库，由 `download_models.py` 获取。
若该脚本失败（网络原因），也可以手动下载后放到 `models/`：

| 文件 | 来源 |
|---|---|
| `FacePhys.onnx` | [FacePhys-Release](https://github.com/KegangWangCCNU/FacePhys-Release) 的 `weights/model.onnx` |
| `face_detection_yunet_2023mar.onnx` | [OpenCV Zoo](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) |

---

## 摄像头被其他程序占用怎么办

**USB 摄像头通常只允许一个程序打开。** 如果面补 / 相机 App / OBS / 浏览器已经占用了它，本程序就读不到画面。

有两条路：

### 方案 A：各用一颗摄像头（最简单，推荐先试）

如果你有两颗摄像头（例如外接 USB + 笔记本内置），让两边各用一颗即可。

在界面里选一颗能看到你的摄像头；把面补换成另一颗。两颗是**互相独立**的物理设备，不会冲突。

程序的下拉框会显示设备名，例如：

```
#0  4M HD Camera           ← 外接彩色
#1  HD Camera              ← 笔记本内置彩色
#2  IR Camera  [红外]       ← 红外，不能用于测心率
```

> 红外摄像头画面几乎无色彩，而 rPPG 依赖皮肤颜色变化，**不要选标了 `[红外]` 的设备**。

### 方案 B：虚拟摄像头（只有一颗摄像头时）

勾选界面里的「**启用虚拟摄像头输出**」，然后：

1. 点「开始」——本程序独占物理摄像头，并把画面转发到一个虚拟摄像头
2. 状态栏会显示虚拟摄像头的设备名（通常是 `OBS Virtual Camera`）
3. 到**面补软件**里把摄像头改成这个设备

前提：系统里需要有一个**虚拟摄像头驱动**。装了
[OBS Studio](https://obsproject.com/)（提供 `OBS Virtual Camera`）或
[UnityCapture](https://github.com/schellingb/UnityCapture)（提供
`Unity Video Capture`）任一即可，程序会自动选用可用的那个。**两者都不需要
处于运行状态** —— 本程序自己充当生产者。

程序不写死设备名：它通过 pyvirtualcam 自动发现可用的后端，界面上显示的就是
实际创建出来的设备名，按那个名字去面补里找即可。

> ⚠️ 注意顺序：本程序必须先启动并点「开始」，虚拟摄像头才存在。

---

## 参数设置

| 参数 | 默认 | 说明 |
|---|---|---|
| OSC 地址 | `/avatar/parameters/VRCOSC/Heartrate/Value` | 需与 Avatar 上 `float` 参数名一致 |
| OSC 端口 | `9000` | VRChat 默认 |
| 信号门限 | `0.30` | 低于此值不输出心率。越大越严格，但可能长时间不出数 |
| 平滑系数 | `0.10` | 越小越平滑、越大越跟手 |
| 聊天框模板 | `当前心率：{hr}` | `{hr}` 会替换成心率数值 |
| 聊天框间隔 | `5` 秒 | VRChat 对聊天框有频率限制，设太短消息会被丢弃 |

Avatar 上需要一个 `float` 参数接收心率。参数名的最后一段由你决定，例如参数名 `VRCOSC/Heartrate/Value` 对应地址 `/avatar/parameters/VRCOSC/Heartrate/Value`。

---

## 命令行参数

用法以网页为主，命令行参数都是可选的：

```bash
python osc_heartrate_cam.py                 # 默认，自动开浏览器
python osc_heartrate_cam.py --no-browser    # 不自动开浏览器
python osc_heartrate_cam.py --port 7900     # 换端口（被占用时会自动顺延）
python osc_heartrate_cam.py --max-index 8   # 扫描更多摄像头索引（默认 6，越大越慢）
python osc_heartrate_cam.py --size 1280x720 # 指定首选分辨率
```

---

## 工作原理

```
摄像头 ──▶ YuNet 人脸检测 ──▶ 裁剪 36×36 ──▶ FacePhys (ONNX) ──▶ BVP 波形
                                                                    │
                                              带通 + 等间隔重采样 ────┤
                                                                    ▼
                                        自相关（主） + Welch PSD（交叉校验）
                                                                    │
                                              中值滤波 + 平滑 ───────┤
                                                                    ▼
                                            OSC ──▶ VRChat 参数 / 聊天框
```

几个关键点：

- **预处理复刻官方 Web Demo**：人脸框垂直放大 1.2 倍并上移 20% 以包含额头（rPPG 最关键区域）
- **等间隔重采样**：摄像头帧率会波动，而 FFT / 自相关都要求均匀采样，所以先插值到固定 30 Hz
- **双算法交叉校验**：自相关与频谱结果相差过大时判定信号有歧义，降低可信度
- **心率来源可验证**：在官方样例上验证过，两种算法都给出 75.0 BPM、自相关峰 0.72

---

## 已知限制

请务必了解这些，避免误判：

- **这是研究模型，不是医疗器械**，不能用于任何医学用途
- **精度受摄像头与光照影响很大**。rPPG 依赖皮肤上约 0.1% 的亮度变化，以下情况会显著变差：
  - 摄像头自动曝光/白平衡频繁调整（静止背景都会有百分之几的亮度波动）
  - 光照不足或光源闪烁（尤其荧光灯/劣质 LED）
  - 大幅度移动、说话、面部遮挡
- **红外摄像头不可用** —— 画面几乎无色彩信息
- **VRChat 自带的面捕用头显摄像头**，与普通 USB 摄像头不冲突；本程序的虚拟摄像头功能主要针对**读 USB 摄像头的第三方面补软件**
- **打包体积较大（约 500 MB）** —— gradio 的前端资源、matplotlib、pandas 等都是其硬依赖

---

## 隐私

本程序的所有生物特征推理与信号处理**全部在本机完成**，不上传任何图像或视频。

- 心率数值只通过 OSC (UDP) 发到 `127.0.0.1:9000`，即本机的 VRChat
- **但请注意**：VRChat 是联网平台，把衍生生理指标（心率）发给它，属于你个人需要知情同意的行为
- 若启用「虚拟摄像头输出」，画面会经由系统虚拟摄像头驱动暴露给**本机其他程序**——这正是该功能的目的，但请知悉

模型 [FacePhys](https://github.com/KegangWangCCNU/FacePhys-Release) 的许可证含**隐私保护附加条款**，要求生物特征推理必须本地完成、且不得未经同意把衍生指标传出设备。本程序满足这些要求，你二次分发时也需保留该约束（完整条款随模型放在 `models/FacePhys-LICENSE.txt`，说明见 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)）。

---

## 项目结构

```
.
├── osc_heartrate_cam.py        # 主程序：摄像头 rPPG → VRChat OSC
├── osc_heartrate_ble.py        # 备选：蓝牙心率带 → VRChat OSC（不走摄像头）
├── facephys.py                 # FacePhys ONNX 推理的精简封装（自包含）
├── requirements.txt
├── models/                     # 模型权重（由 download_models.py 获取，不在仓库里）
│   ├── FacePhys.onnx
│   ├── FacePhys-LICENSE.txt    # 上游许可证，含隐私保护附加条款
│   └── face_detection_yunet_2023mar.onnx
├── tools/
│   ├── download_models.py      # 下载模型权重
│   ├── build_web.spec          # PyInstaller 打包配置
│   ├── fix_msvc_dlls.py        # 打包后修正 MSVC 运行时（见 docs/BUILD.md）
│   ├── verify_pipeline.py      # 端到端帧率/延迟检查
│   ├── profile_*.py            # 性能剖析（后端读帧、绘制、采集各步骤）
│   └── diag_*.py               # 诊断（摄像头共享/色彩/索引映射/线程状态）
└── docs/
    ├── BUILD.md                # 打包说明与踩坑记录
    └── TROUBLESHOOTING.md      # 常见问题
```

---

## 打包成 exe

见 [docs/BUILD.md](docs/BUILD.md)。里面记录了三个不解决就跑不起来的坑（onnxruntime DLL、OpenCV 中文路径、虚拟摄像头依赖收集）。

---

## 许可证

本项目采用 **[GPL-3.0-or-later](LICENSE)**（GNU 通用公共许可证第 3 版或更高版本）。
版权声明与选择该许可证的原因见 [COPYRIGHT.md](COPYRIGHT.md)。

### 为什么是 GPL-3.0（而不是 MIT，也不是 GPL-2.0）

本项目依赖的组件对许可证版本的要求相互矛盾：

| 组件 | 许可证 | 与 MIT 项目 | 与 GPL-2.0-only | 与 GPL-3.0 |
|---|---|---|---|---|
| [pyvirtualcam](https://github.com/letmaik/pyvirtualcam) | GPL-2.0 | ✗ 冲突 | ✓ | ✓ |
| [OpenCV](https://opencv.org/) | Apache-2.0 | ✓ | ✗ **不兼容** | ✓ |
| [gradio](https://gradio.app/) | Apache-2.0 | ✓ | ✗ **不兼容** | ✓ |
| onnxruntime / scipy / numpy / Pillow | MIT / BSD | ✓ | ✓ | ✓ |

- 用 MIT：与 GPL-2.0 的 pyvirtualcam **冲突**（这正是之前的问题）
- 用 GPL-2.0-only：与 Apache-2.0 的 OpenCV / gradio **不兼容**
  （FSF 明确认定；Apache-2.0 的专利与赔偿条款属于 GPLv2 第 6 条禁止的"进一步限制"）
- **用 GPL-3.0：以上全部兼容** —— 这是唯一能同时容纳它们的版本

所以本项目整体为 GPL-3.0-or-later，源文件均带 SPDX 标识。

### 这意味着什么

- 你可以自由使用、修改、再分发，**包括商用**
- 但**再分发时必须提供完整源代码**，并以同样的 GPL 条款授权
- 不能把本项目改造成闭源专有软件再分发

无需为 pyvirtualcam 单独做任何声明 —— 整个项目本来就是 GPL。

### 虚拟摄像头还需要一个系统级驱动

`pyvirtualcam` 只是"生产者"，真正对外的设备由系统里已注册的
DirectShow 过滤器提供。以下任一即可，程序会自动选用可用的那个
（两个本项目都**不打包**）：

| 提供方 | 设备名 | 许可证 |
|---|---|---|
| [OBS Studio](https://obsproject.com/) | `OBS Virtual Camera` | GPL-2.0 |
| [UnityCapture](https://github.com/schellingb/UnityCapture) | `Unity Video Capture` | 公共领域 |

所以**并非必须安装 OBS**；而且无论用哪个，**都不需要该软件处于运行状态**。

### 第三方组件

[FacePhys](https://github.com/KegangWangCCNU/FacePhys-Release) 模型（MIT +
**隐私保护附加条款**）、YuNet 模型等第三方组件的完整说明见
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。

> 说明: FacePhys 模型的许可证含使用限制（要求生物特征推理本地完成等），
> 与 GPL 的兼容性可能存在争议。模型不是本项目的代码、无法由本项目重新授权，
> 因此其条款随模型保留在 `models/FacePhys-LICENSE.txt`。若你要商用，
> 建议自行确认这一点。

## 致谢

- [FacePhys](https://github.com/KegangWangCCNU/FacePhys-Release) —— rPPG 模型（Kegang Wang 等）
- [OpenCV](https://opencv.org/) —— YuNet 人脸检测
- [pyvirtualcam](https://github.com/letmaik/pyvirtualcam) —— 虚拟摄像头输出
- [OBS Studio](https://obsproject.com/) / [UnityCapture](https://github.com/schellingb/UnityCapture) —— 虚拟摄像头驱动
- [Gradio](https://gradio.app/) —— 网页界面
