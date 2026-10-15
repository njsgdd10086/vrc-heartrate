# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 njsgdd10086
#
# 本项目采用 GPL-3.0-or-later。完整条款见仓库根目录的 LICENSE，版权与说明见 COPYRIGHT.md，
# 第三方组件（含 FacePhys 模型的隐私保护附加条款）见 THIRD_PARTY_LICENSES.md。

"""
BLE 蓝牙心率 → VRChat OSC 心率桥接脚本（异步高性能 + 优雅退出）
参考 osc_heartrate.py (Pulsoid 走云版) 改写：不走云端，直接通过蓝牙连接心率广播设备
依赖: bleak (pip install bleak)
无 python-osc 依赖，自行实现轻量 OSC 发送

适用设备: 手机"心率广播"类 App（如 xinlvguangbo-Iphone，广播标准 BLE 心率服务 0x180D）
说明: iOS 的 BLE 地址会轮换，所以默认按设备名关键字自动发现，不依赖写死的 MAC
"""

import argparse
import asyncio
import logging
import math
import signal
import struct
import sys
import time

from bleak import BleakClient, BleakScanner

# 中文日志在 Windows 下需要显式指定 UTF-8，否则控制台/重定向会乱码
# （打包成 exe 后没有 PYTHONIOENCODING 兜底，这段是必须的）
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# -------------------- 配置 --------------------
VRC_OSC_IP = "127.0.0.1"
VRC_OSC_PORT = 9000
OSC_ADDRESS = "/avatar/parameters/VRCOSC/Heartrate/Value"  # 可自行修改

# 蓝牙目标（iOS 地址会轮换，优先靠名字自动发现）
BLE_DEVICE_ADDRESS = ""  # 留空 = 自动扫描；也可填 MAC，如 "76:3D:8F:F4:41:C0"
BLE_DEVICE_NAME_FILTER = "xinlvguangbo"  # 设备名关键字（大小写不敏感）
SCAN_DURATION = 10  # 每次扫描时长（秒）
CONNECT_TIMEOUT = 20  # 连接超时（秒）
RECONNECT_DELAY = 5

# BLE 标准心率服务 / 心率测量特征
HR_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HR_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

# 平滑参数（与走云版一致）
SMOOTHING_FACTOR = 0.1
MAX_RATE = 0
SEND_INTERVAL = 1.0 / 10  # 10 Hz

# 超时波动动画
TIMEOUT_SECONDS = 15
ANIMATION_PERIOD = 10

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# 用于优雅关闭的事件
shutdown_event = asyncio.Event()

# -------------------- 轻量异步 OSC 发送器 --------------------
class AsyncOSCSender:
    """基于 asyncio UDP 的简易 OSC 发送器，仅支持发送 float"""

    def __init__(self, ip, port):
        self.ip = ip
        self.port = port
        self.transport = None

    async def start(self):
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(
            lambda: asyncio.DatagramProtocol(),
            remote_addr=(self.ip, self.port)
        )

    def send(self, address: str, value: float):
        if not self.transport:
            return
        # 构建 OSC 包
        addr_bytes = address.encode("utf-8") + b'\x00' * (4 - len(address) % 4)
        type_tag = b',f\x00\x00'
        value_bytes = struct.pack('>f', value)
        packet = addr_bytes + type_tag + value_bytes
        self.transport.sendto(packet)

    def close(self):
        if self.transport:
            self.transport.close()

# -------------------- 心率平滑与超时控制 --------------------
class HeartrateSmoother:
    def __init__(self, osc: AsyncOSCSender, osc_address: str):
        self.osc = osc
        self.osc_address = osc_address
        self.current_value = None
        self.target_value = None
        self.last_data_time = 0.0  # 与走云版一致：启动即触发超时动画，直到收到第一条心率数据
        self.timeout_triggered = False

    def update_target(self, hr: float):
        now = time.time()
        self.last_data_time = now
        self.timeout_triggered = False

        if self.current_value is None:
            self.current_value = 0.0
            self.target_value = float(hr)
            logging.info(f"收到原始心率: {hr} BPM，从 0 开始平滑过渡")
        else:
            self.target_value = float(hr)
            logging.info(f"更新目标心率: {hr} BPM")

    async def run(self):
        try:
            while not shutdown_event.is_set():
                now = time.time()

                # 检查超时
                if not self.timeout_triggered and (now - self.last_data_time) > TIMEOUT_SECONDS:
                    self.timeout_triggered = True
                    logging.warning("蓝牙心率数据超时，启动断连波动动画")

                if self.timeout_triggered:
                    value = (math.sin(now * 2 * math.pi / ANIMATION_PERIOD) + 1) * 50
                    if self.current_value is None or abs(value - self.current_value) > 0.1:
                        self.current_value = value
                        self.osc.send(self.osc_address, float(value))
                else:
                    current = self.current_value
                    target = self.target_value
                    if current is not None and target is not None:
                        new_value = current + SMOOTHING_FACTOR * (target - current)
                        if MAX_RATE > 0:
                            max_delta = MAX_RATE * SEND_INTERVAL
                            delta = new_value - current
                            if abs(delta) > max_delta:
                                new_value = current + math.copysign(max_delta, delta)
                        if abs(new_value - target) < 0.05:
                            new_value = target
                        if new_value != current:
                            self.current_value = new_value
                            self.osc.send(self.osc_address, float(new_value))

                await asyncio.sleep(SEND_INTERVAL)
        except asyncio.CancelledError:
            # 被取消时安静退出
            pass
        finally:
            logging.debug("心率平滑任务已停止")

# -------------------- BLE 心率数据解析 --------------------
def parse_hr_measurement(data: bytes):
    """解析标准 BLE 心率测量特征 (0x2A37) 的负载，返回心率 BPM（float）"""
    if not data:
        return None
    flags = data[0]
    if flags & 0x01:
        # 16 位心率
        if len(data) < 3:
            return None
        hr = struct.unpack_from("<H", data, 1)[0]
    else:
        # 8 位心率
        if len(data) < 2:
            return None
        hr = data[1]
    return float(hr)

# -------------------- BLE 扫描与目标发现 --------------------
async def scan_once(scan_duration: float):
    """执行一次扫描，返回 {地址: (BLEDevice, AdvertisementData)}

    注意：iOS 的"心率广播"App 只有在真正开始广播时，才会在广播包里带上设备名和
    0x180D 心率服务；App 停止广播后，系统里仍会残留已配对的设备节点（能扫到地址，
    但既没有名字也没有心率服务），那种状态下连上去是找不到 0x2A37 的。
    """
    scanner = BleakScanner(scanning_mode="active")
    await scanner.start()
    await asyncio.sleep(scan_duration)
    await scanner.stop()
    return scanner.discovered_devices_and_advertisement_data

async def list_devices(scan_duration: float):
    """--scan 模式：列出附近所有 BLE 设备并退出"""
    print(f"正在扫描附近 BLE 设备 {scan_duration} 秒...")
    try:
        items = await scan_once(scan_duration)
    except Exception as e:
        print(f"扫描失败（请确认电脑蓝牙已开启）: {e}")
        return
    if not items:
        print("未发现任何 BLE 设备")
        return

    rows = []
    for addr, (dev, adv) in items.items():
        name = adv.local_name or dev.name or "(无名称)"
        uuids = [u.lower() for u in adv.service_uuids]
        is_hr = HR_SERVICE_UUID in uuids
        rows.append((name, addr, adv.rssi, is_hr))
    rows.sort(key=lambda r: (not r[3], (r[0] or "").lower()))

    print(f"发现 {len(rows)} 个设备:")
    for name, addr, rssi, is_hr in rows:
        mark = "  <== 心率服务设备" if is_hr else ""
        print(f"  name={name!r} addr={addr} rssi={rssi}{mark}")

def _device_score(dev, adv, address: str, name_filter: str):
    """给设备打分：地址精确匹配 > 名字+心率服务 > 心率服务 > 名字匹配"""
    name = (adv.local_name or dev.name or "").lower()
    uuids = [u.lower() for u in adv.service_uuids]
    has_svc = HR_SERVICE_UUID in uuids
    has_name = bool(name_filter) and name_filter.lower() in name
    addr_match = bool(address) and adv.address.lower() == address.lower()

    if addr_match:
        return 4
    if has_name and has_svc:
        return 3
    if has_svc:
        return 2
    if has_name:
        return 1
    return 0

async def find_target_device(address: str, name_filter: str, scan_duration: float):
    """扫描并按优先级寻找目标设备。

    返回 (地址, 设备名, 是否在广播心率服务) 或 None。
    第三个值用来区分"设备正在广播心率"和"只是扫到这个地址的残留节点"。
    """
    try:
        items = await scan_once(scan_duration)
    except Exception as e:
        logging.error(f"扫描失败（请确认电脑蓝牙已开启）: {e}")
        return None

    if not items:
        logging.warning("扫描未发现任何 BLE 设备")
        return None

    best_addr = None
    best_dev = None
    best_adv = None
    best_score = 0
    best_rssi = -999
    for addr, (dev, adv) in items.items():
        score = _device_score(dev, adv, address, name_filter)
        rssi = adv.rssi if adv.rssi is not None else -999
        if score > best_score or (score == best_score and score > 0 and rssi > best_rssi):
            best_score = score
            best_addr = addr
            best_dev = dev
            best_adv = adv
            best_rssi = rssi

    if best_score == 0:
        named = [
            f"{adv.local_name or dev.name or '(无名称)'}@{addr}"
            for addr, (dev, adv) in items.items()
        ]
        logging.warning(
            f"未找到匹配的心率设备（关键字={name_filter!r}, 地址={address or '自动'}），"
            f"附近 {len(named)} 个设备: " + ", ".join(named)
        )
        logging.warning(
            "提示: 手机上的「心率广播」类 App 必须正在广播且保持在前台，"
            "广播包里才会带上设备名和 0x180D 心率服务。"
        )
        return None

    dev_name = best_adv.local_name or best_dev.name or "(无名称)"
    hr_in_adv = HR_SERVICE_UUID in [u.lower() for u in best_adv.service_uuids]
    return best_addr, dev_name, hr_in_adv

# -------------------- 异步 BLE 客户端 --------------------
def describe_exception(e: BaseException) -> str:
    """把异常描述清楚。

    Windows 的 WinRT 底层异常（尤其 asyncio.TimeoutError）str() 往往是空字符串，
    直接 f"连接失败: {e}" 会打出"连接失败:"这种没有任何信息的日志，所以这里
    至少要把异常类型名和链条里的内层异常带出来。
    """
    parts = []
    seen = set()
    cur = e
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        text = str(cur).strip()
        name = type(cur).__name__
        parts.append(f"{name}({text})" if text else name)
        cur = cur.__cause__ or cur.__context__
    return " <- ".join(parts)

async def ble_heartrate_client(smoother: HeartrateSmoother, address: str, name_filter: str):
    """扫描→连接→订阅心率通知；断线自动重连"""
    while not shutdown_event.is_set():
        try:
            target = await find_target_device(address, name_filter, SCAN_DURATION)
            if target is None:
                if not shutdown_event.is_set():
                    logging.info(f"{RECONNECT_DELAY} 秒后重新扫描...")
                    await asyncio.sleep(RECONNECT_DELAY)
                continue

            addr, dev_name, hr_in_adv = target
            logging.info(f"连接蓝牙心率设备: {dev_name} ({addr})")
            if not hr_in_adv:
                logging.warning(
                    "该设备当前没有广播 0x180D 心率服务（很可能手机上 App 没在广播）。"
                    "连接后可能找不到 0x2A37 特征。"
                )

            disconnected_event = asyncio.Event()
            # bleak 3.x: 断连回调通过构造函数传入
            client = BleakClient(
                addr,
                timeout=CONNECT_TIMEOUT,
                disconnected_callback=lambda _c: disconnected_event.set(),
            )
            try:
                await client.connect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"连接失败: {describe_exception(e)}")
                if isinstance(e, asyncio.TimeoutError):
                    logging.error(
                        "  → 连了 %s 秒都没握上手。设备在广播但拒绝建立连接，"
                        "通常是手机 App 退到后台/屏幕锁定，或已被别的中心设备占用。",
                        CONNECT_TIMEOUT,
                    )
                if not shutdown_event.is_set():
                    await asyncio.sleep(RECONNECT_DELAY)
                continue

            logging.info("已连接，订阅心率通知 (0x2A37)...")

            got_data = False

            def on_notify(_sender, data: bytearray):
                nonlocal got_data
                hr = parse_hr_measurement(bytes(data))
                if hr is not None and hr > 0:
                    got_data = True
                    smoother.update_target(hr)

            try:
                await client.start_notify(HR_MEASUREMENT_UUID, on_notify)
                logging.info("心率通知已订阅，等待数据...")

                # 等待断连或收到关闭信号；顺带监控是否一直收不到数据
                last_warn = time.time()
                while not shutdown_event.is_set() and not disconnected_event.is_set():
                    await asyncio.sleep(0.5)
                    if not got_data and (time.time() - last_warn) > 10:
                        last_warn = time.time()
                        logging.warning(
                            "已订阅但 10 秒内没收到任何心率数据；"
                            "请确认手机 App 正在广播且屏幕未熄灭。"
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"订阅心率通知异常: {describe_exception(e)}")
                # 关键诊断：把设备实际暴露的 GATT 服务表打出来
                try:
                    svcs = list(client.services) if client.services else []
                    if svcs:
                        logging.error(f"设备实际暴露 {len(svcs)} 个服务:")
                        for s in svcs:
                            logging.error(f"  [service] {s.uuid}")
                            for ch in s.characteristics:
                                logging.error(f"      [char] {ch.uuid} props={ch.properties}")
                        logging.error(
                            "如果上面没有 0000180d-...，说明这台设备当前没有提供标准心率服务，"
                            "请先在手机上启动「心率广播」。"
                        )
                    else:
                        logging.error("设备未暴露任何 GATT 服务（连接可能不完整）")
                except Exception as dump_err:
                    logging.error(f"读取服务列表也失败: {dump_err}")
            finally:
                try:
                    await client.stop_notify(HR_MEASUREMENT_UUID)
                except Exception:
                    pass
                try:
                    await client.disconnect()
                except Exception:
                    pass

            if disconnected_event.is_set():
                logging.warning("蓝牙连接已断开，准备重连")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"蓝牙客户端异常: {e}")

        if not shutdown_event.is_set():
            await asyncio.sleep(RECONNECT_DELAY)

    logging.debug("蓝牙客户端已停止")

# -------------------- 主任务 --------------------
async def main(address: str, name_filter: str, osc_address: str):
    logging.info(
        f"启动: Python {sys.version.split()[0]}"
        f"{' (打包 exe)' if getattr(sys, 'frozen', False) else ''}"
    )
    osc = AsyncOSCSender(VRC_OSC_IP, VRC_OSC_PORT)
    await osc.start()
    logging.info(f"OSC 目标 {VRC_OSC_IP}:{VRC_OSC_PORT} -> {osc_address}")

    smoother = HeartrateSmoother(osc, osc_address)

    ble_task = asyncio.create_task(ble_heartrate_client(smoother, address, name_filter))
    smooth_task = asyncio.create_task(smoother.run())

    # 等待外部关闭信号
    await shutdown_event.wait()

    # 优雅关闭：取消任务并等待它们结束
    logging.info("正在执行清理...")
    ble_task.cancel()
    smooth_task.cancel()
    await asyncio.gather(ble_task, smooth_task, return_exceptions=True)

    osc.close()
    logging.info("清理完成，程序退出")

# -------------------- 命令行参数 --------------------
def parse_args():
    p = argparse.ArgumentParser(
        description="BLE 蓝牙心率 → VRChat OSC 桥接（走蓝牙版）",
        epilog="示例: python osc_heartrate_ble.py --scan",
    )
    p.add_argument("--scan", action="store_true", help="只扫描并列出附近的 BLE 设备，然后退出")
    p.add_argument("--address", default=BLE_DEVICE_ADDRESS, help="指定 BLE 设备 MAC 地址（默认留空自动扫描）")
    p.add_argument("--name", default=BLE_DEVICE_NAME_FILTER, help="设备名关键字（默认 xinlvguangbo）")
    p.add_argument("--osc-address", default=OSC_ADDRESS, help="OSC 参数地址（默认 /avatar/parameters/VRCOSC/Heartrate/Value）")
    return p.parse_args()

# -------------------- 程序入口与信号处理 --------------------
if __name__ == "__main__":
    args = parse_args()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    if args.scan:
        try:
            loop.run_until_complete(list_devices(SCAN_DURATION))
        finally:
            loop.close()
        sys.exit(0)

    main_task = loop.create_task(main(args.address, args.name, args.osc_address))

    def shutdown():
        logging.info("收到终止信号，正在退出...")
        shutdown_event.set()  # 通知 main() 开始清理

    # 注册信号处理器
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown)
        except NotImplementedError:
            # Windows 下不支持 add_signal_handler，回退到传统方法
            signal.signal(sig, lambda s, f: shutdown())

    try:
        loop.run_until_complete(main_task)
    except asyncio.CancelledError:
        pass
    finally:
        loop.close()
