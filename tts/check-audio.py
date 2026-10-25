"""생성한 음성을 Whisper 로 다시 받아써서 원문과 비교한다 (발음/누락/환각 점검). .venv-tts 로 실행.

    .venv-tts/bin/python tts/check-audio.py --raw-dir tts/raw [--only id ...]
출력: 클래스별 글자 오류율(CER, 띄어쓰기·문장부호 제외), 길이, 받아쓴 문장. 오류율이 높으면 문장을 고치거나 시드를 바꿔 다시 합성한다.
주의: 받아쓰기도 틀릴 수 있어서 '의심 목록'을 뽑는 용도다. 음색·억양은 직접 들어야 한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))   # scripts/ (paths.py)
import paths  # noqa: E402
import argparse
import json
import os
import re
import subprocess
import tempfile

ap = argparse.ArgumentParser()
ap.add_argument("--raw-dir", default="tts/raw")
ap.add_argument("--text-json", default="data/tts_text.json")
ap.add_argument("--stt", default=str(paths.STT_MODEL))   # 드라이브 Models/ 공용 모델
ap.add_argument("--only", nargs="*", default=None)
ap.add_argument("--report", default="tts/check_report.json")
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


from mlx_audio.stt.utils import load_model  # noqa: E402

model = load_model(a.stt)
items = json.load(open(a.text_json, encoding="utf-8"))
if a.only:
    items = [x for x in items if x["class_id"] in a.only]
rep = []
for it in items:
    p = os.path.join(a.raw_dir, f"{it['class_id']}.wav")
    if not os.path.exists(p):
        continue
    res = model.generate(p, language="ko")
    hyp = res.text.strip()
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p]).decode().strip())
    e = cer(it["text"], hyp)
    rep.append({"class_id": it["class_id"], "cer": round(e, 3), "sec": round(dur, 1), "hyp": hyp, "ref": it["text"]})
    print(f"{it['class_id']:16s} CER {e:5.2f}  {dur:5.1f}s  {hyp}", flush=True)
json.dump(rep, open(a.report, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
if rep:
    import statistics as st
    print(f"\n평균 CER {st.mean(r['cer'] for r in rep):.3f}, 0.15 초과 {sum(r['cer'] > 0.15 for r in rep)}개, 길이 {min(r['sec'] for r in rep):.1f}~{max(r['sec'] for r in rep):.1f}s")
