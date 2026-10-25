"""앱을 빌드한다: 에셋 → iOS release (+ 아이폰 설치) → 안드로이드 APK (보관).

사용자 요청(2026-10-25): 빌드할 때마다 APK 도 만들어 보관해 두고 나중에 안드로이드에도 넣는다.

  .venv/bin/python scripts/deploy/build-app.py                      # 에셋 + iOS + APK
  .venv/bin/python scripts/deploy/build-app.py --install-ios <UDID>  # + 아이폰 설치·실행 (xcrun devicectl)
  .venv/bin/python scripts/deploy/build-app.py --skip-assets --skip-ios

APK 는 paths.APP_BUILDS/konnadex_<YYYYmmdd_HHMM>.apk 로 복사하고 최근 --keep 개(기본 3)만 남긴다 (한 개 약 250MB).
안드로이드에 넣기: adb install -r <apk>  (USB 디버깅 켠 폰)
같은 app/ 폴더를 다른 세션과 같이 쓰면 빌드를 동시에 돌리지 않는다 (build/·.dart_tool 공유).
"""
import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/ (paths.py)
import paths  # noqa: E402


def run(cmd, cwd=None, log=None):
    print("$", " ".join(map(str, cmd)), flush=True)
    if log:
        with open(log, "w") as f:
            r = subprocess.run(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT)
        tail = Path(log).read_text(errors="ignore").strip().splitlines()[-3:]
        print("  " + "\n  ".join(tail))
    else:
        r = subprocess.run(cmd, cwd=cwd)
    if r.returncode != 0:
        raise SystemExit(f"실패: {' '.join(map(str, cmd))} (로그 {log})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-assets", action="store_true")
    ap.add_argument("--skip-ios", action="store_true")
    ap.add_argument("--skip-apk", action="store_true")
    ap.add_argument("--install-ios", metavar="DEVICE", help="빌드 뒤 이 아이폰(UDID/이름)에 설치·실행")
    ap.add_argument("--keep", type=int, default=3, help="보관할 APK 개수")
    args = ap.parse_args()
    paths.LOGS.mkdir(parents=True, exist_ok=True)
    if not args.skip_assets:
        run([sys.executable, str(paths.SCRIPTS / "deploy" / "build-app-assets.py")])
    if not args.skip_ios:
        run(["flutter", "build", "ios", "--release"], cwd=paths.APP, log=paths.LOGS / "app-build-ios-release.log")
        if args.install_ios:
            app = paths.APP / "build" / "ios" / "iphoneos" / "Runner.app"
            run(["xcrun", "devicectl", "device", "install", "app", "--device", args.install_ios, str(app)])
            r = subprocess.run(["xcrun", "devicectl", "device", "process", "launch", "--terminate-existing",
                                "--device", args.install_ios, paths.APP_BUNDLE_ID], capture_output=True, text=True)
            print("  실행:", "성공" if r.returncode == 0 else "실패 (폰이 잠겨 있으면 직접 열기)")
    if not args.skip_apk:
        run(["flutter", "build", "apk", "--release"], cwd=paths.APP, log=paths.LOGS / "app-build-apk-release.log")
        apk = paths.APP / "build" / "app" / "outputs" / "flutter-apk" / "app-release.apk"
        paths.APP_BUILDS.mkdir(parents=True, exist_ok=True)
        dst = paths.APP_BUILDS / f"konnadex_{datetime.now():%Y%m%d_%H%M}.apk"
        shutil.copy2(apk, dst)
        old = sorted(paths.APP_BUILDS.glob("konnadex_*.apk"))[:-args.keep]
        for o in old:
            o.unlink()
        print(f"APK 보관: {dst} ({dst.stat().st_size / 1e6:.0f}MB), 지운 옛 APK {len(old)}개")


if __name__ == "__main__":
    main()
