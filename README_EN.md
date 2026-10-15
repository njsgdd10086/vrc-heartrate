# VRChat Contactless Heart Rate

Measure heart rate **contactlessly with a regular webcam** and send it to
VRChat via **OSC** (avatar parameter, plus optional Chatbox display).

Built on [FacePhys](https://github.com/KegangWangCCNU/FacePhys-Release)
(a lightweight rPPG model) and YuNet face detection.
**All inference runs locally — no data leaves your machine.**

> 中文文档: [README.md](README.md)

---

## Features

- **Contactless heart rate** — any ordinary USB webcam, no chest strap or wearable
- **Web UI** — pick a camera, watch the live preview and pulse waveform, tweak
  settings in the browser. No command line needed.
- **Camera guidance** — lists real device names and marks which one can see your face
- **Three outputs**
  - Avatar parameter (default `/avatar/parameters/VRCOSC/Heartrate/Value`)
  - Chatbox (`/chatbox/input`, with a customizable template and rate limit)
  - **Virtual camera** — forward the video so face-tracking software can share
    the same physical camera
- **Signal quality gating** — two independent estimators (autocorrelation +
  Welch PSD) cross-check each other; the app refuses to output rather than
  inventing a number from noise

---

## Quick start

### Option 1: prebuilt exe (recommended for most users)

1. Download `VRCHeartRate-*.zip` from [Releases](../../releases) and extract
   it to **any folder**
2. Run `VRCHeartRateWeb.exe` — your browser opens automatically
3. In VRChat, enable **Settings → OSC → Enabled**
4. Pick a camera (the entry marked `★有人脸` is the one facing you) → click
   **开始** (Start) → face the camera and stay still
5. Heart rate appears in ~10–15 seconds

> **Important**: keep the exe together with the `_internal` folder — do not
> copy the exe out on its own.
>
> The UI is currently in Chinese. Pull requests for i18n are welcome.

### Option 2: run from source

Python 3.10+ required.

```bash
git clone https://github.com/njsgdd10086/vrc-heartrate
cd vrc-heartrate

# Download model weights (~3.3 MB)
python tools/download_models.py

# Install dependencies
pip install -r requirements.txt

# Run
python osc_heartrate_cam.py
```

The browser opens `http://127.0.0.1:7860`.

The model weights are not committed (3 MB total) — `download_models.py` fetches
them. If that script fails (network restrictions), download manually into
`models/`:

| File | Source |
|---|---|
| `FacePhys.onnx` | [FacePhys-Release](https://github.com/KegangWangCCNU/FacePhys-Release) → `weights/model.onnx` |
| `face_detection_yunet_2023mar.onnx` | [OpenCV Zoo](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) |

---

## Camera is already in use by another app

**USB webcams normally allow only one application to open them.** If your
face-tracking app, camera app, OBS or a browser already holds the device, this
app sees no frames.

Two ways around it:

### Option A: use two different cameras (simplest)

If you have two cameras (e.g. an external USB one plus a laptop built-in), give
one to each app. They are independent physical devices, so there is no conflict.

The dropdown shows real device names, for example:

```
#0  4M HD Camera           <- external color camera
#1  HD Camera              <- laptop built-in color camera
#2  IR Camera  [红外]       <- infrared, NOT usable
```

> Infrared cameras carry almost no color information, and rPPG relies on subtle
> skin **color** changes. **Do not pick a device marked `[红外]`.**

### Option B: virtual camera (when you only have one camera)

Tick **启用虚拟摄像头输出** (Enable virtual camera output) in the UI, then:

1. Click **开始** — the app takes the physical camera exclusively and forwards
   the frames to a virtual camera
2. The status bar shows the virtual camera's device name (usually
   `OBS Virtual Camera`)
3. Point your **face-tracking software** at that device instead

Requires a **virtual camera driver** to be present on the system. Either
[OBS Studio](https://obsproject.com/) (provides `OBS Virtual Camera`) or
[UnityCapture](https://github.com/schellingb/UnityCapture) (provides
`Unity Video Capture`) works — the app picks whichever is available, and
**neither needs to be running**; this app acts as the producer itself.

No device name is hard-coded: the backend is auto-discovered via pyvirtualcam,
and the status bar shows the device that was actually created — use that name
in your face-tracking software.

> ⚠️ Ordering matters: the virtual camera only exists after this app has
> started and you clicked **开始**.

> ⚠️ **Privacy**: with this enabled, the video becomes available to *other local
> applications* through the system's virtual camera driver. That is the point of
> the feature — just be aware of it.

---

## Settings

| Setting | Default | Notes |
|---|---|---|
| OSC address | `/avatar/parameters/VRCOSC/Heartrate/Value` | must match a `float` parameter on your avatar |
| OSC port | `9000` | VRChat default |
| Signal threshold | `0.30` | below this no heart rate is emitted; higher = stricter but may stay silent longer |
| Smoothing | `0.10` | lower = smoother, higher = more responsive |
| Chatbox template | `当前心率：{hr}` | `{hr}` is replaced with the BPM value |
| Chatbox interval | `5` s | VRChat rate-limits the chatbox; too short and messages get dropped |

---

## CLI options

The web UI is the primary interface; all CLI flags are optional.

```bash
python osc_heartrate_cam.py                 # default, opens the browser
python osc_heartrate_cam.py --no-browser    # don't open a browser
python osc_heartrate_cam.py --port 7900     # change port (auto-advances if busy)
python osc_heartrate_cam.py --max-index 8   # scan more camera indices (default 6)
python osc_heartrate_cam.py --size 1280x720 # preferred resolution
```

---

## How it works

```
camera -> YuNet face detect -> crop 36x36 -> FacePhys (ONNX) -> BVP waveform
                                                                    |
                                    band-pass + uniform resampling --+
                                                                    v
                                  autocorrelation (primary) + Welch PSD (check)
                                                                    |
                                              median filter + smoothing
                                                                    v
                                  OSC -> VRChat parameter / chatbox
```

Key details:

- **Preprocessing mirrors the official web demo**: the face box is scaled 1.2×
  vertically and shifted up by 20% to include the forehead — the most
  informative region for rPPG
- **Uniform resampling**: webcam frame rate fluctuates, but FFT/autocorrelation
  require evenly spaced samples, so the signal is interpolated to a fixed 30 Hz
- **Cross-validation**: when the two estimators disagree strongly, the signal is
  treated as ambiguous and confidence drops
- **Validated against the official sample**: both estimators return 75.0 BPM
  with an autocorrelation peak of 0.72
- **Camera backend matters a lot**: `cap.read()` accounts for ~85% of the
  pipeline time. On the test machine MSMF is twice as fast as DSHOW
  (30 fps vs 15 fps), so MSMF is preferred. See `docs/BUILD.md`.

---

## Known limitations

Please read these before drawing conclusions:

- **Research model, not a medical device.** Not for any diagnostic use.
- **Accuracy depends heavily on camera and lighting.** rPPG relies on ~0.1%
  brightness changes in skin. These hurt it badly:
  - aggressive camera auto-exposure / auto-white-balance (a *static wall* can
    show several percent brightness fluctuation)
  - low light, or flickering sources (cheap LED / fluorescent)
  - large movement, talking, facial occlusion
- **Infrared cameras are unusable** — the image carries almost no color.
- **VRChat's built-in face tracking uses headset cameras**, so it does not
  conflict with a USB webcam. The virtual camera feature targets **third-party
  face-tracking software that reads a USB webcam**.
- **Large bundle (~500 MB)** — gradio's frontend assets, matplotlib and pandas
  are hard dependencies of gradio.

---

## Privacy

All biometric inference and signal processing happens **locally**.
No images or video are uploaded anywhere.

- The heart-rate value is sent via OSC (UDP) to `127.0.0.1:9000`, i.e. VRChat on
  the same machine
- **Note however**: VRChat is an online platform, so forwarding a derived
  physiological metric to it is something you should be aware of and consent to
- If you enable **virtual camera output**, the video becomes available to *other
  local applications* through the system's virtual camera driver

The [FacePhys](https://github.com/KegangWangCCNU/FacePhys-Release) license
includes a **Privacy Protection Addendum** requiring biometric inference to be
local-only and derived metrics not to be transmitted off-device without consent.
This project satisfies those terms; if you redistribute, you must preserve them
(the full text ships with the model as `models/FacePhys-LICENSE.txt`).
See [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

---

## Project layout

```
.
├── osc_heartrate_cam.py        # main: camera rPPG -> VRChat OSC
├── osc_heartrate_ble.py        # alternative: BLE heart-rate strap -> OSC
├── facephys.py                 # minimal FacePhys ONNX inference wrapper
├── requirements.txt
├── models/                     # model weights (fetched by download_models.py)
│   ├── FacePhys.onnx
│   ├── FacePhys-LICENSE.txt    # upstream license incl. privacy addendum
│   └── face_detection_yunet_2023mar.onnx
├── tools/
│   ├── download_models.py
│   ├── build_web.spec          # PyInstaller config
│   ├── fix_msvc_dlls.py        # post-build MSVC runtime fix (see docs/BUILD.md)
│   ├── verify_pipeline.py      # end-to-end FPS / latency check
│   ├── profile_*.py            # profiling (backend reads, rendering, capture)
│   └── diag_*.py               # diagnostics (camera sharing / color / indexing)
└── docs/
    ├── BUILD.md
    └── TROUBLESHOOTING.md
```

---

## Building the exe

See [docs/BUILD.md](docs/BUILD.md). It documents three packaging traps that make
the exe fail to start (onnxruntime DLL, OpenCV non-ASCII paths, virtual camera
dependency collection), plus why the camera backend choice affects preview
smoothness.

---

## License

Project code: [MIT](LICENSE)

### ⚠️ Virtual camera feature and GPL

The virtual camera output depends on
[pyvirtualcam](https://github.com/letmaik/pyvirtualcam), which is **GPL-2.0**
(note: version 2, not 3). This project is structured to keep that optional:

- Core heart-rate measurement does **not** depend on pyvirtualcam — it is
  imported lazily, only when you enable the virtual camera
- So for an MIT release you can simply not install or bundle it
- If you do bundle it, the distribution as a whole is subject to GPL-2.0 —
  state that in your release notes

The feature additionally needs a system-level **virtual camera driver**. Either
of these works, and the app picks whichever is registered (neither is bundled):

| Provider | Device name | License |
|---|---|---|
| [OBS Studio](https://obsproject.com/) | `OBS Virtual Camera` | GPL-2.0 |
| [UnityCapture](https://github.com/schellingb/UnityCapture) | `Unity Video Capture` | public domain |

So OBS is **not** strictly required, and neither program needs to be running.

Full third-party terms (including FacePhys's privacy addendum) are in
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

## Credits

- [FacePhys](https://github.com/KegangWangCCNU/FacePhys-Release) — rPPG model
- [OpenCV](https://opencv.org/) — YuNet face detection
- [pyvirtualcam](https://github.com/letmaik/pyvirtualcam) — virtual camera output
- [OBS Studio](https://obsproject.com/) / [UnityCapture](https://github.com/schellingb/UnityCapture) — virtual camera driver
- [Gradio](https://gradio.app/) — web UI
