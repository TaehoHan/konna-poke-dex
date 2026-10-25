"""안내 문장(data/tts_text.json) -> 클래스별 음성 파일(audio/<class_id>.wav) 일괄 생성.

엔진은 교체할 수 있다. 어떤 엔진이든 마지막에 같은 후처리를 거쳐 보드용 포맷으로 맞춘다:
  16-bit PCM wav, 모노, 기본 16kHz, 앞뒤 무음 제거, 음량 -16 LUFS.

사용:
    python tts/build-audio.py --engine say                       # 임시: 맥 내장 한국어 음성(Yuna). 파이프라인 시험용
    python tts/build-audio.py --engine say --only s1_baroping kikiping
    python tts/build-audio.py --engine cmd --cmd 'python my_tts.py --text {text} --out {out}'   # 임의의 외부 명령
출력: workspace/audio/<class_id>.wav, workspace/audio/manifest.json (class_id, 이름, 문장, 길이, 크기)

주의:
  - 목소리 클로닝 엔진은 레퍼런스(tts/ref/*.wav)가 필요하다. 레퍼런스·생성 음성은 개인 기기용으로만 쓰고 공개·배포하지 않는다.
  - 문장은 data/pokedex-ko.json(PokeAPI, 도감 설명은 게임 원문)에서 만든다. 생성 음성과 문장 파일은 개인 기기용, 배포 금지.
"""
import argparse
import wave
import json
import os
import shlex
import subprocess
import sys
import tempfile

ap = argparse.ArgumentParser()
ap.add_argument("--text-json", default="data/tts_text.json")
ap.add_argument("--out-dir", default="workspace/audio")
ap.add_argument("--engine", default="say", choices=["say", "cmd", "dir"])
ap.add_argument("--voice", default="Yuna", help="--engine say 의 음성 이름")
ap.add_argument("--cmd", default=None, help="--engine cmd: 명령 템플릿. {text} {out} 치환. 결과는 wav/aiff/mp3 아무거나")
ap.add_argument("--sr", type=int, default=16000, help="출력 샘플레이트 (보드 재생용)")
ap.add_argument("--lufs", type=float, default=-16.0)
ap.add_argument("--raw-dir", default="tts/raw", help="--engine dir: 이미 합성된 원음 폴더(<class_id>.wav)")
ap.add_argument("--no-voice-trim", action="store_true", help="앞뒤 비음성 구간(숨소리·쉬익거림) 자르기를 끈다")
ap.add_argument("--no-clean", action="store_true", help="잡음 제거(고역 통과 + FFT 노이즈 감소 + 페이드)를 끈다")
ap.add_argument("--only", nargs="*", default=None, help="이 class_id 만 생성")
ap.add_argument("--force", action="store_true", help="이미 있어도 다시 생성")
a = ap.parse_args()

items = json.load(open(a.text_json, encoding="utf-8"))
if a.only:
    items = [x for x in items if x["class_id"] in a.only]
os.makedirs(a.out_dir, exist_ok=True)


def synth(text, out):
    if a.engine == "say":
        subprocess.run(["say", "-v", a.voice, "-o", out, text], check=True)
    elif a.engine == "dir":
        raise RuntimeError("dir 엔진은 synth() 를 거치지 않는다")
    else:
        if not a.cmd:
            sys.exit("--engine cmd 에는 --cmd 가 필요하다")
        cmd = a.cmd.replace("{text}", shlex.quote(text)).replace("{out}", shlex.quote(out))
        subprocess.run(cmd, shell=True, check=True)



def voiced_span(path, pre=0.05, post=0.15):
    """첫·마지막 '유성음(주기성이 있는 소리)' 프레임을 찾아 앞뒤 군더더기(숨소리·쉬익거림, 레퍼런스 앞부분 흉내)를 자를 구간을 구한다.
    반환: (시작초, 끝초). 찾지 못하면 None (그대로 둔다)."""
    import numpy as np
    w = wave.open(path)
    sr, ch = w.getframerate(), w.getnchannels()
    x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    if ch > 1:
        x = x.reshape(-1, ch).mean(1)
    fr = int(sr * 0.025)
    hop = fr // 2
    starts = list(range(0, len(x) - fr, hop))
    e = np.array([np.sqrt((x[i:i + fr] ** 2).mean()) for i in starts])
    if len(e) == 0:
        return None
    lo, hi = int(sr / 400), int(sr / 80)
    voiced = []
    for k, i in enumerate(starts):
        if e[k] < e.max() * 0.12:
            continue
        s = x[i:i + fr] - x[i:i + fr].mean()
        ac = np.correlate(s, s, "full")[fr - 1:]
        j = lo + int(np.argmax(ac[lo:hi]))
        if ac[j] / (ac[0] + 1e-9) > 0.5:
            voiced.append(k)
    if not voiced:
        return None
    t0 = max(0.0, starts[voiced[0]] / sr - pre)
    t1 = min(len(x) / sr, (starts[voiced[-1]] + fr) / sr + post)
    return t0, t1


def post(src, dst):
    trim = ("silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
            "silenceremove=start_periods=1:start_threshold=-45dB,areverse")
    # 잡음 제거: 저역 웅웅거림 제거, FFT 노이즈 감소(잡음 수준을 추적), 처음·끝 페이드(툭 끊기는 클릭 방지)
    pre = trim if a.no_clean else (trim + ",highpass=f=70,afftdn=nr=12:nf=-48:tn=1,"
                                   "afade=t=in:d=0.03,areverse,afade=t=in:d=0.05,areverse")
    # 음량: 1차로 측정하고 2차에 선형(linear)으로 맞춘다. 한 번에 하는 동적 loudnorm 은 조용한 구간(숨소리·잡음)까지 끌어올린다.
    span = None if a.no_voice_trim else voiced_span(src)
    seg = ["-ss", f"{span[0]:.3f}", "-t", f"{span[1] - span[0]:.3f}"] if span else []
    probe = subprocess.run(["ffmpeg", "-v", "info", *seg, "-i", src, "-af", f"{pre},loudnorm=I={a.lufs}:TP=-1.5:LRA=11:print_format=json",
                            "-f", "null", "-"], capture_output=True, text=True).stderr
    m = json.loads(probe[probe.rindex("{"):probe.rindex("}") + 1])
    ln = (f"loudnorm=I={a.lufs}:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    subprocess.run(["ffmpeg", "-y", "-v", "error", *seg, "-i", src, "-af", f"{pre},{ln}", "-ac", "1", "-ar", str(a.sr),
                    "-sample_fmt", "s16", dst], check=True)


def duration(p):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p]).decode().strip())


manifest = []
for it in items:
    dst = os.path.join(a.out_dir, f"{it['class_id']}.wav")
    if os.path.exists(dst) and not a.force:
        manifest.append({**it, "file": dst, "sec": round(duration(dst), 2), "bytes": os.path.getsize(dst)})
        continue
    with tempfile.TemporaryDirectory() as td:
        if a.engine == "dir":
            raw = os.path.join(a.raw_dir, f"{it['class_id']}.wav")
            if not os.path.exists(raw):
                print(f"[건너뜀] 원음 없음: {raw}")
                continue
        else:
            raw = os.path.join(td, "raw.aiff" if a.engine == "say" else "raw.wav")
            synth(it["text"], raw)
        post(raw, dst)
    d = duration(dst)
    manifest.append({**it, "file": dst, "sec": round(d, 2), "bytes": os.path.getsize(dst)})
    print(f"{it['class_id']:16s} {d:5.1f}s {os.path.getsize(dst) // 1024:4d}KB", flush=True)

json.dump(manifest, open(os.path.join(a.out_dir, "manifest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
tot = sum(m["bytes"] for m in manifest)
secs = [m["sec"] for m in manifest]
print(f"완료: {len(manifest)}개, 총 {tot / 1e6:.1f}MB, 길이 평균 {sum(secs) / len(secs):.1f}s / 최대 {max(secs):.1f}s")
