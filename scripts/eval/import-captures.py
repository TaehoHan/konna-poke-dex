"""폰 앱(진단 보기의 "포켓몬 아님"/"정답 지정")에서 저장한 실제 촬영을 맥으로 꺼내 workspace/eval_real/<라벨>/ 에 넣는다.

앱은 Documents/captures/<라벨>/<시각>_{crop.png, frame.jpg, .json} 로 저장한다 (라벨: none 또는 class_id).
  crop.png   모델에 들어간 입력 그대로 (224)   frame.jpg  원본 프레임   .json  시간·상위 3개·확률·임계값 등

꺼내는 방법 (둘 중 하나):
  1) 이 스크립트가 xcrun devicectl 로 앱 데이터에서 바로 복사 (폰이 USB/같은 Wi-Fi 로 페어링돼 있어야 함)
       .venv/bin/python scripts/eval/import-captures.py --device <UDID 또는 이름>
  2) 파일 앱(나의 iPhone > 포켓몬 도감 > captures) 이나 Finder(아이폰 > 파일 > 포켓몬 도감)에서 captures 폴더를
     맥으로 복사한 뒤
       .venv/bin/python scripts/eval/import-captures.py --from <복사한 captures 폴더>

eval_real 은 학습·합성·none 풀에 넣지 않는다 (CLAUDE.md). 이미 있는 파일은 건너뛴다. 이미지는 git 에 넣지 않는다 (workspace/ 는 git 제외).
"""
import argparse
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/ (paths.py)
import paths  # noqa: E402


def pull(device):
    dst = paths.CAPTURES_IN
    shutil.rmtree(dst, ignore_errors=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["xcrun", "devicectl", "device", "copy", "from", "--device", device,
                    "--domain-type", "appDataContainer", "--domain-identifier", paths.APP_BUNDLE_ID,
                    "--source", "Documents/captures", "--destination", str(dst)], check=False)
    if not dst.exists():
        raise SystemExit("폰에서 꺼내지 못했다. 앱에 저장한 촬영이 없거나(빈 폴더는 복사되지 않는다) 폰이 잠겨 있다/연결이 끊겼다.")
    return dst


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--device", help="xcrun devicectl 의 기기 이름·UDID")
    g.add_argument("--from", dest="src", help="폰에서 복사해 온 captures 폴더")
    args = ap.parse_args()
    src = pull(args.device) if args.device else Path(args.src).expanduser()
    if not src.exists():
        raise SystemExit(f"{src} 가 없다")
    n = Counter()
    for f in sorted(src.rglob("*")):
        if not f.is_file() or f.name.startswith("."):
            continue
        label = f.parent.name
        out = paths.EVAL_REAL / label / f.name
        if out.exists():
            n["skip"] += 1
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, out)
        if f.suffix == ".json":
            n[label] += 1
    print(f"새로 넣은 촬영 {sum(v for k, v in n.items() if k != 'skip')}건 {dict((k, v) for k, v in n.items() if k != 'skip')} "
          f"| 건너뜀(이미 있음) 파일 {n['skip']} → {paths.EVAL_REAL}")


if __name__ == "__main__":
    main()
