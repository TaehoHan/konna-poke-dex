"""② 폼 모델 평가: dataset_form 의 test(또는 val) 분할을 ONNX(fp32, CPU)로.

앱과 같은 방식(DESIGN.md 2.1)으로 잰다: ① 종 모델이 종을 맞혔다고 보고, 그 종의 클래스(기본형 + 폼)만 남겨(마스크) 고른다.
마스크 없이 전체 클래스에서 고른 정확도도 같이 낸다 (종까지 헷갈리는지 보려고).
폼 이미지는 대부분 공식 아트 1장 + 64px 도트 증강본에서 왔다. test 는 매우 적고 같은 분포다. 실제 카메라 성능이 아니다.

사용: .venv/bin/python scripts/eval/eval-form.py --run form_v1 [--split val]
출력: workspace/logs/eval_<run>_<split>.json
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


def category(cls, cats):
    return "base" if "__" not in cls else cats.get(cls, "other")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default="form_v1")
    ap.add_argument("--split", default="test", choices=["test", "val"])
    a = ap.parse_args()

    sess = ort.InferenceSession(str(paths.MODELS / a.run / "weights" / "best.onnx"), providers=["CPUExecutionProvider"])
    meta = ast.literal_eval(sess.get_modelmeta().custom_metadata_map["names"])
    names = [meta[i] for i in range(len(meta))]
    species_of = [n.split("__")[0] for n in names]
    inp = sess.get_inputs()[0]
    size = inp.shape[-1]
    with paths.FORM_LIST.open(encoding="utf-8", newline="") as f:
        cats = {r["form_class"]: r["category"] for r in csv.DictReader(f)}
    with (paths.DATASET_FORM / "manifest.csv").open(encoding="utf-8", newline="") as f:
        items = [r for r in csv.DictReader(f) if r["split"] == a.split]

    rows = []
    for r in items:
        img = cv2.cvtColor(cv2.imread(str(paths.DATASET_FORM / r["file"])), cv2.COLOR_BGR2RGB)
        x = (img.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        p = sess.run(None, {inp.name: x})[0][0]
        gt = r["class_id"]
        sp = gt.split("__")[0]
        mask = np.array([s == sp for s in species_of])
        pm = np.where(mask, p, -1)
        rows.append({"gt": gt, "species": sp, "cat": category(gt, cats), "source": r["source_id"],
                     "pred": names[int(p.argmax())], "pred_masked": names[int(pm.argmax())],
                     "n_choices": int(mask.sum())})
    for r in rows:
        r["ok"] = int(r["pred"] == r["gt"])
        r["ok_masked"] = int(r["pred_masked"] == r["gt"])
        r["ok_species"] = int(r["pred"].split("__")[0] == r["species"])

    def table(key):
        g = collections.defaultdict(list)
        for r in rows:
            g[r[key]].append(r)
        return {k: {"n": len(v), "masked": round(sum(x["ok_masked"] for x in v) / len(v), 4),
                    "unmasked": round(sum(x["ok"] for x in v) / len(v), 4)} for k, v in sorted(g.items())}

    n = max(1, len(rows))
    summary = {
        "run": a.run, "split": a.split, "images": len(rows), "classes": len(names),
        "note": "같은 분포, 폼 test 는 매우 적다. 마스크 = ① 이 종을 맞혔다고 보고 그 종의 클래스만 남긴 정확도",
        "masked_top1": round(sum(r["ok_masked"] for r in rows) / n, 4),
        "unmasked_top1": round(sum(r["ok"] for r in rows) / n, 4),
        "unmasked_species_ok": round(sum(r["ok_species"] for r in rows) / n, 4),
        "by_category": table("cat"), "by_source": table("source"),
        "confusions_masked": [{"gt": g, "pred": p, "n": c} for (g, p), c in
                              collections.Counter((r["gt"], r["pred_masked"]) for r in rows if not r["ok_masked"]).most_common(20)],
    }
    out = paths.LOGS / f"eval_{a.run}_{a.split}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[{a.run} / {a.split}] {len(rows)}장, 클래스 {len(names)} — 같은 분포, 실제 카메라 성능 아님")
    print(f"  마스크(종 안에서 폼 고르기) top1 {summary['masked_top1']:.3f}, 마스크 없음 {summary['unmasked_top1']:.3f} (종만 맞힘 {summary['unmasked_species_ok']:.3f})")
    for k in ("by_category", "by_source"):
        print(f"  {k}: " + ", ".join(f"{c}={v['masked']:.3f}({v['n']})" for c, v in summary[k].items()))
    print("  마스크 오답: " + ", ".join(f"{c['gt']}→{c['pred']}({c['n']})" for c in summary["confusions_masked"][:10]))
    print(f"  {out}")


if __name__ == "__main__":
    main()
