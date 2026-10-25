"""YOLOv8n-cls 학습 + ONNX 내보내기 (① 종 모델: 1025종 + none. teeniping-dex 에서 포크).

사용: python scripts/train/train-cls.py --epochs 12 [--name train_v4] [--imgsz 224]
결과: workspace/models/<name>/weights/best.pt, best.onnx
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/ (paths.py)
import paths  # noqa: E402
import argparse

from ultralytics import YOLO

ap = argparse.ArgumentParser()
ap.add_argument("--data", default=str(paths.DATASET_CLS))
ap.add_argument("--weights", default=str(paths.PRETRAINED))
ap.add_argument("--epochs", type=int, default=30)
ap.add_argument("--imgsz", type=int, default=224)
ap.add_argument("--batch", type=int, default=64)
ap.add_argument("--workers", type=int, default=8, help="데이터 로더 작업자 (포켓몬 train 약 31만 장)")
ap.add_argument("--device", default="mps")  # Apple Silicon. CUDA 면 0, 아니면 cpu
ap.add_argument("--name", default=paths.RUN_NAME, help="결과 폴더 이름 (workspace/models/<name>). 기본값이 현재 모델이다")
ap.add_argument("--overwrite", action="store_true", help="이미 있는 결과를 덮어쓴다")
a = ap.parse_args()

if (paths.MODELS / a.name / "weights" / "best.pt").exists() and not a.overwrite:
    raise SystemExit(f"workspace/models/{a.name} 에 이미 학습 결과가 있다 (현재 모델일 수 있다). 새 이름은 --name, 덮어쓰기는 --overwrite")

model = YOLO(a.weights)
model.train(
    data=a.data, epochs=a.epochs, imgsz=a.imgsz, batch=a.batch, device=a.device,
    project=str(paths.MODELS), name=a.name, exist_ok=True,
    workers=a.workers, patience=10,
    # optimizer=auto 는 반복 횟수(에폭 x 배치 수)가 1만 미만이면 AdamW + 아주 작은 lr(6e-5)을 골라 학습이 안 된다.
    # (에폭을 30 -> 10 으로 줄였더니 val 정확도가 무작위 수준에 머물렀다.) 에폭 수와 무관하게 고정한다.
    optimizer="SGD", lr0=0.01,
    # 합성 단계에서 이미 증강했으므로 ultralytics 증강은 약하게
    fliplr=0.0, erasing=0.2, auto_augment=None,
)
print(model.val(project=str(paths.MODELS / "ultralytics_val"), name=a.name, exist_ok=True))   # 루트에 runs/ 가 생기지 않게

best = YOLO(str(model.trainer.save_dir / "weights" / "best.pt"))
# 고정 shape + simplify. opset 13: per-channel 양자화(DequantizeLinear axis)에 필요하다 (opset 12 에서는 per-tensor 만 되어 정확도가 크게 떨어졌다).
best.export(format="onnx", imgsz=a.imgsz, opset=13, simplify=True, dynamic=False)
