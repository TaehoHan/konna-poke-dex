"""Qwen3-TTS(제로샷 클로닝)로 안내 문장을 합성한다. 반드시 .venv-tts 로 실행한다 (mlx-audio).

    .venv-tts/bin/python tts/qwen3_synth.py --only s1_baroping kikiping          # 시험
    .venv-tts/bin/python tts/qwen3_synth.py                                      # 전부 (없는 것만)
    .venv-tts/bin/python tts/qwen3_synth.py --seed 7 --temperature 0.7            # 샘플링 조절

출력: tts/raw/<class_id>.wav (원음, 후처리 전). 이어서
    .venv/bin/python tts/build-audio.py --engine dir --raw-dir tts/raw
로 보드용 포맷으로 변환한다.

레퍼런스(tts/ref/narr.wav + narr.txt)는 사용자가 마련한 개인용 샘플이다. 공개·배포하지 않는다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))   # scripts/ (paths.py)
import paths  # noqa: E402
import argparse
import json
import os
import time

import mlx.core as mx
from mlx_audio.tts.generate import generate_audio
from mlx_audio.tts.utils import load_model

ap = argparse.ArgumentParser()
ap.add_argument("--model", default=str(paths.TTS_MODEL))   # 드라이브 Models/ 공용 모델
ap.add_argument("--text-json", default="data/tts_text.json")
ap.add_argument("--ref-audio", default="tts/ref/narr_clean.wav")
ap.add_argument("--ref-text-file", default="tts/ref/narr.txt")
ap.add_argument("--out-dir", default="tts/raw")
ap.add_argument("--lang", default="ko")
ap.add_argument("--only", nargs="*", default=None)
ap.add_argument("--force", action="store_true")
ap.add_argument("--seed", type=int, default=0)
ap.add_argument("--temperature", type=float, default=None)
ap.add_argument("--top-p", type=float, default=None)
ap.add_argument("--speed", type=float, default=1.0)
a = ap.parse_args()

items = json.load(open(a.text_json, encoding="utf-8"))
if a.only:
    items = [x for x in items if x["class_id"] in a.only]
ref_text = open(a.ref_text_file, encoding="utf-8").read().strip()
os.makedirs(a.out_dir, exist_ok=True)

t0 = time.time()
model = load_model(a.model)
print(f"모델 로드 {time.time() - t0:.1f}s", flush=True)

for it in items:
    out = os.path.join(a.out_dir, f"{it['class_id']}.wav")
    if os.path.exists(out) and not a.force:
        continue
    mx.random.seed(a.seed)
    t1 = time.time()
    kw = {}
    if a.temperature is not None:
        kw["temperature"] = a.temperature
    if a.top_p is not None:
        kw["top_p"] = a.top_p
    generate_audio(text=it["text"], model=model, ref_audio=a.ref_audio, ref_text=ref_text, lang_code=a.lang,
                   output_path=a.out_dir, file_prefix=it["class_id"], audio_format="wav", speed=a.speed,
                   verbose=False, **kw)
    # mlx-audio 가 <prefix>_000.wav 로 저장하는 경우가 있어 정규화
    cand = [f for f in os.listdir(a.out_dir) if f.startswith(it["class_id"]) and f.endswith(".wav") and f != f"{it['class_id']}.wav"]
    if cand and not os.path.exists(out):
        os.replace(os.path.join(a.out_dir, sorted(cand)[0]), out)
    print(f"{it['class_id']:16s} {time.time() - t1:5.1f}s", flush=True)
