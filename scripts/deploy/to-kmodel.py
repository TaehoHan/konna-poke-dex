"""best.onnx -> teeniping.kmodel (CanMV K230 KPU, nncase PTQ).

!! 이 스크립트는 공식 문서(K230 nncase 개발 가이드)의 API 이름·예제를 따라 작성했지만,
!! nncase-kpu 가 x86 Linux/Windows 전용이라 이 맥에서는 실행해 보지 못했다 (미검증).
!! 첫 실행에서 옵션 이름이 버전마다 달라 에러가 나면 `python -c "import nncase; print(nncase.__version__)"`와
!! 설치된 버전의 문서를 대조해 고친다.

환경 (x86 Linux / WSL2 / Windows, 보드 펌웨어의 nncase 런타임과 **같은 버전**):
    pip install nncase nncase-kpu onnx onnxsim numpy opencv-python
  - kmodel 을 만든 nncase 버전과 보드 SDK 의 nncase 런타임 버전이 다르면 보드에서 추론이 이상해진다.
    펌웨어/SDK 가 요구하는 버전을 확인해 같은 버전을 설치할 것 (docs/KMODEL.md).
  - nncase 휠은 Python 3.6~3.10 대 기준 (지원 버전은 설치 시 pip 가 알려준다).

사용:
    python to-kmodel.py --onnx best.onnx --calib dataset_cls/val --out teeniping.kmodel   # 학습 PC 의 workspace/dataset_cls/val 일부를 복사해 둔 폴더
옵션:
    --calibrate Kld|NoClip     보정 방식 (문서 기본 Kld). 정확도가 낮으면 NoClip 도 시도
    --w-quant uint8|int8|int16 가중치 양자화 타입. 정확도가 떨어지면 int16 (이 경우 --quant 는 uint8)
    --n 100                    보정 이미지 수 (공식 예제는 6장이지만 많을수록 안정적)
    --export-check 20          ONNX 기준 정답(JSON)과 이미지를 check/ 에 저장 (보드 출력과 비교용)

전제: 모델 입력은 NCHW float 0~1 RGB (ultralytics 전처리). 컴파일러가 전처리를 모델에 넣어서
      kmodel 은 **uint8 RGB(0~255) NCHW 이미지를 그대로 받는다** (mean=0, std=255).
      보드에서는 ai2d 로 224x224 로 resize 한 RGB planar uint8 을 넣는다.
"""
import argparse
import ast
import glob
import json
import os
import shutil

import cv2
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--onnx", default="best.onnx")
ap.add_argument("--calib", default="calib", help="<클래스>/*.jpg 구조의 보정 이미지 폴더 (none 클래스 포함 권장)")
ap.add_argument("--out", default="teeniping.kmodel")
ap.add_argument("--imgsz", type=int, default=224)
ap.add_argument("--n", type=int, default=100, help="보정 이미지 수")
ap.add_argument("--target", default="k230")
ap.add_argument("--calibrate", default="Kld", choices=["Kld", "NoClip"])
ap.add_argument("--quant", default="uint8", choices=["uint8", "int8", "int16"], help="활성값(data) 양자화 타입")
ap.add_argument("--w-quant", default="uint8", choices=["uint8", "int8", "int16"], help="가중치 양자화 타입")
ap.add_argument("--dump-dir", default="tmp/kmodel")
ap.add_argument("--export-check", type=int, default=0, help="보드 검증용 샘플 이미지/ONNX 정답을 check/ 에 저장할 장수")
a = ap.parse_args()
if a.quant == "int16" and a.w_quant == "int16":
    raise SystemExit("--quant 와 --w-quant 는 동시에 int16 일 수 없다 (문서 제약)")

import nncase  # noqa: E402  (x86 에서만 설치됨)
import onnx  # noqa: E402

print("nncase", getattr(nncase, "__version__", "?"))
os.makedirs(a.dump_dir, exist_ok=True)

# ---------- 1) ONNX 읽기 + (선택) simplify ----------
model_path = a.onnx
try:
    import onnxsim
    m, ok = onnxsim.simplify(onnx.load(a.onnx))
    if ok:
        model_path = os.path.join(a.dump_dir, "simplified.onnx")
        onnx.save(m, model_path)
        print("onnxsim OK")
except ImportError:
    print("onnxsim 없음: simplify 건너뜀 (ultralytics 가 이미 simplify 했다)")

# 클래스 이름 (보드의 라벨 파일). ultralytics 가 ONNX 메타데이터에 넣어 둔다.
meta = {p.key: p.value for p in onnx.load(a.onnx).metadata_props}
names = ast.literal_eval(meta["names"]) if "names" in meta else None
labels = [names[i] for i in sorted(names)] if names else []
if labels:
    lp = os.path.splitext(a.out)[0] + ".labels.txt"
    open(lp, "w", encoding="utf-8").write("\n".join(labels) + "\n")
    print(f"라벨 {len(labels)}개 -> {lp} ('none' 은 알파벳순 {labels.index('none') if 'none' in labels else '없음'}번 인덱스: 배경/모름, 클래스 순서는 이 파일 기준)")

# ---------- 2) 보정 데이터: uint8 RGB NCHW (1,3,H,W), 클래스를 고르게 ----------
by_class = {}
for f in sorted(glob.glob(os.path.join(a.calib, "*", "*.jpg"))):
    by_class.setdefault(os.path.basename(os.path.dirname(f)), []).append(f)
classes = sorted(by_class)
files, i = [], 0
while len(files) < a.n and any(by_class.values()):          # 클래스를 돌아가며 한 장씩
    c = classes[i % len(classes)]
    if by_class[c]:
        files.append(by_class[c].pop(len(by_class[c]) // 2))
    i += 1
print(f"보정 이미지 {len(files)}장 ({len(classes)}개 클래스에서)")


def load_u8(f):
    img = cv2.cvtColor(cv2.imread(f), cv2.COLOR_BGR2RGB)
    img = cv2.resize(img, (a.imgsz, a.imgsz), interpolation=cv2.INTER_LINEAR)
    return np.transpose(img, (2, 0, 1))[np.newaxis, ...].astype(np.uint8)   # (1,3,H,W)


calib = [[load_u8(f)] for f in files]                       # List[List[ndarray]]: 샘플 x 입력

# ---------- 3) 컴파일 옵션 (공식 문서 예제 yolov5s_onnx.py 와 같은 구조) ----------
co = nncase.CompileOptions()
co.target = a.target
co.dump_ir = False
co.dump_asm = False
co.dump_dir = a.dump_dir
co.preprocess = True
co.swapRB = False                       # 입력이 이미 RGB
co.input_type = "uint8"
co.input_shape = [1, 3, a.imgsz, a.imgsz]
co.input_range = [0, 255]
co.mean = [0, 0, 0]
co.std = [255, 255, 255]                # (x - 0) / 255  == ultralytics 의 /255
co.input_layout = "NCHW"

compiler = nncase.Compiler(co)
compiler.import_onnx(open(model_path, "rb").read(), nncase.ImportOptions())

# ---------- 4) PTQ ----------
ptq = nncase.PTQTensorOptions()
ptq.samples_count = len(calib)
ptq.calibrate_method = a.calibrate
ptq.finetune_weights_method = "NoFineTuneWeights"
ptq.quant_type = a.quant
ptq.w_quant_type = a.w_quant
ptq.export_weight_range_by_channel = True   # 문서가 True 를 권장 (by-channel 가중치 범위). ONNX Runtime 모사에서도 per-channel 이 필수였다
ptq.quant_scheme = ""
ptq.quant_scheme_strict_mode = False
ptq.export_quant_scheme = True
ptq.set_tensor_data(calib)
compiler.use_ptq(ptq)

compiler.compile()
open(a.out, "wb").write(compiler.gencode_tobytes())
print(f"saved {a.out} ({os.path.getsize(a.out) / 1e6:.2f}MB)")

# ---------- 5) 보드 비교용 샘플 (ONNX 가 낸 정답) ----------
if a.export_check:
    import onnxruntime as ort
    sess = ort.InferenceSession(a.onnx, providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0].name
    shutil.rmtree("check", ignore_errors=True)
    os.makedirs("check")
    rec = []
    for k, f in enumerate(files[:: max(1, len(files) // a.export_check)][: a.export_check]):
        u8 = load_u8(f)
        p = sess.run(None, {inp: u8.astype(np.float32) / 255.0})[0][0]
        top = np.argsort(-p)[:3]
        name = f"check/{k:03d}.png"
        cv2.imwrite(name, cv2.cvtColor(np.transpose(u8[0], (1, 2, 0)), cv2.COLOR_RGB2BGR))
        rec.append({"image": name, "top3": [(labels[j] if labels else int(j), round(float(p[j]), 4)) for j in top]})
    json.dump(rec, open("check/onnx_expected.json", "w"), ensure_ascii=False, indent=1)
    print("check/ 에 샘플 이미지와 ONNX 정답 저장: 보드에서 같은 이미지를 넣어 top-1 이 같은지 비교")
