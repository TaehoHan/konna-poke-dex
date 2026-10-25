"""① 종 모델 평가: dataset_cls 의 test(또는 val) 분할을 ONNX(fp32, CPU)로 돌려 나눠 보고한다.

test 분할은 학습과 **같은 분포**다 (같은 출처, 같은 합성 방식). 실제 카메라 성능이 아니다.
그래서 한 숫자로 말하지 않고 나눠서 본다:
  - 출처별(serebii / hf-jjmack / kg-1000), 이미지 종류별(art / render / sprite / anime / mixed), 뒷모습, 폼 이미지, 작은 이미지
  - 진화 계열 혼동: top-1 오답 중 정답과 같은 진화 계열(pokedex-ko.json 의 evolution)로 틀린 비율
  - none: 포켓몬이 아닌 이미지를 none 으로 거부한 비율, 포켓몬을 none 으로 잘못 거부한 비율
  - 확신도 임계값 표: 임계값마다 받아들이는 비율과 받아들인 것의 정확도, none 이미지를 잘못 받아들이는 비율
  - 자주 틀리는 쌍

클래스 이름은 ONNX 메타데이터(ultralytics 가 넣는 names)에서 읽는다. none 인덱스를 하드코딩하지 않는다.
전처리는 ultralytics classify 와 같다: RGB, 224 로 맞춤(이미 224), /255, NCHW.
GPU 학습과 겹치지 않게 CPU 로 돈다.

사용:
  .venv/bin/python scripts/eval/eval-test.py --run train_v1            # test 분할
  .venv/bin/python scripts/eval/eval-test.py --run train_v1 --split val
출력: workspace/logs/eval_<run>_<split>.json (요약), eval_<run>_<split>.csv (이미지별 예측)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/ (paths.py)
import paths  # noqa: E402
import argparse
import ast
import collections
import csv
import json

import cv2
import numpy as np
import onnxruntime as ort

NONE = "none"
THRESHOLDS = [0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]


def load_names(sess):
    meta = sess.get_modelmeta().custom_metadata_map
    names = ast.literal_eval(meta["names"])          # {0: 'abomasnow', ...}
    return [names[i] for i in range(len(names))]


def preprocess(path, size):
    img = cv2.imread(str(path))
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    if img.shape[:2] != (size, size):
        img = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
    return (img.astype(np.float32) / 255.0).transpose(2, 0, 1)


def softmax(x):
    e = np.exp(x - x.max(1, keepdims=True))
    return e / e.sum(1, keepdims=True)


def acc_table(rows, key):
    g = collections.defaultdict(list)
    for r in rows:
        g[r[key] or "-"].append(r)
    out = {}
    for k, v in sorted(g.items()):
        out[k] = {"n": len(v), "top1": round(sum(r["ok1"] for r in v) / len(v), 4),
                  "top5": round(sum(r["ok5"] for r in v) / len(v), 4)}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default=paths.RUN_NAME)
    ap.add_argument("--onnx", help="기본: workspace/models/<run>/weights/best.onnx")
    ap.add_argument("--split", default="test", choices=["test", "val"])
    ap.add_argument("--batch", type=int, default=64)
    a = ap.parse_args()

    onnx_path = Path(a.onnx) if a.onnx else paths.MODELS / a.run / "weights" / "best.onnx"
    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    names = load_names(sess)
    idx = {n: i for i, n in enumerate(names)}
    inp = sess.get_inputs()[0]
    size = inp.shape[-1]
    if isinstance(inp.shape[0], int):   # 기기용 ONNX 는 배치 1 고정 shape 다
        a.batch = inp.shape[0]
    dex = json.loads(paths.POKEDEX_KO.read_text(encoding="utf-8"))
    family = {c: set(d.get("evolution") or [c]) for c, d in dex.items()}

    with (paths.DATASET_CLS / "manifest.csv").open(encoding="utf-8", newline="") as f:
        items = [r for r in csv.DictReader(f) if r["split"] == a.split]
    missing = sorted({r["class_id"] for r in items} - set(names))
    if missing:
        sys.exit(f"모델에 없는 클래스가 데이터에 있다: {missing[:10]}")

    rows = []
    for s in range(0, len(items), a.batch):
        chunk = items[s:s + a.batch]
        x = np.stack([preprocess(paths.DATASET_CLS / r["file"], size) for r in chunk])
        out = sess.run(None, {inp.name: x})[0]
        prob = out if np.allclose(out.sum(1), 1, atol=1e-3) else softmax(out)   # ultralytics 분류 ONNX 는 softmax 포함
        top5 = np.argsort(-prob, 1)[:, :5]
        for r, p, t5 in zip(chunk, prob, top5):
            gt = r["class_id"]
            pred = names[t5[0]]
            rows.append({**r, "pred": pred, "conf": float(p[t5[0]]), "top5": [names[i] for i in t5],
                         "ok1": int(pred == gt), "ok5": int(gt in [names[i] for i in t5]),
                         "same_family": int(pred != gt and gt != NONE and pred in family.get(gt, ()))})

    poke = [r for r in rows if r["class_id"] != NONE]
    nonep = [r for r in rows if r["class_id"] == NONE]
    wrong = [r for r in poke if not r["ok1"]]
    wrong_poke = [r for r in wrong if r["pred"] != NONE]
    for r in poke:
        r["is_form"] = "form" if r["form"] else "base"
    summary = {
        "run": a.run, "split": a.split, "onnx": str(onnx_path), "images": len(rows),
        "note": "같은 분포의 test 분할이다. 실제 카메라 성능이 아니다.",
        "pokemon": {"n": len(poke), "top1": round(sum(r["ok1"] for r in poke) / max(1, len(poke)), 4),
                    "top5": round(sum(r["ok5"] for r in poke) / max(1, len(poke)), 4),
                    "classes_evaluated": len({r["class_id"] for r in poke}), "classes_total": len(names) - 1},
        "by_source": acc_table(poke, "source_id"),
        "by_image_type": acc_table(poke, "image_type"),
        "by_view": acc_table(poke, "view"),
        "by_form": acc_table(poke, "is_form"),
        "by_small": acc_table(poke, "small"),
        "errors": {
            "wrong": len(wrong),
            "predicted_none": sum(r["pred"] == NONE for r in wrong),
            "same_evolution_family": sum(r["same_family"] for r in wrong_poke),
            "same_family_ratio_of_wrong_species": round(sum(r["same_family"] for r in wrong_poke) / max(1, len(wrong_poke)), 4),
        },
        "none": {"n": len(nonep), "rejected": round(sum(r["pred"] == NONE for r in nonep) / max(1, len(nonep)), 4)},
        "thresholds": [],
        "top_confusions": [],
    }
    for t in THRESHOLDS:
        acc_p = [r for r in poke if r["pred"] != NONE and r["conf"] >= t]
        fa = [r for r in nonep if r["pred"] != NONE and r["conf"] >= t]
        summary["thresholds"].append({
            "threshold": t,
            "pokemon_accepted": round(len(acc_p) / max(1, len(poke)), 4),
            "accuracy_of_accepted": round(sum(r["ok1"] for r in acc_p) / max(1, len(acc_p)), 4),
            "none_false_accept": round(len(fa) / max(1, len(nonep)), 4)})
    conf = collections.Counter((r["class_id"], r["pred"]) for r in wrong)
    summary["top_confusions"] = [{"gt": g, "pred": p, "n": n, "same_family": int(p in family.get(g, ()))}
                                 for (g, p), n in conf.most_common(25)]

    out_json = paths.LOGS / f"eval_{a.run}_{a.split}.json"
    out_csv = paths.LOGS / f"eval_{a.run}_{a.split}.csv"
    paths.LOGS.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        fields = ["file", "class_id", "pred", "conf", "ok1", "ok5", "same_family", "source_id", "image_type", "view", "form", "small"]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    p = summary["pokemon"]
    print(f"[{a.run} / {a.split}] 같은 분포 test — 실제 카메라 성능 아님")
    print(f"포켓몬 {p['n']}장 ({p['classes_evaluated']}/{p['classes_total']}종): top1 {p['top1']:.3f}, top5 {p['top5']:.3f}")
    for k in ("by_source", "by_image_type", "by_view", "by_form", "by_small"):
        print(f"  {k}: " + ", ".join(f"{n}={v['top1']:.3f}({v['n']})" for n, v in summary[k].items()))
    e = summary["errors"]
    print(f"  오답 {e['wrong']} 중 none 으로 거부 {e['predicted_none']}, 다른 종 오답 중 같은 진화 계열 {e['same_family_ratio_of_wrong_species']:.1%}")
    print(f"none {summary['none']['n']}장: none 으로 거부 {summary['none']['rejected']:.1%}")
    print("임계값  포켓몬수용  수용정확도  none오수용")
    for t in summary["thresholds"]:
        print(f"  {t['threshold']:.1f}    {t['pokemon_accepted']:.3f}     {t['accuracy_of_accepted']:.3f}     {t['none_false_accept']:.3f}")
    print("자주 틀리는 쌍: " + ", ".join(f"{c['gt']}→{c['pred']}({c['n']}{'*' if c['same_family'] else ''})" for c in summary["top_confusions"][:12]))
    print(f"  {out_json}\n  {out_csv}")


if __name__ == "__main__":
    main()
