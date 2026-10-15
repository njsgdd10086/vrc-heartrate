"""
摄像头 rPPG 心率 → VRChat OSC 桥接（基于 FacePhys）

算法: FacePhys (KegangWangCCNU/FacePhys-Release, MIT + 隐私保护附加条款)
  模型为纯本地 ONNX 推理，不外传任何图像数据，符合其许可的本地处理要求。

预处理复刻官方 Web Demo (FacePhys-Demo):
  人脸框垂直放大 1.2x 并上移 20%（含额头）→ 裁 36x36 → RGB/255

心率估计: BVP → 等间隔重采样 → 带通 → 自相关峰(主) + Welch PSD(交叉校验)

★ 关于摄像头设置 ★
rPPG 依赖皮肤上约 0.1% 的极微弱颜色变化。实测证明: 若摄像头开着
自动曝光/自动白平衡，静止墙面都会有约 8% 的逐帧亮度波动，噪声比
脉搏信号高一个数量级，信号会被彻底淹没。因此本脚本启动时会:
  1) 枚举并逐个探测摄像头，用"背景区时序噪声"客观打分，自动挑最好的
  2) 尝试锁定曝光/增益/白平衡，并重新测量确认噪声确实降下来了
强烈建议配合 --list 先看看哪个摄像头在锁定后噪声最低。

依赖: onnxruntime, opencv-python, scipy, numpy
"""

import argparse
import asyncio
import logging
import math
import os
import re
import signal
import struct
import sys
import threading
import time

# 中文日志在 Windows 下需显式 UTF-8（打包 exe 后没有 PYTHONIOENCODING 兜底）
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import cv2
import numpy as np
from scipy import signal as sp_signal

# -------------------- 配置 --------------------
VRC_OSC_IP = "127.0.0.1"
VRC_OSC_PORT = 9000
OSC_ADDRESS = "/avatar/parameters/VRCOSC/Heartrate/Value"

CAM_WIDTH, CAM_HEIGHT = 640, 480
CROP_SIZE = 36                     # FacePhys 固定输入尺寸
DETECT_INTERVAL = 2                # 每 N 帧做一次人脸检测，其余帧沿用平滑框

# 心率估计
BVP_WINDOW_SEC = 30.0              # BVP 分析窗长（越长频率分辨率越高，但越滞后）
UNIFORM_FS = 30.0                  # 重采样目标采样率
BPM_MIN, BPM_MAX = 42.0, 180.0
MIN_SAMPLES = 150
# 预览绘制帧率。绘制在独立线程里做，限速是为了省 CPU，
# 与推理帧率无关（推理仍是满速跑，不受预览影响）
PREVIEW_FPS = 15
# 自相关峰的门限。实测校准: 官方参考样例为 0.72（很强）；
# 本机摄像头可靠读数约 0.34~0.42，噪声读数约 0.15~0.28，
# 故取 0.30 作为"可用"下限，0.45 以上视为"良好"。
MIN_AC_PEAK = 0.30
GOOD_AC_PEAK = 0.45
MEDIAN_WINDOW = 7                  # 心率读数中值滤波长度（秒）

# OSC 输出平滑（与蓝牙版一致的手感）
SMOOTHING_FACTOR = 0.1
SEND_INTERVAL = 1.0 / 10
TIMEOUT_SECONDS = 12
ANIMATION_PERIOD = 10

# 版本号（发布时同步更新；用户报障时能从启动信息里看到）
__version__ = "1.0.0"

# Chatbox: 把当前心率发到 VRChat 聊天框（OSC 地址 /chatbox/input）
CHATBOX_ENABLE = False
CHATBOX_TEMPLATE = "当前心率：{hr}"
# 发送间隔(秒)。VRChat 对 chatbox 有频率限制，用"立即发送"模式时限制更严，
# 别设太小，否则消息会被丢弃
CHATBOX_INTERVAL = 5.0
CHATBOX_SOUND = False

# ==================== 日志 ====================
# 打包成 exe 后，控制台日志一闪而过，用户报障时没有现场信息。
# 所以同时写一份到文件；写不进去（只读目录等）就退回到只输出控制台，
# 绝不因为日志问题让程序起不来。
LOG_FILE = None


def setup_logging():
    """配置日志：控制台 + 文件（文件失败自动降级）"""
    global LOG_FILE
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)

    # 依次尝试：用户目录 -> 程序目录
    # （用户目录优先，避免从源码运行时把日志写进仓库、误提交）
    candidates = []
    try:
        d = os.path.join(os.path.expanduser("~"), ".vrc_heartrate_cam")
        os.makedirs(d, exist_ok=True)
        candidates.append(os.path.join(d, "vrc-heartrate.log"))
    except Exception:
        pass
    try:
        candidates.append(os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "vrc-heartrate.log"))
    except Exception:
        pass

    for path in candidates:
        try:
            fh = logging.FileHandler(path, encoding="utf-8", mode="a")
            fh.setFormatter(fmt)
            root.addHandler(fh)
            LOG_FILE = path
            return
        except Exception:
            continue
    # 全失败也不影响运行


setup_logging()
shutdown_event = asyncio.Event()


# ==================== 摄像头枚举与质量探测 ====================
# 教训: cap.isOpened() 返回 True 完全不代表能抓到帧。
# 实测某摄像头在 640x480 + MSMF 下 isOpened()=True 但 40 次读取全部失败
# （OnReadSample error -1072875772 = MF_E_HW_MFT_FAILED_START_STREAMING），
# 而在 1280x720 下 40 帧全部正常。所以必须实测抓帧并校验内容。

# 超时保护（必须在函数定义之前）
#
# 背景: OpenCV 的 cap.read() / VideoCapture 构造在设备异常时会**无限阻塞**。
# 实测同一台机器上 HD Camera(索引1) 在 MSMF 后端下永久卡住，而 DSHOW 完全
# 正常 —— 所以超时必须下沉到"单个后端的一次尝试"，否则一个后端卡住会让
# 整颗摄像头被误判为不可用（这正是之前的 bug：下拉框里少了那颗摄像头）。
#
# 数值权衡: 太大则枚举慢（用户干等界面），太小则误杀正常但缓慢的设备。
# 实测正常后端 1~4 秒内出结果，留 5 秒；复核只需 5 帧，3 秒足够。
PROBE_TIMEOUT_SEC = 5.0        # 单次「打开 + 抓帧」时限
VERIFY_TIMEOUT_SEC = 3.0       # 采纳句柄前的复核时限
ENUM_TOTAL_BUDGET_SEC = 45.0   # 整轮枚举的总预算（兜底，正常用不到）


def _run_with_timeout(fn, timeout):
    """在守护线程里跑 fn，超时返回 (None, True)。

    为什么必须这么做: OpenCV 的 cap.read() / VideoCapture 构造在设备异常时
    会**无限阻塞**，实测某摄像头在 MSMF 后端下直接卡死（DSHOW 却完全正常）。
    没有这层保护，一个后端卡住就会把整个枚举/启动拖死。

    注意: 超时后线程无法强行终止（Python 没这能力）。但它是 daemon，
    不阻止进程退出；且一旦设备被释放，阻塞的调用终会返回。
    调用方每轮最多泄漏一个线程，可以接受。
    """
    box = {}

    def target():
        try:
            box["r"] = fn()
        except Exception as e:  # noqa: BLE001
            box["e"] = e

    t = threading.Thread(target=target, daemon=True, name="probe")
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, True
    if "e" in box:
        raise box["e"]
    return box.get("r"), False


def _verify_cap(cap, check=5):
    """在被采纳前再验证一次：真正能连续读到有效帧才算数。

    为什么需要: 探测多个后端时，打开第二个设备可能会让第一个句柄失效
    （实测 MSMF 探测通过后，因又去探测 DSHOW，最终返回的 MSMF 句柄读帧
    全部失败，表现为画面完全不动、frames_failed 飙升）。所以选定候选后
    必须重新验证，不能直接相信探测阶段的结果。
    """
    def _do():
        good = 0
        for _ in range(check):
            ok, fr = cap.read()
            if ok and fr is not None and fr.size > 0 and float(fr.std()) > 1.0:
                good += 1
        return good >= max(1, check // 2)

    try:
        r, timed_out = _run_with_timeout(_do, VERIFY_TIMEOUT_SEC)
        return bool(r) and not timed_out
    except Exception:
        return False


def try_open_once(index, backend, width, height, warmup=5, check=8,
                  timeout=PROBE_TIMEOUT_SEC):
    """打开一次并实测抓帧。

    返回 (cap, 描述, 实测fps) 或 (None, 失败原因, 0)。

    实测fps 很重要: 同一颗摄像头在不同后端下读帧速度能差一倍
    （实测某摄像头 DSHOW 57ms/帧 ≈ 15fps，MSMF 只要 33ms/帧 ≈ 30fps），
    而 cap.read() 往往是整条流水线里最慢的一步。选后端要按实测速度来，
    不能写死顺序 —— 不同机器结论可能不同。

    timeout: 整个"打开+抓帧"过程的时限。**关键**：某个后端可能永久阻塞
    （实测 HD Camera 在 MSMF 下如此，而 DSHOW 正常），必须能被超时打断，
    否则后面更合适的后端根本没机会尝试。
    """
    def _do():
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            cap.release()
            return None, "打不开", 0.0
        try:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            aw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            ah = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            # 丢弃启动阶段的空帧（摄像头刚打开时前几帧常是黑的）
            for _ in range(warmup):
                cap.read()
            good = blank = fail = 0
            t0 = time.perf_counter()
            for _ in range(check):
                ok, fr = cap.read()
                if not ok or fr is None or fr.size == 0:
                    fail += 1
                elif float(fr.std()) < 1.0:
                    blank += 1          # 纯色/黑屏也算无效
                else:
                    good += 1
            elapsed = max(time.perf_counter() - t0, 1e-6)
            if good == 0:
                reason = "全失败" if fail else ("全空帧" if blank else "无数据")
                cap.release()
                return None, f"{reason}({aw}x{ah})", 0.0
            return cap, f"{aw}x{ah}", good / elapsed
        except Exception as e:
            try:
                cap.release()
            except Exception:
                pass
            return None, f"异常 {type(e).__name__}", 0.0

    result, timed_out = _run_with_timeout(_do, timeout)
    if timed_out:
        # 泄漏的那个线程仍持有设备，尽力而为地不阻塞即可
        return None, f"超时(>{timeout:.0f}s，该后端阻塞)", 0.0
    return result if result else (None, "无结果", 0.0)


def open_camera(index, prefer_size=None, verbose=True, fast=False):
    """打开摄像头并确保真能抓到有效帧。

    会依次尝试 [首选分辨率/各后端] -> [其他常见分辨率]，
    返回 (cap, 描述字符串) 或 (None, None)。

    fast=True 用于枚举场景: 减少校验帧数、只试 2 种分辨率，
    把单个摄像头的探测时间从十几秒压到 1~2 秒。

    重要: 只尝试指定索引，绝不使用 CAP_ANY。原因是另一颗摄像头可能正被
    VRChat 面补等程序占用，CAP_ANY 会让后端自己去挑设备、可能误开那颗，
    加剧抢占甚至把它从别的程序手里抢过来。
    """
    sizes = []
    if prefer_size:
        sizes.append(tuple(prefer_size))
    for s in ((640, 480), (1280, 720), (960, 540)):
        if s not in sizes:
            sizes.append(s)
    if fast:
        sizes = sizes[:2]
    # 只用明确指定设备的方式（DSHOW/MSMF 都接受索引），不用 CAP_ANY。
    # MSMF 优先：实测它读帧更快（30fps vs DSHOW 15fps），而 cap.read()
    # 常占整条流水线 85% 的时间。但不同机器/驱动结论可能相反，
    # 所以下面会实测速度并对候选做一次比较，而不是见到能开就用。
    backends = (("MSMF", cv2.CAP_MSMF), ("DSHOW", cv2.CAP_DSHOW))
    warmup, check = (1, 2) if fast else (5, 8)

    attempts = []
    for (w, h) in sizes:
        for bname, be in backends:
            cap, info, fps = try_open_once(index, be, w, h,
                                           warmup=warmup, check=check)
            if cap is None:
                attempts.append(f"{bname}/{w}x{h}: {info}")
                continue
            # 采纳前再验证一次（见 _verify_cap 的说明）
            if not _verify_cap(cap):
                attempts.append(f"{bname}/{w}x{h}: 探测可读但采纳前验证失败")
                try:
                    cap.release()
                except Exception:
                    pass
                continue
            if verbose:
                logging.info(f"摄像头 #{index} 打开成功: {bname} {info}，"
                             f"实测 {fps:.1f} fps"
                             + (f"（请求 {w}x{h}）"
                                if (w, h) != tuple(prefer_size or (w, h)) else ""))
            if fps < 12:
                logging.warning(
                    f"该后端读帧只有 {fps:.1f} fps，画面会偏卡。"
                    f"若另一颗摄像头更快，可在下拉框里换一颗")
            return cap, f"{bname} {info} {fps:.0f}fps"
    if verbose:
        logging.error(f"摄像头 #{index} 所有后端/分辨率组合都无法抓到有效帧:")
        for a in attempts:
            logging.error(f"    {a}")
    return None, None


def lock_camera(cap):
    """尝试关闭自动曝光/自动白平衡并固定手动参数。返回做了哪些改动。"""
    actions = []
    # AUTO_EXPOSURE 的取值语义随后端而异: DSHOW 下 0.75 常表示手动, MSMF 下 0.25
    for prop, name, cands in (
        (cv2.CAP_PROP_AUTO_EXPOSURE, "自动曝光", [0.25, 0.75, 0, 1]),
        (cv2.CAP_PROP_AUTO_WB, "自动白平衡", [0, 1]),
    ):
        try:
            before = cap.get(prop)
        except Exception:
            continue
        if before < 0:          # 驱动未暴露该项
            continue
        for c in cands:
            try:
                cap.set(prop, c)
            except Exception:
                continue
            after = cap.get(prop)
            if after != before:
                actions.append(f"{name}: {before:g} -> {after:g}")
                break
    return actions


class AsyncOSCSender:
    """基于 asyncio UDP 的简易 OSC 发送器，仅发送 float"""

    def __init__(self, ip, port):
        self.ip = ip
        self.port = port
        self.transport = None

    async def start(self):
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(
            lambda: asyncio.DatagramProtocol(), remote_addr=(self.ip, self.port)
        )

    def send(self, address: str, value: float):
        if not self.transport:
            return
        addr_bytes = address.encode("utf-8") + b"\x00" * (4 - len(address) % 4)
        self.transport.sendto(addr_bytes + b",f\x00\x00" + struct.pack(">f", value))

    def close(self):
        if self.transport:
            self.transport.close()


def send_osc_once(ip, port, address, value):
    """单次发送一条 OSC 浮点消息（供 Web UI 的测试按钮使用）。

    额外返回实际发到的目标地址，便于确认是否真的发出去了。
    """
    import socket
    try:
        addr_bytes = address.encode("utf-8") + b"\x00" * (4 - len(address) % 4)
        packet = addr_bytes + b",f\x00\x00" + struct.pack(">f", float(value))
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.sendto(packet, (ip, int(port)))
        finally:
            s.close()
        return True, f"已发送 {address} = {float(value):.1f}  ->  {ip}:{port}"
    except Exception as e:
        return False, f"发送失败: {type(e).__name__}: {e}"


def osc_pad(data: bytes) -> bytes:
    """OSC 字符串补齐: 末尾至少一个 \\0，且总长度是 4 的倍数"""
    out = data + b"\x00"
    while len(out) % 4:
        out += b"\x00"
    return out


def build_chatbox_packet(text: str, send_immediately: bool = True,
                         play_sound: bool = False) -> bytes:
    """构造 VRChat Chatbox 的 OSC 包。

    地址: /chatbox/input
    参数: [string 文本, bool 立即发送, bool 提示音]
    注意: 第二个参数必须为 True，否则 VRChat 只把文本放进输入框而不发送。
    """
    addr = osc_pad(b"/chatbox/input")
    tags = osc_pad(b",sTT")            # T = OSC True
    args = osc_pad(text.encode("utf-8"))
    args += b"\x01" if send_immediately else b"\x00"
    args += b"\x00\x00\x00"
    args += b"\x01" if play_sound else b"\x00"
    args += b"\x00\x00\x00"
    return addr + tags + args


def send_chatbox(ip, port, text, send_immediately=True, play_sound=False):
    """发送一条 Chatbox 消息，返回 (成功, 说明)"""
    import socket
    try:
        pkt = build_chatbox_packet(text, send_immediately, play_sound)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.sendto(pkt, (ip, int(port)))
        finally:
            s.close()
        return True, f"已发送 Chatbox: {text}"
    except Exception as e:
        return False, f"Chatbox 发送失败: {type(e).__name__}: {e}"


# ==================== 一维卡尔曼（平滑人脸框） ====================
class Kalman1D:
    """平滑人脸框坐标。

    参数说明: 稳态增益 k ≈ (sqrt(q²+4qr) - q) / (2r)，必须让 k 明显大于 0，
    否则框会"冻住"。之前用 q=0.005/r=0.08 时 k≈0.059，人脸移动 50px 需要
    约 20 秒才追上，表现就是"脸在动但框不动"。现在提高 q，k≈0.35，
    既滤掉逐帧抖动（约 2~3 帧时间常数），又能跟上真实移动。
    """

    def __init__(self, init_value, q=0.02, r=0.10):
        self.x = float(init_value)
        self.p = 1.0
        self.q = q
        self.r = r

    def update(self, z):
        self.p += self.q
        k = self.p / (self.p + self.r)
        self.x += k * (float(z) - self.x)
        self.p *= (1.0 - k)
        return self.x

    def nudge(self, z, alpha=0.5):
        """丢失检测时用的"半跟随"，避免框长期停在旧位置"""
        self.x += alpha * (float(z) - self.x)
        return self.x


# ==================== 心率估计 ====================
def estimate_bpm(values, timestamps, fs_uniform=UNIFORM_FS, prev_bpm=None):
    """从 BVP 估计心率，返回 (bpm, ac_peak, info)。

    主算法用自相关: 在官方样例上自相关给出 75.0 BPM（峰 0.7165），
    与频谱法完全一致，而对噪声更稳健、不受 FFT 频率分辨率限制。
    频谱法作为交叉校验。
    """
    if len(values) < MIN_SAMPLES:
        return None, 0.0, {}

    y = np.asarray(values, dtype=np.float64)
    t = np.asarray(timestamps, dtype=np.float64)

    # 安全网: 只要序列里混入一个 NaN/Inf，scipy.detrend/butter 就会抛异常，
    # 把调用方（推理线程）整个搞崩。这里先剔除非法样本。
    finite = np.isfinite(y) & np.isfinite(t)
    if not finite.all():
        y, t = y[finite], t[finite]
        if len(y) < MIN_SAMPLES:
            return None, 0.0, {}

    span = t[-1] - t[0]
    if span <= 2.0:
        return None, 0.0, {}

    # 1) 等间隔重采样（摄像头帧率会波动，FFT/自相关都要求均匀采样）
    n_u = int(span * fs_uniform)
    if n_u < MIN_SAMPLES:
        return None, 0.0, {}
    t_u = np.linspace(t[0], t[-1], n_u)
    try:
        from scipy.interpolate import interp1d
        y_u = interp1d(t, y, kind="cubic", bounds_error=False,
                       fill_value="extrapolate")(t_u)
    except Exception:
        y_u = np.interp(t_u, t, y)

    # 2) 去趋势 + 带通
    y_u = sp_signal.detrend(y_u, type="linear")
    nyq = fs_uniform / 2.0
    lo, hi = BPM_MIN / 60.0, min(BPM_MAX / 60.0, nyq * 0.95)
    if lo >= hi:
        return None, 0.0, {}
    b, a = sp_signal.butter(3, [lo / nyq, hi / nyq], btype="band")
    try:
        x = sp_signal.filtfilt(b, a, y_u)
    except Exception:
        x = sp_signal.lfilter(b, a, y_u)
    x = x - x.mean()

    # 3) 自相关（用 FFT 加速）
    n = len(x)
    if n < 32:
        return None, 0.0, {}
    ac = sp_signal.fftconvolve(x, x[::-1], mode="full")[n - 1:]
    if ac[0] <= 1e-12:
        return None, 0.0, {}
    ac = ac / ac[0]

    lag_min = max(1, int(fs_uniform * 60.0 / BPM_MAX))
    lag_max = min(int(fs_uniform * 60.0 / BPM_MIN), len(ac) - 2)
    if lag_max <= lag_min + 1:
        return None, 0.0, {}
    seg = ac[lag_min:lag_max]
    k = int(np.argmax(seg))
    ac_peak = float(seg[k])

    # 抛物线插值细化 lag
    if 0 < k < len(seg) - 1:
        a0, a1, a2 = seg[k - 1], seg[k], seg[k + 1]
        den = a0 - 2 * a1 + a2
        dl = 0.5 * (a0 - a2) / den if abs(den) > 1e-15 else 0.0
        dl = max(-0.5, min(0.5, dl))
    else:
        dl = 0.0
    lag = lag_min + k + dl
    bpm_ac = 60.0 * fs_uniform / lag

    # 4) Welch PSD 交叉校验
    nper = int(min(n, 8 * fs_uniform))
    bpm_sp, sp_ratio = None, 0.0
    if nper >= 32:
        f, pxx = sp_signal.welch(x, fs=fs_uniform, window="hann",
                                 nperseg=nper, noverlap=nper // 2,
                                 detrend=False, scaling="density")
        m = (f >= lo) & (f <= hi)
        if m.any():
            fb, pb = f[m], pxx[m]
            i = int(np.argmax(pb))
            bpm_sp = float(fb[i] * 60.0)
            sp_ratio = float(pb[i] / (pb.sum() + 1e-12))

    # 5) 两法若相差过大，说明信号有歧义，降低可信度
    agree = True
    if bpm_sp is not None:
        agree = abs(bpm_ac - bpm_sp) < max(8.0, 0.10 * bpm_ac)

    bpm = bpm_ac
    if not agree and sp_ratio > 0.25:
        # 频谱在生理频带内非常突出时，以频谱为准
        bpm = bpm_sp

    info = {
        "ac_peak": ac_peak,
        "sp_bpm": bpm_sp,
        "sp_ratio": sp_ratio,
        "agree": agree,
        "n": n,
        "win_s": span,
    }
    return float(bpm), ac_peak, info


# ==================== 采集 + 推理线程 ====================
class CameraRPPG:
    def __init__(self, model, detector, camera_index, show_preview,
                 min_ac_peak=MIN_AC_PEAK, prefer_size=None):
        self.model = model
        self.detector = detector
        self.camera_index = camera_index
        self.show_preview = show_preview
        self.min_ac_peak = min_ac_peak
        self.prefer_size = prefer_size
        self.stop_flag = threading.Event()
        self.thread = None
        self.lock = threading.Lock()
        self.bpm = None
        self.ac_peak = 0.0
        self.sp_ratio = 0.0
        self.agree = False
        self.raw_bpm = []
        self.fps = 0.0
        self.face_seen = False
        self.last_error = None
        self.trace = []            # 预览用的 BVP 片段
        self.debug_path = None
        self._dbg = []
        # 诊断用：区分"没打开摄像头"、"打开但读不到帧"、"读得到帧但没检测到人脸"
        self.cam_opened = False
        self.cam_backend = None
        self.frames_read = 0
        self.frames_failed = 0
        self.nan_frames = 0
        # 虚拟摄像头（把画面转发给面补用，避免抢物理摄像头）
        self.vcam = None
        self.vcam_device = None
        self._vcam_dropped = 0
        # 预览线程的统计（用于排查"画面卡顿"，生产环境也能显示）
        self._pv_idle = 0
        self._pv_rendered = 0
        self._pv_render_ms = 0.0
        self._pv_render_err = 0
        # 预览画面: 由独立的预览线程产出，主线程/推理线程都不做绘制。
        # 教训: 曾把绘制放在推理线程里（每 3 帧一次 frame.copy + cvtColor +
        # resize + polylines，640x480 上约 15~20ms），结果把 30fps 的推理
        # 拖到 0.5fps，预览变成 2~3 秒一帧。绘制必须与推理解耦。
        self._preview_lock = threading.Lock()
        self._preview_frame = None
        self._preview_seq = 0
        self._pv_thread = None
        # 推理线程只写这些轻量字段（赋值都是原子的，无需加锁）
        self._pv_raw = None            # 最新原始帧（BGR）
        self._pv_box = None            # (sx, sy, ex, ey)
        self._pv_crop = None           # 36x36 裁剪图
        self._pv_raw_seq = 0
        # 预览叠加用的统计字段：推理线程单写、预览/主线程单读，故意不加锁。
        # 若这里去拿锁，会和主线程的 get_preview() 形成交叉等待，导致 UI 卡顿。
        self._pv = {"bpm": None, "ac": 0.0, "agree": False, "fps": 0.0}
        # 波形用"整体替换新列表"的方式更新，赋值本身是原子的
        self._pv_trace = []
        # 供 Web UI 使用的成品帧（已带标注的 RGB）
        self._ui_frame = None
        # 运行时切换摄像头: 工作线程轮询该变量，变化则重开设备
        self._want_index = camera_index
        self._index_changed = threading.Event()
        self.switch_error = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name="rppg", daemon=True)
        self.thread.start()
        # 独立的预览绘制线程（限速，避免抢占推理与 CPU）
        self._pv_thread = threading.Thread(target=self._preview_worker,
                                           name="preview", daemon=True)
        self._pv_thread.start()

    def stop(self):
        self.stop_flag.set()
        if self.thread:
            self.thread.join(timeout=5.0)
        self.stop_virtual_cam()

    # ---------- 虚拟摄像头输出 ----------
    # 用途: 本程序独占物理摄像头后，把画面**同时**转发到一个虚拟摄像头，
    # 这样 VRChat 面补（VRCFaceTracking 等）去读虚拟摄像头即可，
    # 两边不再抢同一个物理设备。
    # 实现: pyvirtualcam（Windows 上走 OBS 虚拟摄像头驱动，不需要 OBS 运行）
    def start_virtual_cam(self, width, height, fps=30):
        """启动虚拟摄像头输出。返回 (成功, 说明)"""
        if self.vcam is not None:
            return True, f"虚拟摄像头已在运行: {self.vcam.device}"
        try:
            import pyvirtualcam
        except ImportError:
            return False, "未安装 pyvirtualcam（pip install pyvirtualcam）"
        try:
            cam = pyvirtualcam.Camera(width=int(width), height=int(height),
                                      fps=int(fps),
                                      fmt=pyvirtualcam.PixelFormat.BGR,
                                      print_fps=False)
            self.vcam = cam
            self._vcam_dropped = 0
            # 供状态显示
            self.vcam_device = cam.device
            logging.info(f"虚拟摄像头已启动: {cam.device} "
                         f"{cam.width}x{cam.height} @ {cam.fps}fps")
            return True, f"{cam.device} ({cam.width}x{cam.height}@{cam.fps}fps)"
        except Exception as e:
            self.vcam = None
            return False, (f"虚拟摄像头启动失败: {type(e).__name__}: {e}。"
                           f"请确认已安装 OBS（其虚拟摄像头驱动是必需的）")

    def stop_virtual_cam(self):
        cam, self.vcam = self.vcam, None
        self.vcam_device = None
        if cam is not None:
            try:
                cam.close()
                logging.info("虚拟摄像头已关闭")
            except Exception:
                pass

    def send_virtual_frame(self, frame):
        """把一帧送到虚拟摄像头（失败不抛异常，避免影响推理）"""
        cam = self.vcam
        if cam is None:
            return
        try:
            h, w = frame.shape[:2]
            # 分辨率必须与创建时一致，不一致就跳过（并计数）
            if w != cam.width or h != cam.height:
                f = cv2.resize(frame, (cam.width, cam.height),
                               interpolation=cv2.INTER_AREA)
            else:
                f = frame
            cam.send(f)
            cam.sleep_until_next_frame()
        except Exception:
            self._vcam_dropped += 1
            if self._vcam_dropped in (1, 100, 1000):
                logging.warning(f"虚拟摄像头送帧失败（已累计 {self._vcam_dropped} 次）")

    def snapshot(self):
        with self.lock:
            return (self.bpm, self.ac_peak, self.sp_ratio, self.agree,
                    self.fps, self.face_seen, self.last_error,
                    list(self.trace))
    
    def diag(self):
        """给状态打印用的诊断信息"""
        with self.lock:
            return (self.cam_opened, self.cam_backend,
                    self.frames_read, self.frames_failed)

    def _run(self):
        """采集主循环。

        外层循环负责"打开设备 / 被要求换设备后重开"，内层循环负责逐帧处理。
        Web UI 切换摄像头时只需设置 _want_index，这里会在下一轮自动重开，
        React 保证同一时刻只有一个进程持有摄像头。
        """
        while not self.stop_flag.is_set():
            idx = self._want_index
            self._index_changed.clear()
            cap = self._open_and_prepare(idx)
            if cap is None:
                # 打不开时不要空转刷屏，等一会儿或等用户换设备
                for _ in range(30):
                    if self.stop_flag.is_set() or self._index_changed.is_set():
                        break
                    time.sleep(0.1)
                continue
            try:
                self._process_loop(cap)
            except Exception as e:
                # 单帧处理出问题绝不能让整条采集链断掉 —— 之前一次
                # scipy 的 NaN 异常就把推理线程终结了，界面表现为永久冻住。
                # 这里记下错误、重开设备继续跑。
                import traceback
                logging.error(f"采集循环异常，将重试: {type(e).__name__}: {e}")
                logging.debug(traceback.format_exc())
                with self.lock:
                    self.last_error = f"采集异常已自动恢复: {type(e).__name__}"
                time.sleep(1.0)
            finally:
                try:
                    cap.release()
                except Exception:
                    pass
                with self.lock:
                    self.cam_opened = False
            if self._index_changed.is_set():
                logging.info(f"正在切换到摄像头 #{self._want_index} ...")
        logging.debug("采集线程结束")

    def _open_and_prepare(self, idx):
        """打开指定摄像头并尝试锁定曝光/白平衡，返回 cap 或 None"""
        logging.info(f"正在打开摄像头 #{idx} ...")
        cap, info = open_camera(idx, prefer_size=self.prefer_size)
        if cap is None:
            msg = (f"摄像头 #{idx} 所有后端/分辨率组合都抓不到有效帧 —— "
                   f"可能被其他程序独占（相机 App/浏览器/OBS/NVIDIA Broadcast 等），"
                   f"或索引无效")
            logging.error(msg)
            self.switch_error = msg
            with self.lock:
                self.last_error = msg
                self.cam_opened = False
            return None
        with self.lock:
            self.cam_opened = True
            self.cam_backend = info
            self.last_error = None
        self.switch_error = None
        logging.info(f"摄像头 #{idx} 已就绪: {info}")
        acts = lock_camera(cap)
        if acts:
            logging.info(f"已调整摄像头控制: {', '.join(acts)}")
        else:
            logging.warning("摄像头不支持锁定曝光/白平衡 —— 信号质量会明显变差，"
                            "请尽量保持光照稳定、避免屏幕亮度变化")
        return cap

    def _process_loop(self, cap):
        """逐帧处理: 人脸检测 → 裁剪 → FacePhys → 心率估计"""
        state = {kk: vv.copy() for kk, vv in self.model.init_state.items()}
        values, stamps = [], []
        kfs = None
        last_box = None
        frame_i = 0
        last_detect_ok = 0.0
        prev_t = None
        fps_t0, fps_n = time.time(), 0
        last_est = 0.0
        self.raw_bpm = []

        try:
            while not self.stop_flag.is_set() and not self._index_changed.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    with self.lock:
                        self.frames_failed += 1
                    time.sleep(0.005)
                    continue
                with self.lock:
                    self.frames_read += 1
                now = time.time()
                h, w = frame.shape[:2]
                frame_i += 1
                # 转发给虚拟摄像头（若已启用）—— 让面补能读到同一画面。
                # 送帧内部已做异常与尺寸处理，不会影响推理。
                if self.vcam is not None:
                    self.send_virtual_frame(frame)
                # 注: _ui_frame 由预览线程的 _render_preview 统一写入（带标注的
                # RGB），不要在这里直接塞原始帧，否则会覆盖掉标注。

                # 人脸检测: 按间隔执行; 若距上次成功检测太久则强制重检
                need_detect = (frame_i % DETECT_INTERVAL == 0
                               or last_box is None
                               or (now - last_detect_ok) > 1.5)
                if need_detect:
                    self.detector.setInputSize((w, h))
                    _, faces = self.detector.detect(frame)
                    if faces is not None and len(faces) > 0:
                        f = max(faces, key=lambda r: r[2] * r[3])
                        if kfs is None:
                            kfs = tuple(Kalman1D(float(f[q])) for q in range(4))
                            box = tuple(float(f[q]) for q in range(4))
                        else:
                            box = tuple(kfs[q].update(f[q]) for q in range(4))
                        # 垂直放大 1.2x 并上移 20%（含额头），与官方 demo 一致
                        bh2 = box[3] * 1.2
                        last_box = (box[0], box[1] - bh2 * 0.2, box[2], bh2)
                        last_detect_ok = now
                        with self.lock:
                            self.face_seen = True
                    else:
                        with self.lock:
                            self.face_seen = False
                        # 连续丢检时逐渐放开框，避免框在旧位置僵死
                        if last_box is not None and (now - last_detect_ok) > 2.0:
                            last_box = None
                            kfs = None

                if last_box is None:
                    prev_t = now
                    time.sleep(0.005)
                    continue

                bx, by, bw, bh = last_box
                if not all(np.isfinite([bx, by, bw, bh])):
                    # 框坐标出现 NaN/Inf 时直接丢弃并复位，否则 int(round()) 会抛异常
                    last_box = None
                    kfs = None
                    prev_t = now
                    continue
                sx = max(0, int(round(bx))); sy = max(0, int(round(by)))
                ex = min(w, int(round(bx + bw))); ey = min(h, int(round(by + bh)))
                if ex - sx < 8 or ey - sy < 8:
                    # 框太小或大部分在画面外时，裁剪基本没有有效皮肤像素，
                    # 喂给模型容易产生非法输出（NaN/Inf），所以直接跳过
                    prev_t = now
                    continue

                small = cv2.resize(frame[sy:ey, sx:ex], (CROP_SIZE, CROP_SIZE),
                                   interpolation=cv2.INTER_AREA)
                if float(small.std()) < 1.0:
                    # 全黑/纯色区域 -> 模型输入没有信息，跳过以免产生 NaN
                    prev_t = now
                    continue
                rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0

                dt = 1.0 / 30.0 if prev_t is None else now - prev_t
                dt = max(1.0 / 240.0, min(dt, 1.0))
                prev_t = now

                r, state = self.model(rgb, state, dt=np.float32(dt))
                r = float(r)
                # 模型偶尔会吐出 NaN/Inf（实测会，来源是异常裁剪）。
                # 一旦把这种值放进 BVP 序列，后面 scipy.detrend 会直接抛
                # "array must not contain infs or NaNs" 把推理线程整个搞崩。
                # 这里直接丢弃这一帧，并重置 SSM 状态以免被污染。
                if not math.isfinite(r):
                    self.nan_frames += 1
                    state = {kk: vv.copy() for kk, vv in self.model.init_state.items()}
                    continue
                values.append(r)
                stamps.append(now)
                if self.debug_path:
                    self._dbg.append((now, float(r), dt))

                cutoff = now - BVP_WINDOW_SEC
                while stamps and stamps[0] < cutoff:
                    stamps.pop(0)
                    values.pop(0)

                if now - last_est >= 1.0 and len(values) >= MIN_SAMPLES:
                    last_est = now
                    with self.lock:
                        prev = self.bpm
                    bpm, ac_peak, info = estimate_bpm(values, stamps, prev_bpm=prev)
                    if bpm is not None:
                        self.raw_bpm.append(bpm)
                        if len(self.raw_bpm) > MEDIAN_WINDOW:
                            self.raw_bpm.pop(0)
                        smoothed = (float(np.median(self.raw_bpm))
                                    if len(self.raw_bpm) >= 3
                                    else float(np.mean(self.raw_bpm)))
                        agree_v = info.get("agree", False)
                        with self.lock:
                            self.bpm = smoothed
                            self.ac_peak = ac_peak
                            self.sp_ratio = info.get("sp_ratio", 0.0)
                            self.agree = agree_v
                        # 同步无锁快照供预览使用
                        self._pv["bpm"] = smoothed
                        self._pv["ac"] = ac_peak
                        self._pv["agree"] = agree_v

                fps_n += 1
                if now - fps_t0 >= 1.0:
                    cur_fps = fps_n / (now - fps_t0)
                    with self.lock:
                        self.fps = cur_fps
                    self._pv["fps"] = cur_fps
                    fps_t0, fps_n = now, 0

                # 波形数据与预览帧每帧都发布 —— 都只是原子赋值，成本可忽略。
                # 之前用 `% 3` 节流是多余的：预览线程自己已按 PREVIEW_FPS 限速，
                # 而节流会把 15fps 的采集再砍到 5fps，反而让画面更卡。
                self._pv_trace = values[-300:]
                self._pv_raw = frame
                self._pv_box = (sx, sy, ex, ey)
                self._pv_crop = small
                self._pv_raw_seq += 1

        finally:
            # 注意: cap 由 _run 负责 release（切换摄像头时也要重开）。
            # 这里不要 destroyAllWindows —— 切换摄像头时预览窗口应当继续存在。
            if self.debug_path and self._dbg:
                try:
                    np.save(self.debug_path, np.array(self._dbg, dtype=np.float64))
                    logging.info(f"诊断数据已保存: {self.debug_path}")
                except Exception as e:
                    logging.error(f"保存诊断数据失败: {e}")

    def _preview_worker(self):
        """独立的预览绘制线程。

        推理线程只做 `self._pv_raw = frame` 这种原子赋值；绘制（copy / resize /
        画框 / 画波形 / BGR->RGB）全部在这里做，并按 PREVIEW_FPS 限速。
        这样即使绘制耗时十几毫秒，也不会拖慢推理。
        """
        last_seq = -1
        last_t = 0.0
        min_dt = 1.0 / max(1.0, PREVIEW_FPS)
        while not self.stop_flag.is_set():
            seq = self._pv_raw_seq
            now = time.time()
            if seq == last_seq or (now - last_t) < min_dt:
                time.sleep(0.004)
                self._pv_idle += 1
                continue
            last_seq, last_t = seq, now
            try:
                t0 = time.perf_counter()
                self._render_preview()
                self._pv_render_ms += (time.perf_counter() - t0) * 1000.0
                self._pv_rendered += 1
            except Exception as e:
                self._pv_render_err += 1
                if self._pv_render_err in (1, 50):
                    logging.warning(f"预览绘制失败（第 {self._pv_render_err} 次）: "
                                    f"{type(e).__name__}: {e}")

    def _render_preview(self):
        """构造带标注的预览图（只由 _preview_worker 调用）。"""
        frame = self._pv_raw
        if frame is None:
            return
        box = self._pv_box
        small = self._pv_crop
        vis = frame.copy()
        # 读无锁快照，不要在这里拿 self.lock（避免与主线程交叉等待）
        pv = self._pv
        bpm = pv["bpm"]
        ac = pv["ac"]
        fps = pv["fps"]
        agree = pv["agree"]
        if ac >= GOOD_AC_PEAK:
            level, color = "良好", (0, 255, 0)
        elif ac >= MIN_AC_PEAK:
            level, color = "可用", (0, 220, 255)
        else:
            level, color = "信号弱", (0, 0, 255)
        if box is not None:
            sx, sy, ex, ey = box
            cv2.rectangle(vis, (sx, sy), (ex, ey), color, 2)
        if small is not None:
            vis[96:204, 12:120] = cv2.resize(small, (108, 108),
                                             interpolation=cv2.INTER_NEAREST)
            cv2.rectangle(vis, (12, 96), (120, 204), color, 1)
        txt = (f"{bpm:.0f} BPM" if (bpm is not None and ac >= MIN_AC_PEAK)
               else "-- BPM")
        cv2.putText(vis, txt, (12, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)
        cv2.putText(vis, f"signal {level} ({ac:.2f})  {fps:.0f}fps  "
                         f"{'agree' if agree else 'disagree'}",
                    (12, 66), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (210, 210, 210), 1)
        if ac < GOOD_AC_PEAK:
            cv2.putText(vis, "hold still / face camera / steady light",
                        (12, 86), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                        (180, 180, 180), 1)
        # BVP 波形（局部变量，避免与推理线程共享大列表）
        trace = self._pv_trace
        if len(trace) > 10:
            tw, th = 250, 70
            x0, y0 = vis.shape[1] - tw - 12, 12
            tr = np.asarray(trace, dtype=np.float32)
            # 同样要清洗 NaN/Inf，否则归一化与取整都会出问题
            tr = tr[np.isfinite(tr)]
            if len(tr) > 10:
                tr = tr - tr.min()
                if tr.max() > 1e-6:
                    tr = tr / tr.max()
                pts = np.array(
                    [(x0 + int(i * (tw - 1) / (len(tr) - 1)),
                      y0 + th - 4 - int(v * (th - 8)))
                     for i, v in enumerate(tr)], dtype=np.int32)
                cv2.rectangle(vis, (x0, y0), (x0 + tw, y0 + th), (60, 60, 60), 1)
                cv2.polylines(vis, [pts], False, (0, 255, 128), 1)
        rgb = cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)
        with self._preview_lock:
            self._preview_frame = vis
            self._ui_frame = rgb
            self._preview_seq += 1

    def get_preview(self):
        """主线程取最新成品预览图，返回 (seq, image) 或 (seq, None)"""
        with self._preview_lock:
            return self._preview_seq, self._preview_frame

    def set_camera(self, index):
        """运行时切换摄像头（由 Web UI 调用）。工作线程会在下一轮循环重开设备。"""
        try:
            index = int(index)
        except Exception:
            return
        with self._preview_lock:
            if index == self._want_index:
                return
            self._want_index = index
        self._index_changed.set()
        self.switch_error = None

    def jpeg_frame(self, max_width=560):
        """取一张供 Web UI 显示的帧（已带叠加标注）。

        返回 RGB 的 numpy 数组 —— gr.Image 不接受 JPEG bytes
        （会报 "Cannot process this value as an Image"）。
        绘制工作已在预览线程的 _render_preview 里完成，这里只做缩放。
        """
        with self._preview_lock:
            f = self._ui_frame
        if f is None:
            return None
        try:
            h, w = f.shape[:2]
            if w > max_width:
                nh = max(1, int(h * max_width / w))
                f = cv2.resize(f, (max_width, nh), interpolation=cv2.INTER_AREA)
            return f          # 已经是 RGB
        except Exception:
            return None

    def ui_stats(self):
        """Web UI 用的状态快照"""
        pv = self._pv
        with self.lock:
            return {
                "bpm": pv["bpm"],
                "ac": pv["ac"],
                "agree": pv["agree"],
                "fps": pv["fps"],
                "cam_opened": self.cam_opened,
                "backend": self.cam_backend,
                "frames_read": self.frames_read,
                "frames_failed": self.frames_failed,
                "face_seen": self.face_seen,
                "error": self.last_error,
                "switch_error": self.switch_error,
                "camera_index": self._want_index,
                "trace": list(self._pv_trace),
            }


# ==================== 心率平滑与超时动画 ====================
class HeartrateSmoother:
    def __init__(self, osc, osc_address, source: CameraRPPG):
        self.osc = osc
        self.osc_address = osc_address
        self.source = source
        self.current_value = None
        self.target_value = None
        self.last_data_time = 0.0
        self.timeout_triggered = False
        self.last_chatbox = 0.0
        self.chatbox_sent = 0

    async def run(self):
        try:
            while not shutdown_event.is_set():
                now = time.time()
                bpm, ac, spr, agree, fps, face, err, _ = self.source.snapshot()

                # 只在信号足够可信时才更新，避免把噪声当心率推给 VRChat
                if bpm is not None and ac >= MIN_AC_PEAK:
                    self.target_value = float(bpm)
                    self.last_data_time = now
                    self.timeout_triggered = False

                if not self.timeout_triggered and (now - self.last_data_time) > TIMEOUT_SECONDS:
                    self.timeout_triggered = True
                    logging.warning("心率信号丢失，启动断连波动动画")

                if self.timeout_triggered:
                    value = (math.sin(now * 2 * math.pi / ANIMATION_PERIOD) + 1) * 50
                    if self.current_value is None or abs(value - self.current_value) > 0.1:
                        self.current_value = value
                        self.osc.send(self.osc_address, float(value))
                else:
                    cur, tgt = self.current_value, self.target_value
                    if cur is None and tgt is not None:
                        self.current_value = 0.0
                    elif cur is not None and tgt is not None:
                        nv = cur + SMOOTHING_FACTOR * (tgt - cur)
                        if abs(nv - tgt) < 0.05:
                            nv = tgt
                        if nv != cur:
                            self.current_value = nv
                            self.osc.send(self.osc_address, float(nv))

                # --- Chatbox: 定期把当前心率发到聊天框 ---
                # 用平滑后的 current_value（更稳），并限制发送间隔 ——
                # VRChat 对 chatbox 有频率限制，发太快消息会被丢弃
                if (CHATBOX_ENABLE and not self.timeout_triggered
                        and self.current_value is not None
                        and (now - self.last_chatbox) >= CHATBOX_INTERVAL):
                    self.last_chatbox = now
                    try:
                        text = (CHATBOX_TEMPLATE or "当前心率：{hr}").format(
                            hr=int(round(self.current_value)))
                    except Exception:
                        text = f"当前心率：{int(round(self.current_value))}"
                    # 超长会被 VRChat 截断，这里先自己限一下
                    if len(text) > 144:
                        text = text[:144]
                    ok, _ = send_chatbox(VRC_OSC_IP, VRC_OSC_PORT, text,
                                         True, CHATBOX_SOUND)
                    if ok:
                        self.chatbox_sent += 1

                await asyncio.sleep(SEND_INTERVAL)
        except asyncio.CancelledError:
            pass


def config_path():
    """配置保存位置（记住上次选的摄像头）"""
    d = os.path.join(os.path.expanduser("~"), ".vrc_heartrate_cam")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return None
    return os.path.join(d, "config.json")


def save_camera_index(index):
    p = config_path()
    if not p:
        return
    try:
        import json
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"camera": int(index)}, f)
    except Exception:
        pass


def load_camera_index():
    p = config_path()
    if not p or not os.path.isfile(p):
        return None
    try:
        import json
        with open(p, "r", encoding="utf-8") as f:
            v = json.load(f).get("camera")
        return int(v) if v is not None else None
    except Exception:
        return None


def coerce_camera_index(v):
    """把下拉框的值统一成索引整数。

    双保险: 显式设了 type="value" 之后应拿到索引，但不同 Gradio 版本行为
    有差异 —— 实测曾把 label（"#0  640x480   ★有人脸"）传回来，导致
    int() 解析失败。这里两种情况都能正确处理。
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    m = re.match(r"^#?\s*(\d+)", s)
    if m:
        return int(m.group(1))
    try:
        return int(s)
    except Exception:
        return None


def get_camera_names():
    """取摄像头名称，顺序与 OpenCV 的索引一致。

    踩过的坑: 一开始用 DirectShow 注册表
    (HKLM\\...\\CLSID\\{860BB310-...}\\Instance) 的顺序，结果**张冠李戴** ——
    DirectShow 的顺序是 WarudoCam/OBS 等虚拟设备在前，而 OpenCV 用的
    Media Foundation 走的是 "Video Capture" 类别
    ({e5323777-f976-4f5b-9b55-b94699c46e44})，顺序完全不同
    （实测 MSMF 只有 0/1 两个物理摄像头）。

    只有用 Video Capture 类别才能对上索引。取不到名称就返回空，
    让调用方退回到"用画面特征描述"，绝不乱贴名字。
    """
    import subprocess
    try:
        ps = (
            "$c='{e5323777-f976-4f5b-9b55-b94699c46e44}';"
            "$b=\"HKLM:\\SYSTEM\\CurrentControlSet\\Control\\DeviceClasses\\$c\";"
            "$devs = Get-PnpDevice -ErrorAction SilentlyContinue;"
            "Get-ChildItem $b -ErrorAction SilentlyContinue | ForEach-Object {"
            "  $sym = $_.PSChildName;"
            "  foreach ($d in $devs) {"
            "    $pat = $d.InstanceId.Replace('\\','#');"
            "    if ($sym -like \"*$pat*\") { $d.FriendlyName; break }"
            "  }"
            "}"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace")
        return [n.strip() for n in (out.stdout or "").splitlines() if n.strip()]
    except Exception as e:
        logging.debug(f"获取摄像头名称失败: {e}")
        return []


def camera_label(index, name):
    """给设备名加上类型提示，减少用户选错"""
    if not name:
        return f"#{index}"
    tag = ""
    if re.search(r"nvidia|broadcast", name, re.I):
        tag = "  [虚拟·NVIDIA]"
    elif re.search(r"obs", name, re.I):
        tag = "  [虚拟·OBS]"
    elif re.search(r"warudo", name, re.I):
        tag = "  [虚拟·Warudo]"
    elif re.search(r"\bIR\b|infrared|红外", name, re.I):
        tag = "  [红外]"
    return f"#{index}  {name}{tag}"


def _probe_with_timeout(fn, timeout):
    """兼容旧调用名（实际实现在 _run_with_timeout）"""
    return _run_with_timeout(fn, timeout)


def enumerate_cameras(max_index=6, yunet_path=None, face_frames=3):
    """枚举可用摄像头，并顺带检查每个能否看到人脸。

    返回 [{"index", "backend", "size", "has_face", "name"}, ...]
    标出"有人脸"能立刻告诉用户该选哪个，省得逐个试。

    速度说明: 枚举慢主要来自三块 —— 不存在的索引要等驱动超时、
    不支持的索引要走分辨率回退、以及设备释放开销。因此默认只扫 0~5，
    人脸校验帧数也压到最少。需要扫更大索引时用 --max-index。

    健壮性: 超时保护下沉在 try_open_once 里（每个后端单独计时），
    所以某个后端阻塞不会让整颗摄像头被漏掉 —— 实测 HD Camera 正是
    这种情况：MSMF 永久阻塞、DSHOW 正常，必须靠 DSHOW 把它列出来。
    这里只保留一个总预算兜底，防止极端情况下无休止扫描。
    """
    t_start = time.time()
    det = None
    if yunet_path and os.path.isfile(yunet_path):
        try:
            det = cv2.FaceDetectorYN.create(opencv_safe_path(yunet_path), "",
                                            (320, 320), 0.5, 0.3, 5000)
        except Exception as e:
            logging.warning(f"人脸检测器初始化失败（枚举将跳过人脸检查）: {e}")
            det = None

    names = get_camera_names()
    out = []
    skipped = []
    for i in range(max_index):
        if time.time() - t_start > ENUM_TOTAL_BUDGET_SEC:
            skipped.append(f"#{i}+（超出总预算）")
            logging.warning(
                f"摄像头枚举已用 {ENUM_TOTAL_BUDGET_SEC:.0f} 秒，停止继续扫描。"
                f"若确实有更多摄像头，用 --max-index 配合空闲的摄像头重试")
            break

        try:
            cap, info = open_camera(i, verbose=False, fast=True)
        except Exception as e:
            logging.debug(f"摄像头 #{i} 探测异常: {e}")
            continue
        if cap is None:
            continue

        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        has_face = False
        if det is not None:
            hits = checked = 0
            for _ in range(face_frames):
                ok, fr = cap.read()
                if not ok or fr is None:
                    continue
                checked += 1
                det.setInputSize((fr.shape[1], fr.shape[0]))
                _, faces = det.detect(fr)
                if faces is not None and len(faces) > 0:
                    hits += 1
            has_face = checked > 0 and (hits / checked) >= 0.5
        try:
            cap.release()
        except Exception:
            pass
        out.append({"index": i, "backend": info, "size": (w, h),
                    "has_face": has_face,
                    "name": names[i] if i < len(names) else ""})
        logging.debug(f"枚举到摄像头 #{i}: {info} 有人脸={has_face}")

    if skipped:
        logging.warning(f"以下索引未扫描: {', '.join(skipped)}")

    # 有人脸的排前面
    out.sort(key=lambda c: (not c["has_face"], c["index"]))
    logging.info(f"摄像头枚举完成: 找到 {len(out)} 个 -> "
                 + ", ".join(f"#{c['index']}({c['backend']})" for c in out))
    return out


# ==================== Web UI (Gradio) ====================
WEB_CSS = """
.gr-image img { image-rendering: auto; }
#bpm-box { text-align: center; }
#bpm-val { font-size: 46px; font-weight: 700; line-height: 1.1; }
"""


class HeartRateWebUI:
    """Gradio 网页界面。

    关键设计: 摄像头只由 CameraRPPG 的采集线程持有，UI 只从它取帧，
    绝不自己开摄像头 —— 否则两个 cv2.VideoCapture 抢同一个设备，
    会表现为"一帧都读不到"。
    """

    def __init__(self, model_path, yunet_path, host="127.0.0.1", port=7860,
                 prefer_size=None, max_index=6):
        self.model_path = model_path
        self.yunet_path = yunet_path
        self.host = host
        self.port = port
        self.prefer_size = prefer_size
        self.max_index = max_index
        self.cam = None
        self.smoother = None
        self.smoother_thread = None
        self.smoother_loop = None
        self.osc = None
        self.running = False
        self.osc_ip = VRC_OSC_IP
        self.osc_port = VRC_OSC_PORT
        self.osc_address = OSC_ADDRESS
        # 摄像头列表缓存：枚举要逐个开关设备并读帧（约数秒），
        # 绝不能放进 every 轮询里，否则会把队列堵死（表现为界面一直 processing）
        self._cam_cache = None
        # 轮询重入保护: Gradio 的 every 循环不会等上一次返回，
        # 若单次处理偏慢，请求会越积越多把队列堵死。这里直接跳过本轮。
        self._poll_busy = threading.Lock()
        self._poll_skipped = 0
        self.vcam_error = None
        # 摄像头枚举改为后台线程 —— 它要 10~17 秒，放在 build() 里会让
        # 浏览器一直转圈，用户以为程序坏了
        self._enum_thread = None
        self._enum_ready = threading.Event()
        # 用户选过的摄像头（避免刷新/重载把他的选择覆盖掉）
        self._preferred_camera = None
        self._user_picked = None

    # ---------- 模型与采集 ----------
    def load_models(self):
        logging.info("加载 FacePhys 模型...")
        self.run = load_facephys(self.model_path)
        logging.info(f"  FacePhys 就绪（{len(self.run.init_state)} 个状态张量）")
        logging.info("加载 YuNet 人脸检测...")
        # OpenCV 读不了非 ASCII 路径，这里换成 ASCII 安全的副本
        self.detector_src = opencv_safe_path(self.yunet_path)

    def make_detector(self):
        return cv2.FaceDetectorYN.create(self.detector_src, "", (320, 320),
                                         0.6, 0.3, 5000)

    def start_capture(self, camera_index, want_vcam=False):
        """启动采集 + OSC 平滑线程；want_vcam 时同时开启虚拟摄像头输出"""
        if self.cam is not None:
            self.stop_capture()
        self.cam = CameraRPPG(self.run, self.make_detector(), camera_index,
                              show_preview=False, prefer_size=self.prefer_size)
        self.cam.start()
        if want_vcam:
            # 等物理摄像头确定分辨率后再建虚拟摄像头，尺寸要对齐
            for _ in range(50):
                st = self.cam.ui_stats()
                if st.get("cam_opened"):
                    break
                time.sleep(0.1)
            pre = self.prefer_size or (640, 480)
            ok, msg = self.cam.start_virtual_cam(pre[0], pre[1], 30)
            if ok:
                logging.info(f"虚拟摄像头就绪: {msg}")
            else:
                logging.error(f"虚拟摄像头启动失败: {msg}")
                self.vcam_error = msg
        # OSC 发送线程（自带事件循环）
        self.osc = AsyncOSCSender(self.osc_ip, int(self.osc_port))
        self.smoother = HeartrateSmoother(self.osc, self.osc_address, self.cam)
        self.smoother_thread = threading.Thread(
            target=self._run_smoother, name="osc", daemon=True)
        self.smoother_thread.start()
        self.running = True
        logging.info(f"已开始采集: 摄像头 #{camera_index}, "
                     f"OSC -> {self.osc_ip}:{self.osc_port}"
                     + (f", 虚拟摄像头={self.cam.vcam_device}"
                        if self.cam.vcam_device else ""))

    def _run_smoother(self):
        loop = asyncio.new_event_loop()
        self.smoother_loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.osc.start())
            loop.run_until_complete(self.smoother.run())
        except Exception as e:
            logging.debug(f"OSC 线程结束: {e}")
        finally:
            try:
                self.osc.close()
            except Exception:
                pass
            loop.close()

    def stop_capture(self):
        if self.cam is not None:
            self.cam.stop()
            self.cam = None
        if self.smoother_loop is not None:
            try:
                self.smoother_loop.call_soon_threadsafe(
                    lambda: [t.cancel() for t in asyncio.all_tasks(self.smoother_loop)])
            except Exception:
                pass
        self.smoother = None
        self.osc = None
        self.running = False

    # ---------- UI 回调 ----------
    def cam_choices(self, rescan=False):
        """列出可用摄像头，返回 (choices, default)。

        结果会缓存 —— 枚举要逐个打开设备并读帧（实测 10~17 秒），绝不能
        在界面构建时同步调用，否则程序看起来像卡死。改为后台线程枚举 +
        前端 on_load_cameras 填充（见 _enum_worker）。
        """
        if self._cam_cache is None or rescan:
            self._cam_cache = enumerate_cameras(max_index=self.max_index,
                                                yunet_path=self.yunet_path)
        cams = self._cam_cache
        if not cams:
            return [], None
        choices = []
        default = None
        for c in cams:
            label = (f"{camera_label(c['index'], c.get('name', ''))}"
                     f"  {c['size'][0]}x{c['size'][1]}")
            if c.get("has_face"):
                label += "  ★有人脸"
                if default is None:
                    default = c["index"]
            choices.append((label, c["index"]))
        if default is None:
            default = cams[0]["index"]
        # 用户此前选过、且这颗摄像头仍可用 -> 优先，别把他选好的覆盖掉
        if self._preferred_camera is not None:
            if any(c["index"] == self._preferred_camera for c in cams):
                default = self._preferred_camera
        return choices, default

    def _preferred_default(self):
        """当前应选中的摄像头：用户选过的 > 配置文件里的 > 枚举默认"""
        if self._preferred_camera is None:
            self._preferred_camera = self.load_saved().get("camera")
        return self._preferred_camera

    def start_enumeration(self):
        """后台线程枚举摄像头（界面不阻塞）。

        为什么不放在 build() 里: 枚举要 10~17 秒，同步做会让浏览器一直
        转圈、用户以为程序坏了。前端 on_load_cameras 会在枚举完成后填充。
        """
        if self._enum_thread is not None and self._enum_thread.is_alive():
            return
        self._enum_thread = threading.Thread(target=self._enum_worker,
                                             name="enum", daemon=True)
        self._enum_thread.start()

    def _enum_worker(self):
        try:
            self.cam_choices(rescan=True)
            logging.info("后台枚举完成，等待前端拉取")
        except Exception as e:
            logging.error(f"后台枚举摄像头失败: {type(e).__name__}: {e}")
        finally:
            self._enum_ready.set()

    def on_refresh(self):
        """刷新摄像头列表（用户主动点击时才重新枚举）"""
        import gradio as gr
        try:
            choices, default = self.cam_choices(rescan=True)
            logging.info(f"刷新摄像头列表: {[c[1] for c in choices]}")
            if not choices:
                return gr.update(choices=[], value=None)
            return gr.update(choices=choices, value=default)
        except Exception as e:
            logging.error(f"刷新摄像头失败: {type(e).__name__}: {e}")
            return gr.update()

    def on_start(self, cam_index, osc_port, osc_addr, gate, smooth,
                 cb_enable=False, cb_template="当前心率：{hr}",
                 cb_interval=5.0, cb_sound=False, vcam_enable=False):
        global MIN_AC_PEAK, SMOOTHING_FACTOR
        global CHATBOX_ENABLE, CHATBOX_TEMPLATE, CHATBOX_INTERVAL, CHATBOX_SOUND
        idx = coerce_camera_index(cam_index)
        if idx is None:
            return "请先选择一个摄像头", None, ""
        try:
            self.osc_port = int(osc_port)
            self.osc_address = osc_addr or OSC_ADDRESS
            MIN_AC_PEAK = float(gate)
            SMOOTHING_FACTOR = float(smooth)
            CHATBOX_ENABLE = bool(cb_enable)
            CHATBOX_TEMPLATE = cb_template or "当前心率：{hr}"
            CHATBOX_INTERVAL = max(1.0, float(cb_interval))
            CHATBOX_SOUND = bool(cb_sound)
            CHATBOX_TEMPLATE.format(hr=80)
        except KeyError as e:
            return (f"Chatbox 模板里的占位符不对: {e}。"
                    f"只能使用 {{hr}}，例如「当前心率：{{hr}}」"), None, ""
        except Exception as e:
            return f"参数错误: {e}", None, ""
        if self.cam is not None:
            self.stop_capture()
        self.start_capture(idx, want_vcam=bool(vcam_enable))
        parts = [f"已开始: 摄像头 #{idx}  ->  OSC {self.osc_address} "
                 f"@ {self.osc_ip}:{self.osc_port}"]
        parts.append(f"Chatbox 每 {CHATBOX_INTERVAL:.0f} 秒发一次"
                     if CHATBOX_ENABLE else "Chatbox 关闭")
        if vcam_enable:
            vin = self.cam.vcam_device if self.cam else None
            if vin:
                parts.append(f"<b>虚拟摄像头已输出到「{vin}」</b>，"
                             f"面补请选这个设备")
            else:
                parts.append("<b>虚拟摄像头启动失败</b>（见下方提示）")
        return "，".join(parts), None, ""

    def on_stop(self):
        self.stop_capture()
        return "已停止采集（OSC 不再发送）"

    def on_pick_camera(self, cam_index):
        """用户在下拉框里选了摄像头。

        必须记住这个选择 —— 否则任何一次刷新/重载都会用 default 覆盖掉它，
        表现就是"点选了反而没了"。
        """
        idx = coerce_camera_index(cam_index)
        if idx is None:
            return "未选择摄像头"
        self._user_picked = idx
        self._preferred_camera = idx
        if not self.running or self.cam is None:
            return f"已选择摄像头 #{idx}　→　点「开始」生效"
        self.cam.set_camera(idx)
        return f"正在切换到摄像头 #{idx} ..."

    def on_switch_camera(self, cam_index):
        """保留旧接口名（内部与 on_pick_camera 一致）"""
        return self.on_pick_camera(cam_index)

    def on_test_osc(self, value, osc_port, osc_addr):
        addr = osc_addr or OSC_ADDRESS
        ok, msg = send_osc_once(self.osc_ip, int(osc_port), addr, float(value))
        return msg

    def on_test_chatbox(self, template, osc_port):
        """发一条示例 Chatbox 消息，方便先确认 VRChat 那边能收到"""
        try:
            text = (template or "当前心率：{hr}").format(hr=88)
        except KeyError as e:
            return (f"模板占位符有误: {e}。只能使用 {{hr}}，"
                    f"例如「当前心率：{{hr}}」")
        except Exception as e:
            return f"模板错误: {e}"
        ok, msg = send_chatbox(self.osc_ip, int(osc_port), text, True, False)
        if ok:
            return f"{msg}（在 VRChat 聊天框里应该能看到）"
        return msg

    def on_save(self, cam_index, osc_port, osc_addr, gate, smooth,
                cb_enable=None, cb_template=None, cb_interval=None,
                cb_sound=None):
        cfg = {
            "camera": coerce_camera_index(cam_index),
            "osc_port": int(osc_port),
            "osc_address": osc_addr,
            "min_ac_peak": float(gate),
            "smoothing": float(smooth),
            "chatbox_enable": bool(cb_enable),
            "chatbox_template": cb_template or "当前心率：{hr}",
            "chatbox_interval": float(cb_interval or 5),
            "chatbox_sound": bool(cb_sound),
        }
        p = config_path()
        if not p:
            return "无法写入配置目录"
        try:
            import json
            with open(p, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            return f"已保存到 {p}（下次启动自动载入）"
        except Exception as e:
            return f"保存失败: {e}"

    def load_saved(self):
        p = config_path()
        if not p or not os.path.isfile(p):
            return {}
        try:
            import json
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    # ---------- 流式预览 ----------
    def one_frame(self, with_image=True):
        """取当前指标（有界函数，由前端 every= 定时轮询）。

        注意: 这里刻意不用"无限生成器"。Gradio 会把生成器一直占在队列里
        （表现为界面右下角 queue 卡住、按钮点不动），必须用有界函数配合
        event 的 every 参数来轮询。

        with_image=False 时只回文字，不传画面 —— 图像要 base64 编码再传输，
        若和文字一样高频会导致请求堆积、整个界面卡死。所以分成两个轮询：
        文字快、图像慢。

        重入保护: every 循环不会等上一次返回。若单次处理偏慢（例如编码变慢），
        本轮的请求会不断堆积，最后把队列堵死、界面完全不再刷新。
        这里上一轮未结束就直接返回 gr.skip()，让前端保持原值。
        """
        import gradio as gr
        if not self._poll_busy.acquire(blocking=False):
            self._poll_skipped += 1
            return gr.skip() if with_image else (gr.skip(),) * 4
        try:
            return self._one_frame_impl(with_image)
        except Exception as e:
            # 轮询里抛异常会让这一路 UI 更新永久中断（界面看起来就是冻住），
            # 所以这里必须兜住，并退化成"保持原值"继续下一轮。
            logging.error(f"刷新界面失败（已忽略）: {type(e).__name__}: {e}")
            return gr.skip() if with_image else (gr.skip(),) * 4
        finally:
            self._poll_busy.release()

    def _one_frame_impl(self, with_image=True):
        # 取一次局部引用：用户点「停止」会把 self.cam 置 None，
        # 若先判断非空再直接用，中间这一小段就会 AttributeError。
        cam = self.cam
        if not self.running or cam is None:
            if with_image:
                return None
            vc = f"　虚拟摄像头: {self.vcam_error}" if self.vcam_error else ""
            return "--", "未开始采集 —— 请选择摄像头后点「开始」" + vc, None, ""
        st = cam.ui_stats()
        bpm = st["bpm"]
        ac = st["ac"]
        if st["error"]:
            status = f"⚠ {st['error']}"
        elif not st["cam_opened"]:
            status = "正在打开摄像头……或设备被其他程序占用"
        elif st["frames_read"] == 0:
            status = "摄像头已打开但读不到帧 —— 可能被其他程序独占"
        elif bpm is None:
            status = "正在测量……请正对摄像头并保持不动（约需 10~15 秒）"
        else:
            tone = ("信号良好" if ac >= GOOD_AC_PEAK else
                    ("信号可用" if ac >= MIN_AC_PEAK else "信号偏弱（读数仅供参考）"))
            status = (f"{tone}    信号强度 {ac:.2f}    "
                      f"{'两法一致' if st['agree'] else '两法不一致'}    "
                      f"{st['fps']:.0f} fps")
        # 虚拟摄像头状态也显示出来，方便确认面补该选哪个设备
        vdev = cam.vcam_device
        vdrop = cam._vcam_dropped
        if vdev:
            status += f"　|　虚拟摄像头: {vdev}" + (f"（丢帧 {vdrop}）" if vdrop else "")
        elif self.vcam_error:
            status += f"　|　虚拟摄像头失败: {self.vcam_error[:60]}"
        bpm_txt = f"{bpm:.0f}" if (bpm is not None and ac >= MIN_AC_PEAK) else "--"
        if with_image:
            return cam.jpeg_frame()
        # 波形图改为"画好后当图片发"。原因: gr.LinePlot(Vega-Lite) 在这个
        # 打包环境里始终渲染不出来（试过声明 x/y 列名、改 ASCII 列名、
        # 传 DataFrame 都不行），而 gr.Image 一直工作正常，所以用 OpenCV
        # 直接画一张波形图，稳且可控。
        #
        # 必须缓存: 文字路每 0.25s 轮询一次，图像路每 0.5s 还要拿一次，
        # 每次都重画会让单次调用从 1ms 涨到近百毫秒（实测平均 91ms、
        # 峰值 2.2s），界面就会明显卡。波形本身变化很慢，2Hz 足够。
        wave_img = self._wave_cached(cam, st.get("trace") or [])
        info = (f"摄像头 #{st['camera_index']}　{st['backend'] or ''}　"
                f"已读 {st['frames_read']} 帧 / 失败 {st['frames_failed']}　"
                f"人脸{'可见' if st['face_seen'] else '未检测到'}")
        return bpm_txt, status, wave_img, info

    # 波形图缓存（见 _one_frame_impl 里的说明）
    _wave_lock = None
    _wave_cache = None
    _wave_cache_t = 0.0
    _wave_last_fp = None
    WAVE_MIN_INTERVAL = 0.5

    @staticmethod
    def _render_wave_image(trace, w=520, h=170):
        """把 BVP 波形画成一张图（深色背景 + 网格 + 曲线 + 说明文字）"""
        img = np.full((h, w, 3), 22, dtype=np.uint8)
        # 网格
        for x in range(0, w, 40):
            cv2.line(img, (x, 0), (x, h), (45, 45, 45), 1)
        for y in range(0, h, 30):
            cv2.line(img, (0, y), (w, y), (45, 45, 45), 1)
        if len(trace) < 8:
            cv2.putText(img, "waiting for signal...", (16, h // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (120, 120, 120), 1)
            return img
        tr = np.asarray(trace, dtype=np.float32)
        # 必须清洗 NaN/Inf: 否则后面的 int() 会抛 "cannot convert float NaN
        # to integer"，把这一轮 UI 刷新搞崩（表现为画面冻住）
        tr = tr[np.isfinite(tr)]
        if len(tr) < 8:
            cv2.putText(img, "waiting for signal...", (16, h // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (120, 120, 120), 1)
            return img
        mid = float(np.median(tr))
        amp = float(np.percentile(np.abs(tr - mid), 95))
        if not math.isfinite(amp) or amp <= 1e-6:
            amp = 1.0
        n = len(tr)
        pts = []
        for i, v in enumerate(tr):
            x = int(i * (w - 1) / max(n - 1, 1))
            y = int(h / 2 - (v - mid) / amp * (h * 0.38))
            pts.append((x, max(2, min(h - 3, y))))
        cv2.polylines(img, [np.array(pts, dtype=np.int32)], False,
                      (0, 255, 128), 2)
        cv2.putText(img, "BVP  (last %.0fs)" % (n / 30.0), (12, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (170, 170, 170), 1)
        return img

    @classmethod
    def _wave_cached(cls, cam, trace):
        """带缓存的波形图。

        只在超过 WAVE_MIN_INTERVAL 秒、且数据指纹变化时才重画。
        这样文字路的每秒 4 次轮询里最多只有 2 次真正画图，单次调用
        从近百毫秒降到 1ms 量级。
        """
        if cls._wave_lock is None:
            cls._wave_lock = threading.Lock()
        # 指纹取末尾若干点 + 长度，够判断"波形有没有变"，且极便宜
        if len(trace) >= 8:
            fp = (len(trace), round(trace[-1], 4), round(trace[len(trace) // 2], 4))
        else:
            fp = (len(trace),)
        now = time.time()
        with cls._wave_lock:
            fresh = (cls._wave_cache is not None
                     and (now - cls._wave_cache_t) < cls.WAVE_MIN_INTERVAL)
            if fresh and fp == cls._wave_last_fp:
                return cls._wave_cache
            try:
                img = cls._render_wave_image(trace)
            except Exception as e:
                logging.debug(f"波形绘制失败: {e}")
                return cls._wave_cache
            cls._wave_cache = img
            cls._wave_cache_t = now
            cls._wave_last_fp = fp
            return img

    def on_load_cameras(self):
        """填充摄像头下拉框（前端加载完成后调用）。

        枚举是后台线程做的，这里只等结果（最多 ENUM_TOTAL_BUDGET_SEC+10 秒），
        所以界面能立刻打开，不用干等十几秒。

        关键: 若用户已经选过某颗摄像头，就**不要**改他的 value —— 之前无条件
        写 value=default，把用户的选择覆盖掉（表现为"点了反而没了"）。

        但 choices **必须**每次都下发: 枚举挪到后台后，服务端配置里这份
        Dropdown 的初始 choices 是空的，新打开的页面只能靠这里拿到候选列表。
        一开始我只下发 gr.update()（什么都不改），结果新页面永远拿不到
        choices、下拉框一直是空的 —— 那是另一个 bug。
        正确的组合是: 下发 choices，但已选过时不下发 value。
        """
        import gradio as gr
        # 等后台枚举完成（界面早已可见，这里只是补数据）
        self._enum_ready.wait(timeout=ENUM_TOTAL_BUDGET_SEC + 10)
        try:
            choices, default = self.cam_choices()
            logging.info(f"摄像头列表: {[c[1] for c in choices]}  默认 #{default}")
            if not choices:
                return gr.update(choices=[], value=None,
                                 info="没找到可用摄像头，点「刷新列表」重试")
            hint = "★有人脸 的是能拍到你的那颗"
            # 用户已选过、且那颗仍在列表里 -> 只下发 choices，保留他的 value
            # （若这里也下发 value，会在页面重载时覆盖用户的选择）
            if self._user_picked is not None and any(
                    c[1] == self._user_picked for c in choices):
                logging.info(f"下发 choices，保留用户已选 #{self._user_picked}")
                return gr.update(choices=choices, info=hint)
            return gr.update(choices=choices, value=default, info=hint)
        except Exception as e:
            logging.error(f"枚举摄像头失败: {type(e).__name__}: {e}")
            return gr.update(choices=[], value=None,
                             info=f"枚举失败: {type(e).__name__}")

    # ---------- 构建界面 ----------
    def wait_for_enumeration(self, timeout=None):
        """等后台枚举完成（最多 timeout 秒）"""
        if timeout is None:
            timeout = ENUM_TOTAL_BUDGET_SEC + 10
        return self._enum_ready.wait(timeout=timeout)

    def build(self):
        import gradio as gr
        saved = self.load_saved()
        self._preferred_camera = saved.get("camera")
        # 界面构建前等枚举完成 —— 这样 choices 能作为组件的初始配置一起下发。
        #
        # 为什么不用"先建空界面、再由 demo.load 填充": Gradio 3.50 的前端在
        # 组件初次渲染（choices 为空）之后再收到 choices 更新时会把下拉框
        # 渲染成空的 —— 实测 API 返回的 JSON 完全正确
        # （choices=[[label,0],[label,1]], value=0），但浏览器里就是空白的，
        # info 文字却能更新。与其和框架这个行为纠缠，不如让它一开始就有值。
        #
        # 界面构建时 main_web 已经先给浏览器看过一个"正在检测摄像头"的过渡页，
        # 所以这里等十几秒不会表现为"程序卡住"。
        self.start_enumeration()
        ok = self.wait_for_enumeration()
        if not ok:
            logging.warning("等待摄像头枚举超时，界面可能缺少摄像头列表")
        choices, default = self.cam_choices()
        logging.info(f"建界面时摄像头列表: {[c[1] for c in choices]}  默认 #{default}")
        if default is None and choices:
            default = choices[0][1]

        with gr.Blocks(title="摄像头心率 → VRChat", css=WEB_CSS) as demo:
            gr.Markdown(
                "## 摄像头心率 → VRChat OSC\n"
                "用摄像头无接触测量心率并通过 OSC 发送给 VRChat。"
                "**先在下面选择摄像头** —— 只要摄像头被别人占用（相机 App / 浏览器 / OBS）"
                "就读不到画面。"
            )
            with gr.Row():
                with gr.Column(scale=3):
                    # 尺寸按摄像头帧的 4:3 比例给（640x480 -> 560x420），
                    # 之前固定 height=360 与画面比例不符，会出现大片黑边
                    preview = gr.Image(label="实时画面", width=560, height=420,
                                       interactive=False, show_label=False)
                    info_line = gr.Markdown("")
                with gr.Column(scale=2):
                    with gr.Row():
                        bpm_txt = gr.Textbox(label="当前心率 (BPM)", value="--",
                                             scale=2, elem_id="bpm-box")
                    status = gr.Markdown("未开始采集")
                    # 波形用图片显示（见 _render_wave_image 的说明），
                    # 不用 gr.LinePlot —— 它在打包环境里渲染不出来
                    wave = gr.Image(label="BVP 波形（心跳波形）", height=170,
                                    interactive=False, show_label=True,
                                    show_download_button=False)
                    with gr.Row():
                        # type="value" 必须显式指定: 否则 Dropdown 会把选中的
                        # label 文本（如 "#0 640x480 ★有人脸"）传给回调，
                        # 而不是我们放的索引值，导致 int() 解析失败。
                        # choices 在 build() 里已等枚举完成，所以这里是实值。
                        cam_dd = gr.Dropdown(label="摄像头", choices=choices,
                                             value=default, type="value",
                                             scale=3,
                                             info="★有人脸 的是能拍到你的那颗")
                        refresh_btn = gr.Button("刷新列表", scale=1)

            with gr.Accordion("参数设置（改完点开始生效）", open=False):
                with gr.Row():
                    osc_addr = gr.Textbox(label="OSC 地址", value=self.osc_address,
                                          scale=3)
                    osc_port = gr.Number(label="OSC 端口", value=self.osc_port,
                                         precision=0, scale=1)
                with gr.Row():
                    gate = gr.Slider(0.10, 0.60,
                                     value=float(saved.get("min_ac_peak", MIN_AC_PEAK)),
                                     step=0.01, label="信号门限",
                                     info="低于此值不输出心率（越大越严格）")
                    smooth = gr.Slider(0.02, 0.5,
                                       value=float(saved.get("smoothing", SMOOTHING_FACTOR)),
                                       step=0.01, label="平滑系数",
                                       info="越小越平滑、越大越跟手")
                test_val = gr.Slider(0, 150, value=75, step=1,
                                     label="测试值（点下面按钮发一条 OSC）")

            with gr.Accordion("聊天框播报（把心率显示在 VRChat 聊天框）", open=False):
                cb_enable = gr.Checkbox(
                    label="启用心率播报到聊天框",
                    value=bool(saved.get("chatbox_enable", False)))
                with gr.Row():
                    cb_template = gr.Textbox(
                        label="显示内容",
                        value=saved.get("chatbox_template", "当前心率：{hr}"),
                        scale=3, info="用 {hr} 代表心率数值")
                    cb_sound = gr.Checkbox(
                        label="带提示音",
                        value=bool(saved.get("chatbox_sound", False)), scale=1)
                cb_interval = gr.Slider(
                    1, 60, value=float(saved.get("chatbox_interval", 5)),
                    step=1, label="发送间隔（秒）",
                    info="VRChat 对聊天框有频率限制，设太短消息会被丢弃")
                cb_test_btn = gr.Button("发送一条测试聊天框消息")
                gr.Markdown(
                    "说明: 需在 VRChat 里开启 **OSC**。"
                    "只在心率信号可信时才播报，信号丢失会自动停发。")

            with gr.Accordion("虚拟摄像头（让 VRChat 面补共用这颗摄像头）",
                              open=False):
                vcam_enable = gr.Checkbox(
                    label="启用虚拟摄像头输出", value=False,
                    info="把画面转发成虚拟摄像头，面补改读它即可，两边不再抢设备")
                gr.Markdown(
                    "**为什么需要这个**：USB 摄像头通常只允许一个程序打开，"
                    "所以面补占住后本程序就读不到画面。开启后本程序独占物理摄像头，"
                    "同时把画面输出到一个虚拟摄像头，让面补去读虚拟摄像头。\n\n"
                    "**用法**：\n"
                    "1. 勾选本项并点「开始」，状态栏会显示虚拟摄像头的设备名\n"
                    "2. 到面补软件里把摄像头改成该设备（如 `OBS Virtual Camera`）\n"
                    "3. 前提：需要装有 OBS（提供虚拟摄像头驱动），"
                    "**但 OBS 本身不用运行**\n\n"
                    "注意：程序独占物理摄像头期间，其他程序无法再打开它 —— "
                    "这正是要解决的问题，所以面补必须改用虚拟摄像头。\n\n"
                    "⚠️ **隐私提醒**：启用后画面会经由系统虚拟摄像头驱动暴露给"
                    "**本机其他程序**。这正是该功能的目的，但请确认你了解这一点。")

            with gr.Row():
                start_btn = gr.Button("▶ 开始", variant="primary", scale=1)
                stop_btn = gr.Button("■ 停止", scale=1)
                test_btn = gr.Button("发送测试 OSC", scale=1)
                save_btn = gr.Button("保存设置", scale=1)

            msg = gr.Markdown("")

            # 事件绑定
            refresh_btn.click(self.on_refresh, outputs=[cam_dd])
            # 用户主动改选 -> 记住它，之后任何刷新/重载都不再动 value
            # （之前无条件写 value=default，导致"点了反而没了"）
            cam_dd.change(self.on_pick_camera, inputs=[cam_dd], outputs=[msg])
            start_btn.click(self.on_start,
                            inputs=[cam_dd, osc_port, osc_addr, gate, smooth,
                                    cb_enable, cb_template, cb_interval, cb_sound,
                                    vcam_enable],
                            outputs=[msg, preview, info_line])
            stop_btn.click(self.on_stop, outputs=[msg])
            test_btn.click(self.on_test_osc,
                           inputs=[test_val, osc_port, osc_addr], outputs=[msg])
            cb_test_btn.click(self.on_test_chatbox,
                              inputs=[cb_template, osc_port], outputs=[msg])
            save_btn.click(self.on_save,
                           inputs=[cam_dd, osc_port, osc_addr, gate, smooth,
                                   cb_enable, cb_template, cb_interval, cb_sound],
                           outputs=[msg])
            # 启动时加载摄像头列表（有界函数，不会占住队列）
            demo.load(self.on_load_cameras, inputs=None, outputs=[cam_dd])
            # 实时刷新拆成两个轮询，避免请求堆积把队列堵死:
            #   画面要 base64 编码再传输，代价高 -> 2Hz
            #   文字指标很便宜 -> 5Hz，保证读数看起来跟手
            # 必须走队列（queue 默认开启）—— Gradio 3.50 的 every 循环依赖
            # 队列把更新推给前端，设 queue=False 会导致界面完全不再刷新。
            # 请求堆积由 one_frame 里的重入保护来兜住。
            # 两者输出字段不重叠，避免互相覆盖。
            demo.load(lambda: self.one_frame(True), inputs=None, outputs=[preview],
                      every=0.5, show_progress="hidden")
            demo.load(lambda: self.one_frame(False), inputs=None,
                      outputs=[bpm_txt, status, wave, info_line],
                      every=0.25, show_progress="hidden")
        return demo

    def launch(self, open_browser=True):
        self.load_models()
        # 枚举摄像头要十几秒（build() 必须等它完成，理由见 build()）。
        # 这里先把枚举挂到后台跑，并给终端打一行提示 —— 否则用户会以为程序
        # 卡住了。浏览器在界面构建完成后再打开，打开即可用。
        print()
        print("  正在检测摄像头……（约 5~20 秒，取决于设备响应速度）")
        self.start_enumeration()
        demo = self.build()
        url = f"http://{self.host}:{self.port}"
        print("\n" + "=" * 68)
        print(f"  摄像头心率 → VRChat    v{__version__}  （网页界面）")
        print("=" * 68)
        print(f"  请在浏览器打开:  {url}")
        print()
        print("  使用步骤:")
        print("    1) 下拉框选摄像头（列表里★标出的是能看见你的那个）")
        print("    2) 点「开始」，画面出现后正对摄像头保持不动")
        print("    3) 约 10~15 秒后显示心率，自动通过 OSC 发给 VRChat")
        print("    4) 换摄像头/改参数都在网页里操作，无需重启")
        print()
        print("  关闭本窗口即可退出程序")
        if LOG_FILE:
            print(f"  日志文件: {LOG_FILE}")
            print("  （出问题时请把这个文件的内容附到 Issue 里）")
        print("=" * 68 + "\n")
        demo.queue().launch(server_name=self.host, server_port=self.port,
                            inbrowser=open_browser, show_api=False)


def load_facephys(path):
    """加载 FacePhys 模型。

    实际实现放在 facephys.py（自包含的精简封装，见该文件说明为什么不让使用者
    再去 clone 上游仓库）。这里做一次转发，并在 facephys.py 缺失时退回内置的
    最小实现，保证任何情况下都能跑。
    """
    try:
        from facephys import load_facephys as _load
        return _load(path)
    except ImportError:
        logging.debug("facephys.py 不可用，使用内置加载器")

    import onnxruntime as ort
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    so.log_severity_level = 3
    sess = ort.InferenceSession(path, sess_options=so,
                                providers=["CPUExecutionProvider"])

    def run(img, state, dt=1 / 30):
        res = sess.run(None, {"input": img[None, None],
                              "dt": np.array([dt], dtype="float32"), **state})
        return res[0][0, 0], dict(zip(state, res[1:]))

    run.init_state = {i.name: np.zeros(i.shape, dtype="float32")
                      for i in sess.get_inputs()[2:]}
    return run


def resource_paths():
    here = os.path.dirname(os.path.abspath(__file__))
    if getattr(sys, "frozen", False):
        here = getattr(sys, "_MEIPASS", here)
    return here


_SAFE_PATH_CACHE = {}


def opencv_safe_path(path):
    """给 OpenCV 一个纯 ASCII 的模型路径。

    实测坑: OpenCV(5.0) 的 FaceDetectorYN 打不开含非 ASCII 字符的路径 ——
    报 "Can't read ONNX file"，但同一个文件复制到英文路径就能读。
    原因是其内部用窄字符 API 打开文件。用户把程序放在中文目录（如
    "桌面\\软件收纳\\...\\摄像头心率VRChat"）时必然触发。

    所以这里检测到非 ASCII 路径就复制一份到系统临时目录（纯 ASCII）再加载。
    模型只有 200 多 KB，复制耗时实测 3ms，可忽略。结果会缓存，
    避免人脸枚举和检测器初始化各复制一遍。
    """
    try:
        if not path or path.isascii():
            return path
        key = (path, os.path.getsize(path))
        if key in _SAFE_PATH_CACHE:
            return _SAFE_PATH_CACHE[key]
        import tempfile
        d = os.path.join(tempfile.gettempdir(), "vrc_heartrate_models")
        os.makedirs(d, exist_ok=True)
        safe = os.path.join(d, os.path.basename(path))
        # 源文件更新过才重新复制
        if (not os.path.isfile(safe)
                or os.path.getsize(safe) != os.path.getsize(path)):
            import shutil
            shutil.copyfile(path, safe)
        if safe.isascii():
            logging.info(f"路径含非 ASCII 字符，已复制模型到: {safe}")
            _SAFE_PATH_CACHE[key] = safe
            return safe
        return path
    except Exception as e:
        logging.warning(f"复制模型到 ASCII 路径失败: {e}，将直接尝试原路径")
        return path


def find_free_port(host, start_port, tries=20):
    """找一个可用端口。

    直接写死 7860 时，如果上次的程序没退干净（或别的程序占了），
    Gradio 会抛 OSError 直接崩掉，体验很差。这里主动探测并顺延。
    """
    import socket
    for p in range(start_port, start_port + tries):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, p))
            s.close()
            return p
        except OSError:
            s.close()
            continue
    return None


def find_model(name):
    """按优先级查找模型文件，返回第一个存在的路径（找不到返回首选路径）。

    顺序: models/  ->  FacePhys-Release/weights/（兼容旧布局）
    项目已改为自包含，模型统一放 models/，由 tools/download_models.py 获取。
    """
    base = resource_paths()
    cands = []
    if name == "facephys":
        cands = [os.path.join(base, "models", "FacePhys.onnx"),
                 os.path.join(base, "FacePhys-Release", "weights", "model.onnx")]
    else:
        cands = [os.path.join(base, "models",
                             "face_detection_yunet_2023mar.onnx")]
    for p in cands:
        if os.path.isfile(p):
            return p
    return cands[0]


def main_web(args):
    """启动网页界面（本程序唯一的运行模式）"""
    model_path = find_model("facephys")
    yunet_path = find_model("yunet")
    missing = [(p, what) for p, what in
               ((model_path, "FacePhys 模型"),
                (yunet_path, "YuNet 人脸检测模型"))
               if not os.path.isfile(p)]
    if missing:
        print("\n" + "=" * 68)
        print("  缺少模型文件，程序无法启动")
        print("=" * 68)
        for p, what in missing:
            print(f"  缺少{what}:")
            print(f"      {p}")
        print()
        print("  解决办法（任选其一）:")
        print("    1) 在项目目录执行:  python tools/download_models.py")
        print("    2) 手动下载后放到 models/ 目录:")
        print("       模型请从 GitHub Releases 或下列地址获取")
        print("       FacePhys: https://github.com/KegangWangCCNU/FacePhys-Release")
        print("       YuNet:    https://github.com/opencv/opencv_zoo"
              "/tree/main/models/face_detection_yunet")
        print()
        print("  如果你用的是打包好的 exe，说明 _internal 目录缺失或被破坏，")
        print("  请重新完整解压压缩包（exe 不能单独拷出来运行）。")
        print("=" * 68)
        logging.error(f"缺少模型文件: {[p for p, _ in missing]}")
        return 1

    try:
        import gradio  # noqa: F401
    except Exception as e:
        # 打包后缺文件时也走这里，所以要把真实原因打出来，否则无从排查
        import traceback
        logging.error(f"导入 gradio 失败: {type(e).__name__}: {e}")
        print(f"\n无法启动网页界面: {type(e).__name__}: {e}")
        if not getattr(sys, "frozen", False):
            print("请先安装依赖:  pip install gradio")
        else:
            traceback.print_exc()
        return 1

    logging.info(f"启动: Python {sys.version.split()[0]}"
                 f"{' (打包 exe)' if getattr(sys, 'frozen', False) else ''}")

    port = find_free_port(args.host, args.port)
    if port is None:
        logging.error(f"从 {args.port} 起找不到可用端口")
        print(f"\n端口 {args.port} 起连续 {20} 个都被占用了。")
        print("请用 --port 指定其他端口，例如: --port 7900")
        return 1
    if port != args.port:
        logging.warning(f"端口 {args.port} 被占用，改用 {port}")
        print(f"\n提示: 端口 {args.port} 已被占用（可能是上次的程序没退干净），"
              f"自动改用 {port}")

    ui = HeartRateWebUI(model_path, yunet_path, host=args.host, port=port,
                        prefer_size=parse_size(args.size),
                        max_index=args.max_index)
    try:
        ui.launch(open_browser=not args.no_browser)
    except KeyboardInterrupt:
        pass
    except OSError as e:
        logging.error(f"启动失败: {e}")
        print(f"\n启动失败: {e}")
        print("换一个端口试试:  --port 7900")
        return 1
    finally:
        ui.stop_capture()
    return 0


def parse_size(s):
    """解析 '1280x720' 形式的分辨率"""
    if not s:
        return None
    try:
        w, h = s.lower().replace(" ", "").split("x")
        return (int(w), int(h))
    except Exception:
        logging.warning(f"无法解析分辨率 {s!r}，将使用自动模式")
        return None


def parse_args():
    p = argparse.ArgumentParser(
        description="摄像头心率 → VRChat OSC（网页界面）",
        epilog="直接运行即可，会自动打开浏览器。",
    )
    p.add_argument("--host", default="127.0.0.1",
                   help="监听地址（默认仅本机可访问）")
    p.add_argument("--port", type=int, default=7860,
                   help="端口（默认 7860；被占用会自动顺延）")
    p.add_argument("--max-index", type=int, default=6,
                   help="枚举摄像头时扫描的最大索引（默认 6，越大越慢）")
    p.add_argument("--size", default=None,
                   help="首选分辨率，如 1280x720（默认自动尝试多个）")
    p.add_argument("--no-browser", action="store_true",
                   help="不自动打开浏览器")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        rc = main_web(args)
    except KeyboardInterrupt:
        rc = 0
    sys.exit(rc or 0)
