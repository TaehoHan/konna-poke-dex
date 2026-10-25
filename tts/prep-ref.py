"""가지고 있는 음성/영상 파일에서 제로샷 클로닝용 레퍼런스 클립(24kHz 모노 wav)을 만든다.

이 스크립트는 **이미 가지고 있는 파일**을 자르고 정리하기만 한다. 파일을 내려받거나 추출해 오지 않는다.
개인 기기용으로만 쓰고, 만든 음성(레퍼런스·생성물)은 공개·배포하지 않는다.

사용:
    python tts/prep-ref.py --src my_clip.mp4 --start 12.5 --end 24 --out tts/ref/narr.wav
    python tts/prep-ref.py --src my_clip.mp4 --start 12.5 --end 24 --denoise      # 가벼운 잡음 제거
    python tts/prep-ref.py --src my_clip.mp4 --start 12.5 --end 24 --vocals --demucs-python <demucs 가 깔린 python>   # 배경음악 분리

--vocals: demucs(htdemucs)로 목소리만 남긴다. 모델은 드라이브 Models/hf-hub(paths.HF_HUB_SHARED)에서 오프라인으로 읽는다
          (2026-10-25 김영선 도감 낭독 레퍼런스: 배경음악·효과음이 계속 깔려 afftdn 으로는 안 지워져서 썼다).
          demucs 는 torch 가 필요해 프로젝트 가상환경에 넣지 않았다. 임시 가상환경에 `pip install demucs soundfile` 하고
          그 python 을 --demucs-python 으로 넘긴다. 44.1kHz 스테레오 원본으로 분리한 뒤 자르고 24kHz 모노로 바꾼다.

좋은 레퍼런스 조건:
  - 한 사람의 목소리만, 5~15초 (너무 길면 오히려 불안정한 모델이 많다), 말이 끊기지 않는 구간
  - 배경음악·효과음·겹치는 대사가 없을 것 (있으면 --vocals)
  - 감정이 과하지 않은 평범한 대사. 그 대사의 **정확한 한글 텍스트**를 같이 적어 둘 것 (일부 모델은 필요)
  - 출력 파일 옆에 <이름>.txt 로 그 대사를 저장해 두면 이후 단계가 자동으로 읽는다 (--text 로 지정)
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))   # scripts/ (paths.py)
import paths  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--src", required=True, help="가지고 있는 음성/영상 파일")
ap.add_argument("--start", type=float, default=0.0, help="시작(초)")
ap.add_argument("--end", type=float, default=None, help="끝(초). 생략하면 끝까지")
ap.add_argument("--out", default="tts/ref/ref.wav")
ap.add_argument("--text", default=None, help="이 구간의 대사 (out 과 같은 이름의 .txt 로 저장)")
ap.add_argument("--sr", type=int, default=24000)
ap.add_argument("--denoise", action="store_true", help="ffmpeg afftdn 으로 가벼운 잡음 제거")
ap.add_argument("--vocals", action="store_true", help="demucs 로 보컬만 분리 (모델은 드라이브 paths.HF_HUB_SHARED, 오프라인)")
ap.add_argument("--demucs-python", default=sys.executable, help="demucs 가 설치된 python (기본: 이 python)")
a = ap.parse_args()

if not shutil.which("ffmpeg"):
    sys.exit("ffmpeg 가 필요하다 (brew install ffmpeg)")
os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
tmp = tempfile.mkdtemp(prefix="ref_")

# 1) (선택) 원본 전체에서 목소리 분리 → 2) 구간 자르기 + 모노 + 샘플레이트
src_full = a.src
if a.vocals:
    full = os.path.join(tmp, "full.wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", a.src, "-vn", "-ac", "2", "-ar", "44100", full], check=True)
    if not paths.HF_HUB_SHARED.exists():
        sys.exit(f"demucs 모델 폴더가 없다: {paths.HF_HUB_SHARED} (구글 드라이브 Models/hf-hub)")
    env = dict(os.environ, HF_HUB_CACHE=str(paths.HF_HUB_SHARED), HF_HUB_OFFLINE="1")
    try:
        subprocess.run([a.demucs_python, "-m", "demucs", "-n", "htdemucs", "--two-stems=vocals", "-o", tmp, full],
                       check=True, env=env)
    except Exception as e:
        sys.exit(f"demucs 실패: {e}\n(--demucs-python 에 demucs 가 설치된 python 을 넘긴다)")
    voc = [os.path.join(r, f) for r, _, fs in os.walk(tmp) for f in fs if f == "vocals.wav"]
    if not voc:
        sys.exit("vocals.wav 를 찾지 못했다")
    src_full = voc[0]

cut = os.path.join(tmp, "cut.wav")
cmd = ["ffmpeg", "-y", "-v", "error", "-ss", str(a.start)]
if a.end is not None:
    cmd += ["-to", str(a.end)]
cmd += ["-i", src_full, "-vn", "-ac", "1", "-ar", str(a.sr), cut]
subprocess.run(cmd, check=True)
src = cut

# 3) 잡음 제거(선택) + 앞뒤 무음 제거 + 음량 정규화(-20 LUFS)
filters = []
if a.denoise:
    filters.append("afftdn=nf=-25")
# 앞뒤 무음 제거: 앞을 자른 뒤 뒤집어서 다시 앞을 자르고 되돌린다 (stop_periods 방식은 짧은 클립을 통째로 지울 수 있다)
filters += ["silenceremove=start_periods=1:start_threshold=-45dB",
            "areverse", "silenceremove=start_periods=1:start_threshold=-45dB", "areverse",
            "loudnorm=I=-20:TP=-2:LRA=7"]
subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-af", ",".join(filters), "-ac", "1", "-ar", str(a.sr), "-sample_fmt", "s16", a.out], check=True)

dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", a.out]).decode().strip())
print(f"저장: {a.out} ({dur:.1f}초, {a.sr}Hz 모노)")
if dur < 3 or dur > 20:
    print("※ 권장 길이는 5~15초다. 너무 짧거나 길면 품질이 떨어질 수 있다.")
if a.text:
    open(os.path.splitext(a.out)[0] + ".txt", "w", encoding="utf-8").write(a.text.strip() + "\n")
    print("대사 텍스트 저장")
shutil.rmtree(tmp, ignore_errors=True)
