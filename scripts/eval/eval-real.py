"""학습에 쓰지 않은 '실제' 이미지로 분류기를 평가한다.

합성 val 정확도는 같은 원본 1장의 증강본이라 실제 성능이 아니다. 이 스크립트가 실제 성능의 기준이다.

폴더 구조 (이미지는 jpg/png/webp):
    eval_real/
        <class_id>/*.jpg      # 정답 클래스 폴더 (피규어/카드/화면을 직접 찍은 사진, 장면 캡처 등)
        _negative/*.jpg       # 티니핑이 아닌 이미지 (오탐 확인용, 선택)

사용:
    python eval-real.py                                  # eval_real/ 을 best.onnx 로 평가
    python eval-real.py --model teeniping/train/weights/best.pt
    python eval-real.py --mode letterbox --save-fails fails/   # 틀린 이미지를 폴더에 모아 눈으로 확인
    python eval-real.py --synthetic-negatives 200        # 배경만 있는 합성 음성 추가 (약한 기준선)

출력: 전체/클래스별 정확도, top-5, 혼동 쌍, 신뢰도 분포,
      임계값별 (정답 수용률, 정답 정확도, 음성 거부율) 표와 권장 임계값, eval_real_report.csv

주의:
  - 모델에 없는 클래스 폴더는 건너뛰고 알려준다 (재학습 전 새 클래스 등).
  - coffeeping/muffingping, noraping/noriping 은 원본이 같아 구분 불가라 'indistinguishable' 보정 정확도를 같이 낸다.
  - 이미지 수가 적으면(클래스당 수 장) 숫자는 참고용이다. 장수를 같이 읽을 것.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/ (paths.py)
import paths  # noqa: E402
import argparse
import ast
import collections
import csv
import glob
import importlib.util
import os
import shutil

import cv2
import numpy as np

EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif")
# 원본 이미지가 같아 구분이 불가능한 쌍 (DESIGN.md 참고). 서로 헷갈린 것은 보정 정확도에서 정답으로 센다.
NONE = "none"   # 모델의 배경/모름 클래스. 예측이 none 이면 인식하지 않은 것(신뢰도 0)으로 본다
INDISTINGUISHABLE = [{"coffeeping", "muffingping"}, {"noraping", "noriping"}]


def heic_to_jpg(path):
    """아이폰 HEIC 를 macOS 기본 도구(sips)로 JPG 로 바꿔 임시 파일 경로를 돌려준다."""
    import subprocess
    import tempfile
    out = os.path.join(tempfile.mkdtemp(prefix="evalheic_"), "x.jpg")
    subprocess.run(["sips", "-s", "format", "jpeg", path, "--out", out], check=True, capture_output=True)
    return out


def load_image(path, size, mode):
    if path.lower().endswith((".heic", ".heif")):
        path = heic_to_jpg(path)
    data = np.fromfile(path, np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if img is None:
        from PIL import Image  # webp 등 OpenCV 가 못 읽는 경우
        img = np.array(Image.open(path).convert("RGBA"))
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGRA)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:  # 투명 배경은 흰색 위에 합성
        a = img[..., 3:4].astype(np.float32) / 255
        img = (img[..., :3] * a + 255 * (1 - a)).astype(np.uint8)
    h, w = img.shape[:2]
    if mode == "stretch":      # 보드에서 가장 단순한 resize
        out = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
    elif mode == "center":     # 가운데 정사각형 crop
        s = min(h, w)
        y, x = (h - s) // 2, (w - s) // 2
        out = cv2.resize(img[y:y + s, x:x + s], (size, size), interpolation=cv2.INTER_AREA)
    else:                      # letterbox: 비율 유지 + 회색 여백
        s = size / max(h, w)
        r = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
        out = np.full((size, size, 3), 114, np.uint8)
        y, x = (size - r.shape[0]) // 2, (size - r.shape[1]) // 2
        out[y:y + r.shape[0], x:x + r.shape[1]] = r
    x = cv2.cvtColor(out, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0   # ultralytics 전처리와 동일
    return x.transpose(2, 0, 1)[None]


class Model:
    def __init__(self, path, data_dir):
        self.kind = "onnx" if path.endswith(".onnx") else "pt"
        if self.kind == "onnx":
            import onnxruntime as ort
            self.s = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
            self.inp = self.s.get_inputs()[0].name
            shape = self.s.get_inputs()[0].shape
            self.size = int(shape[2]) if isinstance(shape[2], int) else 224
            meta = self.s.get_modelmeta().custom_metadata_map
            names = ast.literal_eval(meta["names"]) if "names" in meta else None
            self.names = [names[i] for i in sorted(names)] if names else None
        else:
            import torch
            from ultralytics import YOLO
            self.torch = torch
            y = YOLO(path)
            self.net = y.model.eval().float().cpu()
            self.size = 224
            self.names = [y.names[i] for i in sorted(y.names)]
        if not self.names:  # 메타데이터가 없으면 학습 폴더 이름 순서 (ultralytics 규칙)
            self.names = sorted(os.listdir(os.path.join(data_dir, "train")))

    def probs(self, x):
        if self.kind == "onnx":
            p = self.s.run(None, {self.inp: x})[0][0]
        else:
            with self.torch.no_grad():
                o = self.net(self.torch.from_numpy(x))
                p = (o[0] if isinstance(o, (list, tuple)) else o)[0].numpy()
        p = np.asarray(p, np.float64)
        if not (0 <= p.min() and abs(p.sum() - 1) < 1e-3):  # softmax 가 모델 밖이면 적용
            e = np.exp(p - p.max())
            p = e / e.sum()
        return p


def collect(root):
    items = collections.defaultdict(list)
    if not os.path.isdir(root):
        return items
    for d in sorted(os.listdir(root)):
        if d.startswith(".") or not os.path.isdir(os.path.join(root, d)):
            continue
        for f in sorted(glob.glob(os.path.join(root, d, "**", "*"), recursive=True)):
            if f.lower().endswith(EXTS):
                items[d].append(f)
    return items


def synthetic_negatives(n, size, out_dir):
    """make-dataset.py 의 배경 생성기로 '배경만 있는' 이미지를 만든다. 실제 음성의 대용이 아니라 최소 기준선이다."""
    import random
    spec = importlib.util.spec_from_file_location("md", str(paths.MAKE_DATASET))
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)
    os.makedirs(out_dir, exist_ok=True)
    rng = random.Random(123)
    out = []
    for i in range(n):
        img = md.augment(md.random_background(rng, size), rng)
        p = os.path.join(out_dir, f"syn_{i:04d}.jpg")
        cv2.imwrite(p, img)
        out.append(p)
    return out


def same_group(a, b):
    return a == b or any(a in g and b in g for g in INDISTINGUISHABLE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(paths.EVAL_REAL))
    ap.add_argument("--model", default=str(paths.BEST_ONNX))
    ap.add_argument("--train-data", default=str(paths.DATASET_CLS), help="모델에 클래스명 메타데이터가 없을 때 순서를 얻는 폴더")
    ap.add_argument("--mode", choices=["stretch", "center", "letterbox"], default="stretch",
                    help="보드의 전처리와 맞출 것 (K230 ai2d resize 가 stretch 면 stretch)")
    ap.add_argument("--synthetic-negatives", type=int, default=0)
    ap.add_argument("--target-reject", type=float, default=0.95, help="권장 임계값을 정할 때 목표 음성 거부율")
    ap.add_argument("--save-fails", default=None)
    ap.add_argument("--report", default=str(paths.LOGS / "eval_real_report.csv"))
    a = ap.parse_args()

    model = Model(a.model, a.train_data)
    known = {n: i for i, n in enumerate(model.names)}
    items = collect(a.data)
    neg_files = items.pop("_negative", [])
    if a.synthetic_negatives:
        neg_files = neg_files + synthetic_negatives(a.synthetic_negatives, model.size, str(paths.TMP / "eval_syn_neg"))

    skipped = {c: len(v) for c, v in items.items() if c not in known}
    items = {c: v for c, v in items.items() if c in known}
    n_pos = sum(len(v) for v in items.values())
    print(f"모델: {a.model} ({len(model.names)}클래스, 입력 {model.size}, 전처리 {a.mode})")
    print(f"평가: 클래스 {len(items)}개, 이미지 {n_pos}장 / 음성 {len(neg_files)}장")
    if skipped:
        print(f"모델에 없는 클래스라 건너뜀: {skipped}")
    if n_pos == 0:
        print("평가할 이미지가 없다. eval_real/<class_id>/ 에 실제 이미지를 넣을 것 (docs 참고).")
        return

    rows, per = [], collections.defaultdict(lambda: [0, 0, 0])   # 클래스별 [맞음, top5, 전체]
    wrong = collections.Counter()
    for c, files in items.items():
        for f in files:
            try:
                p = model.probs(load_image(f, model.size, a.mode))
            except Exception as e:
                print(f"[읽기 실패] {f}: {e}")
                continue
            order = np.argsort(-p)
            top = model.names[order[0]]
            ok = top == c
            ok5 = known[c] in order[:5]
            per[c][0] += ok
            per[c][1] += ok5
            per[c][2] += 1
            if not ok:
                wrong[(c, top)] += 1
                if a.save_fails:
                    os.makedirs(a.save_fails, exist_ok=True)
                    shutil.copy(f, os.path.join(a.save_fails, f"{c}__as__{top}__{os.path.basename(f)}"))
            eff = 0.0 if top == NONE else p[order[0]]   # none 예측은 인식 실패
            rows.append([f, c, top, f"{eff:.4f}", ok, ok5, same_group(c, top)])

    n = len(rows)
    acc = sum(r[4] for r in rows) / n
    acc5 = sum(r[5] for r in rows) / n
    acc_adj = sum(r[6] for r in rows) / n
    print(f"\n== 전체 ({n}장) ==")
    print(f"top1 {acc:.3f}   top5 {acc5:.3f}   구분불가 쌍 보정 top1 {acc_adj:.3f}")
    bad = sorted(((v[0] / v[2], c, v) for c, v in per.items()), key=lambda t: (t[0], -t[2][2]))[:10]
    print("정확도 낮은 클래스 (맞음/전체):", [(c, f"{v[0]}/{v[2]}") for _, c, v in bad if v[0] < v[2]])
    print("자주 틀리는 쌍 (정답→예측):", [(f"{a_}→{b_}", k) for (a_, b_), k in wrong.most_common(8)])
    few = [c for c, v in per.items() if v[2] < 5]
    if few:
        print(f"※ 이미지 5장 미만 클래스 {len(few)}개: 이 클래스들의 수치는 참고용")

    conf = np.array([float(r[3]) for r in rows])
    ok_mask = np.array([r[4] for r in rows])
    print(f"\n신뢰도 평균: 정답 {conf[ok_mask].mean():.3f}" + (f" / 오답 {conf[~ok_mask].mean():.3f}" if (~ok_mask).any() else ""))

    neg_conf = np.array([])
    if neg_files:
        vals = []
        for f in neg_files:
            try:
                pr = model.probs(load_image(f, model.size, a.mode))
                vals.append(0.0 if model.names[int(pr.argmax())] == NONE else float(pr.max()))
            except Exception as e:
                print(f"[읽기 실패] {f}: {e}")
        neg_conf = np.array(vals)
        print(f"음성 신뢰도(none 예측은 0): 평균 {neg_conf.mean():.3f}, 90분위 {np.percentile(neg_conf, 90):.3f}, 최대 {neg_conf.max():.3f}, none 으로 거부 {np.mean(neg_conf == 0):.3f}")

    print("\n임계값(신뢰도 이상일 때만 '인식'으로 인정)별:")
    print(f"{'임계값':>6} {'정답수용률':>10} {'수용분 정확도':>12} {'음성거부율':>10}")
    best_t = None
    for t in [0.0, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99]:
        acc_mask = conf >= t
        cov = acc_mask.mean()
        prec = ok_mask[acc_mask].mean() if acc_mask.any() else float("nan")
        rej = (neg_conf < t).mean() if len(neg_conf) else float("nan")
        print(f"{t:6.2f} {cov:10.3f} {prec:12.3f} {rej:10.3f}")
        if len(neg_conf) and best_t is None and rej >= a.target_reject:
            best_t = t
    if len(neg_conf):
        print(f"권장 임계값: {best_t if best_t is not None else '0.99 이상에서도 목표 거부율 미달 -> 음성 이미지를 학습에 추가 필요'} "
              f"(음성 거부율 {a.target_reject:.0%} 목표, 정답수용률과 균형은 직접 판단)")
        if a.synthetic_negatives and not any(f for f in glob.glob(os.path.join(a.data, "_negative", "*"))):
            print("※ 음성이 합성 배경뿐이라 실제 오탐률을 과소평가한다. 실제 사진을 eval_real/_negative/ 에 넣을 것.")
    else:
        print("음성 이미지가 없어 거부율은 계산하지 않았다 (eval_real/_negative/ 또는 --synthetic-negatives).")

    with open(a.report, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "true", "pred", "conf", "top1_ok", "top5_ok", "ok_adjusted"])
        w.writerows(rows)
    print(f"\n이미지별 결과: {a.report}")
    shutil.rmtree(paths.TMP / "eval_syn_neg", ignore_errors=True)


if __name__ == "__main__":
    main()
