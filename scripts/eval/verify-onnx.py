"""ONNX 검증: (1) 사용된 연산자 목록 (2) val 정확도 fp32 (3) int8 정적 양자화 모사 정확도.

nncase PTQ(int8)와 같지는 않지만 양자화로 정확도가 얼마나 떨어질지 가늠하는 용도다.
사용: python scripts/eval/verify-onnx.py [--onnx PATH] [--data workspace/dataset_cls/val] [--n 20]   (--n: 클래스당 평가 장수)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/ (paths.py)
import paths  # noqa: E402
import argparse
import collections
import glob
import os

import cv2
import numpy as np
import onnx
import onnxruntime as ort
from onnxruntime.quantization import CalibrationDataReader, CalibrationMethod, QuantFormat, QuantType, quantize_static

ap = argparse.ArgumentParser()
ap.add_argument("--onnx", default=str(paths.BEST_ONNX))
ap.add_argument("--data", default=str(paths.DATASET_CLS / "val"))
ap.add_argument("--n", type=int, default=20)
ap.add_argument("--calib", type=int, default=200)
a = ap.parse_args()

classes = sorted(os.listdir(a.data))
items = []
for ci, c in enumerate(classes):
    for f in sorted(glob.glob(os.path.join(a.data, c, "*.jpg")))[: a.n]:
        items.append((f, ci))


def load(f, size=224):
    img = cv2.cvtColor(cv2.imread(f), cv2.COLOR_BGR2RGB)
    img = cv2.resize(img, (size, size)).astype(np.float32) / 255.0
    return img.transpose(2, 0, 1)[None]


def evaluate(path, tag):
    s = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    name = s.get_inputs()[0].name
    ok = 0
    wrong = collections.Counter()
    for f, y in items:
        p = int(np.argmax(s.run(None, {name: load(f)})[0]))
        ok += p == y
        if p != y:
            wrong[(classes[y], classes[p])] += 1
    print(f"{tag}: top1 {ok / len(items):.4f} ({ok}/{len(items)})")
    return wrong


m = onnx.load(a.onnx)
ops = collections.Counter(n.op_type for n in m.graph.node)
print("opset", [o.version for o in m.opset_import], "입력", [(i.name, [d.dim_value for d in i.type.tensor_type.shape.dim]) for i in m.graph.input])
print("연산자:", dict(ops))
print("크기: %.1fMB, 클래스 %d, 평가 %d장" % (os.path.getsize(a.onnx) / 1e6, len(classes), len(items)))

w32 = evaluate(a.onnx, "fp32")


class Reader(CalibrationDataReader):
    def __init__(self):
        files = sorted(glob.glob(os.path.join(a.data, "*", "*.jpg")))
        files = files[:: max(1, len(files) // a.calib)][: a.calib]
        self.it = iter(files)
        self.name = ort.InferenceSession(a.onnx, providers=["CPUExecutionProvider"]).get_inputs()[0].name

    def get_next(self):
        f = next(self.it, None)
        return None if f is None else {self.name: load(f)}


def quant(tag, **kw):
    os.makedirs(paths.TMP, exist_ok=True)
    q = str(paths.TMP / (Path(a.onnx).stem + f"_int8_{tag}.onnx"))   # 모델 폴더의 파일을 덮어쓰지 않게 tmp 에 쓴다
    quantize_static(a.onnx, q, Reader(), quant_format=QuantFormat.QDQ, activation_type=QuantType.QUInt8, weight_type=QuantType.QInt8, **kw)
    print(f"int8({tag}) 크기: %.1fMB" % (os.path.getsize(q) / 1e6))
    return evaluate(q, f"int8({tag})")


# 160종 모델에서 per-tensor 는 98%대 -> 88%로 떨어졌고, per-channel + Percentile 은 fp32 와 같았다 (opset 13 필요).
w8 = quant("per-tensor")
w8c = quant("per-channel+percentile", per_channel=True, calibrate_method=CalibrationMethod.Percentile)
print("fp32 오답 상위:", w32.most_common(6))
print("int8(per-channel+percentile) 오답 상위:", w8c.most_common(6))
