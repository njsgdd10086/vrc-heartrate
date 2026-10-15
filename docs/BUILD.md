# 打包成 exe

本项目用 PyInstaller 打包。本文记录**配置方式**和**三个不解决就跑不起来的坑** ——
这些坑的报错信息都指向错误的位置，非常容易误判，所以单独写下来。

```bash
pip install pyinstaller
python -m PyInstaller --distpath dist --workpath build \
  --noconfirm tools/build_web.spec

# 打包后必须执行（修正 MSVC 运行时，原因见下）
python tools/fix_msvc_dlls.py dist/VRCHeartRateWeb
```

产物是 **onedir**（一个文件夹），不是 onefile。

---

## 坑 1: onnxruntime 报"DLL 初始化例程失败"

### 现象

```
ImportError: DLL load failed while importing onnxruntime_pybind11_state:
动态链接库(DLL)初始化例程失败。
```

报错指向 `onnxruntime_pybind11_state.pyd`，让人以为是 onnxruntime 本身的问题。

### 真实原因

PyInstaller 的依赖分析会从**无关目录**抓取 MSVC 运行时 DLL。实测抓到了：

```
C:\Program Files\Java\jdk-1.8\bin\msvcp140.dll     (版本 14.36)
```

这份旧 DLL 被放进包根目录后会**优先于系统版本**被加载，而它缺少
onnxruntime 需要的 `api-ms-win-core-path-l1-1-0.dll` 转发，于是初始化失败。

### 定位方法

用 `pefile` 解析导入表，看究竟缺哪个：

```python
import pefile
pe = pefile.PE("onnxruntime.dll", fast_load=True)
pe.parse_data_directories(directories=[
    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
for e in pe.DIRECTORY_ENTRY_IMPORT:
    print(e.dll.decode())
```

### 修复方式

`tools/fix_msvc_dlls.py` 把包内**所有** MSVC 运行时副本（含 pandas/matplotlib
自带的哈希后缀副本）**原地替换**成 System32 里的正确版本。

**注意：不能删除这些副本。** matplotlib / pandas / sklearn 的 `.libs` 目录里
是它们自己构建的私有副本，删掉会导致 `matplotlib` 报
`DLL load failed while importing _path`。只能替换，不能删。

---

## 坑 2: OpenCV 读不了含中文的路径

### 现象

程序放在中文目录（如 `桌面\软件收纳\摄像头心率VRChat`）时：

```
cv2.error: Can't read ONNX file: ...\face_detection_yunet_2023mar.onnx
```

文件明明存在、大小也对。

### 真实原因

OpenCV (5.0) 的 `FaceDetectorYN` 内部用**窄字符 API** 打开文件，
无法处理非 ASCII 路径。同一个文件复制到英文路径就能读。

### 修复方式

代码里的 `opencv_safe_path()`：检测到非 ASCII 路径就把模型复制到
系统临时目录（纯 ASCII）再加载。模型只有 227 KB，复制耗时实测 3 ms。

> 注意 `onnxruntime` **没有**这个问题，它能正常读中文路径。只有 OpenCV 有。

---

## 坑 3: 必须收集 gradio 的前端资源与全部依赖树

### 现象

- 缺 `gradio_client/types.json` → `FileNotFoundError`
- 缺 `matplotlib` → `ModuleNotFoundError`
- 缺 `pandas` → `ModuleNotFoundError`
- 页面能开但白屏 → `gradio/templates` 没打进去（756 个文件）

### 修复方式

`tools/build_web.spec` 里：

- `collect_data_files("gradio", includes=["templates/**", ...])`
- `collect_submodules("gradio")` + `gradio_client`
- 对 gradio 的整棵第三方依赖树做 `collect_submodules`

**教训**：gradio 各组件会在**导入期**就 import 一堆库（`gr.LinePlot` 用
matplotlib、`gr.BarPlot` 用 pandas），逐个试太慢，直接全量收集，
再用 `EXCLUDES` 砍掉确定无关的重型框架（torch / tensorflow / jax / numba）。

---

## 其他打包注意事项

### 摄像头后端选择（影响预览流畅度）

`cap.read()` 实测占整条流水线约 **85%** 的时间，所以后端的读帧速度直接决定
预览流畅度。实测同一颗摄像头：

| 后端 | 读帧 | 实测帧率 |
|---|---|---|
| DSHOW | 57 ms/帧 | 15 fps |
| MSMF | 33 ms/帧 | 30 fps |

因此代码里 **MSMF 优先**。

两个容易踩的坑：

1. **不能"探测完所有后端再挑最快的"** —— 打开第二个后端会让第一个句柄失效。
   实测这样选出的 MSMF 句柄读帧全部失败（`frames_failed` 飙升、画面完全不动），
   尽管探测阶段它报了 30 fps。正确做法是逐个试、**首选通过验证的就直接用**。
2. **采纳句柄前必须再验证一次**（`_verify_cap`）—— 探测阶段的"能读"不代表
   之后还能读，原因同上。

用 `tools/verify_pipeline.py` 可以确认稳态帧率：

```
采集发布      30.2 fps
预览产出      13.0 fps
已读 364 帧 / 失败 0
```

### 用 onedir，不要用 onefile

- onefile 会把所有 DLL 平铺到一个临时目录，加剧坑 1 的冲突
- onefile 每次启动都要解压约 500 MB，启动明显变慢
- 本项目的 `collect_dynamic_libs("pyvirtualcam")` 在 onefile 下不生效

### 虚拟摄像头的 GPL 问题

`pyvirtualcam` 是 **GPL-2.0**。若要以 MIT 分发：

- 从 spec 里移除 `datas/binaries/hiddenimports` 中 pyvirtualcam 相关项
- 从 `requirements-optional.txt` 里确认不装它
- 核心功能不受影响，只是「启用虚拟摄像头输出」会提示未安装

注意 GPL-2.0 与 GPL-3.0 不是一回事：本项目其余部分为 MIT，两者条款不同，
声明时请写**准确**的版本号（容易写错，已踩过）。

### 体积说明

约 500 MB。主要来自 gradio 前端资源、matplotlib、pandas ——
它们是 gradio 的硬依赖，无法省略。

---

## 验证清单

打包后逐一确认：

- [ ] 双击能启动，浏览器自动打开
- [ ] 控制台出现 `FacePhys 就绪（46 个状态张量）`
- [ ] 摄像头下拉框有内容（不是空白转圈）
- [ ] 点「开始」后出现实时画面
- [ ] 画面带人脸框 + 裁剪缩略图 + 波形
- [ ] 右侧波形图有曲线（不是空框）
- [ ] 状态栏显示 `信号强度` 与 `fps`
- [ ] 「发送测试 OSC」返回成功
- [ ] 若启用虚拟摄像头：状态栏显示设备名，且其他程序（如 OpenCV）能读到它
- [ ] 连续运行 5 分钟不冻结（界面数字持续变化）
