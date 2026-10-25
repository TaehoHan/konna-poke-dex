# konnadex (포켓몬 도감 앱)

카메라로 포켓몬을 비추면 종을 알아보고 한국어 도감을 보여 주는 Flutter 앱. 티니핑 도감 앱(`teeniping-dex/app`)을 포크했다.
설계는 저장소 루트 `DESIGN.md` 8절.

- 에셋(`assets/`)은 git 에 없다. 먼저 저장소 루트에서 만든다:
  `.venv/bin/python scripts/deploy/build-app-assets.py [--thumbs originals|serebii]`
- 모델이 없으면 도감만, 폼 모델이 없으면 종만 판정한다.
- `flutter run` (iOS 시뮬레이터에는 카메라가 없어 인식은 실기기에서만 볼 수 있다).
