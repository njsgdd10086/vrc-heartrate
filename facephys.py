"""FacePhys（rPPG）ONNX 推理的最小封装

上游: https://github.com/KegangWangCCNU/FacePhys-Release
      作者 Kegang Wang 等，MIT + Privacy Protection Addendum
      完整的附加条款见 THIRD_PARTY_LICENSES.md（务必阅读，再分发有约束）

这里只保留本项目需要的最少代码，避免让使用者再去 clone 上游仓库。
模型是流式的（SSM 带状态），所以必须逐帧调用并回传 state：

    run = load_facephys("models/FacePhys.onnx")
    state = run.init_state
    for frame in frames:                  # frame: (36, 36, 3) float32, 取值 0~1
        bvp, state = run(frame, state, dt=1/30)

设计说明:
  - intra_op_num_threads = 1 —— 实测推理只要 ~6ms，单线程足够；
    多线程反而会和采集/预览线程抢 CPU，让读帧变慢（读帧才是最慢的一步）
  - 输入名、状态张量名都从 ONNX 图里读，不写死，避免上游改图后失效
"""
from __future__ import annotations

import numpy as np


class FacePhysModel:
    """一个极薄的 ONNX 推理包装，接口与上游 inference.py 的 load_model_onnx 兼容"""

    def __init__(self, path, providers=None):
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.log_severity_level = 3
        self.sess = ort.InferenceSession(
            path, sess_options=so,
            providers=providers or ["CPUExecutionProvider"])

        inputs = self.sess.get_inputs()
        if len(inputs) < 2:
            raise ValueError(
                f"模型输入不符合预期（应有 input / dt / 若干状态张量，"
                f"实际 {len(inputs)} 个）: {[i.name for i in inputs]}")
        self._input_name = inputs[0].name
        self._dt_name = inputs[1].name
        # 第 3 个起都是状态张量（SSM 的隐状态），全部零初始化
        self.init_state = {
            i.name: np.zeros(i.shape, dtype="float32") for i in inputs[2:]}
        if not self.init_state:
            raise ValueError("模型没有状态张量输入，不是流式 rPPG 模型？")

    def __call__(self, img, state=None, dt=1.0 / 30.0):
        """跑一帧。

        img:   (36, 36, 3) float32，取值 0~1
        state: 上一帧返回的状态字典；None 则用 init_state
        返回:  (bvp标量, 新状态字典)
        """
        if state is None:
            state = {k: v.copy() for k, v in self.init_state.items()}
        img = np.asarray(img, dtype=np.float32)
        if img.ndim == 3:
            # (H, W, C) -> (1, 1, H, W, C)，与上游一致
            img = img[None, None]
        res = self.sess.run(
            None, {self._input_name: img,
                   self._dt_name: np.array([dt], dtype="float32"),
                   **state})
        bvp = res[0][0, 0]
        return bvp, dict(zip(state, res[1:]))

    @property
    def state_names(self):
        return list(self.init_state)


def load_facephys(path, providers=None):
    """加载模型，返回可调用的 FacePhysModel（与上游签名一致）"""
    return FacePhysModel(path, providers=providers)
