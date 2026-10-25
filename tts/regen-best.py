"""오류율이 높은 문장을 시드를 바꿔 다시 합성하고, 받아쓰기(STT) 오류율이 가장 낮은 것을 채택한다. .venv-tts 로 실행.

    .venv-tts/bin/python tts/regen-best.py --min-cer 0.08 --tries 3

입력: tts/check_report.json (check-audio.py 의 결과). 개선된 경우에만 tts/raw/<id>.wav 를 바꾸고 보고서를 갱신한다.
받아쓰기도 틀릴 수 있어서 '발음이 더 정확한 쪽'을 고르는 근사치다. 음색은 직접 들어야 한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))   # scripts/ (paths.py)
import paths  # noqa: E402
import argparse
import json
import os
import re
import shutil
import time

import mlx.core as mx
from mlx_audio.stt.utils import load_model as load_stt
from mlx_audio.tts.generate import generate_audio
from mlx_audio.tts.utils import load_model as load_tts

ap = argparse.ArgumentParser()
ap.add_argument("--report", default="tts/check_report.json")
ap.add_argument("--raw-dir", default="tts/raw")
ap.add_argument("--try-dir", default="tts/raw_try")
ap.add_argument("--text-json", default="data/tts_text.json")
ap.add_argument("--ref-audio", default="tts/ref/narr_clean.wav")
ap.add_argument("--ref-text-file", default="tts/ref/narr.txt")
ap.add_argument("--tts", default=str(paths.TTS_MODEL))   # 드라이브 Models/ 공용 모델
ap.add_argument("--stt", default=str(paths.STT_MODEL))
ap.add_argument("--min-cer", type=float, default=0.08)
ap.add_argument("--tries", type=int, default=3)
ap.add_argument("--temperature", type=float, default=None, help="qwen3_synth.py 와 같은 값을 준다 (konna: 0.5)")
a = ap.parse_args()


def norm(s):
    return re.sub(r"[^가-힣a-zA-Z0-9]", "", s)


def cer(ref, hyp):
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hc in enumerate(h, 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (rc != hc))
            prev = cur
    return d[len(h)] / max(1, len(r))


report = json.load(open(a.report, encoding="utf-8"))
by_id = {r["class_id"]: r for r in report}
texts = {x["class_id"]: x["text"] for x in json.load(open(a.text_json, encoding="utf-8"))}
targets = sorted([r for r in report if r["cer"] >= a.min_cer], key=lambda r: -r["cer"])
print(f"재생성 대상 {len(targets)}개 (CER >= {a.min_cer}), 각 {a.tries}회 시도", flush=True)

ref_text = open(a.ref_text_file, encoding="utf-8").read().strip()
tts = load_tts(a.tts)
stt = load_stt(a.stt)
os.makedirs(a.try_dir, exist_ok=True)

improved = 0
for t in targets:
    cid = t["class_id"]
    old_cer = t["cer"]
    best = (t["cer"], None, t["hyp"])
    for seed in range(1, a.tries + 1):
        mx.random.seed(seed)
        pre = f"{cid}_s{seed}"
        generate_audio(text=texts[cid], model=tts, ref_audio=a.ref_audio, ref_text=ref_text, lang_code="ko",
                       output_path=a.try_dir, file_prefix=pre, audio_format="wav", verbose=False,
                       **({"temperature": a.temperature} if a.temperature is not None else {}))
        f = [x for x in os.listdir(a.try_dir) if x.startswith(pre) and x.endswith(".wav")]
        if not f:
            continue
        p = os.path.join(a.try_dir, sorted(f)[0])
        hyp = stt.generate(p, language="ko").text.strip()
        c = cer(texts[cid], hyp)
        if c < best[0]:
            best = (c, p, hyp)
    if best[1]:
        shutil.copy(best[1], os.path.join(a.raw_dir, f"{cid}.wav"))
        by_id[cid].update(cer=round(best[0], 3), hyp=best[2])
        improved += 1
        print(f"{cid:16s} {old_cer:.2f} -> {best[0]:.2f}  개선", flush=True)
    else:
        print(f"{cid:16s} {old_cer:.2f} 유지 (모든 시도가 더 낫지 않음)", flush=True)

json.dump(list(by_id.values()), open(a.report, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
shutil.rmtree(a.try_dir, ignore_errors=True)
import statistics as st
print(f"\n개선 {improved}/{len(targets)}개, 전체 평균 CER {st.mean(r['cer'] for r in by_id.values()):.3f}")
