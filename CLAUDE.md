# CLAUDE.md

포켓몬 도감 프로젝트(konna-poke-dex). 카메라로 포켓몬을 인식하고 도감 항목을 해석해 보여준다.
`teeniping-dex`(`~/workspaces/experiment/teeniping-dex`)에서 포크했다. 개요는 @README.md, 설계와 결정 이유는 @DESIGN.md, 수집 설계는 @docs/COLLECTING.md, 출처 목록은 `data/sources.yaml`. 단계별 절차·함정·점검표는 `konna-poke-dex` 스킬(`.claude/skills/konna-poke-dex/`).

**현재 상태 (2026-10-25): 첫 모델 학습·평가까지.** `.venv` 는 만들었다(2026-10-24, torch MPS 확인). PokeAPI 1025종 수집 완료(`list-poke.csv`, `pokedex-ko.json`). 세레비넷 1025종 받음(드라이브 `raw/serebii`, 7,054장). HF·Kaggle 4개(jjmack, pokemon-cards, gen1-9-complete, 1000-pokemon) 받음(드라이브 `raw/`). 학습 데이터셋(`workspace/dataset_cls`)과 ① 종 모델 `train_v1`(같은 분포 test top1 0.956, DESIGN.md 6절) 있음. 실제 촬영 평가 전. `scripts/dataset/make-dataset.py`·`train/train-cls.py`·`eval/eval-test.py`·`eval/verify-onnx.py` 는 포켓몬용으로 고쳐 돌렸다. `eval/eval-real.py`·`eval/verify-stress.py`·`deploy/to-kmodel.py` 는 티니핑판 그대로이고 돌려 보지 않았다.

## 구조
- `scripts/{collect,dataset,train,eval,deploy}/` 실행 스크립트. **경로는 `scripts/paths.py` 한곳에서 관리**한다. 새 경로는 거기에 추가하고 스크립트에 경로 문자열을 직접 쓰지 않는다. (`scripts/deploy/to-kmodel.py` 만 다른 PC 로 복사해 쓰는 독립 스크립트라 paths 를 쓰지 않는다.)
- `data/` 목록·출처·라벨 매핑(git 추적), `docs/` 문서, `workspace/` 산출물(git 제외).
- **받은 데이터셋 원본(`paths.RAW`)과 HF 캐시는 구글 드라이브 `내 드라이브/Models/konna-poke-dex/`** 에 둔다 (2026-10-24 결정, 로컬 디스크 여유가 약 13GB). 받는 스크립트는 `paths.require_drive()` 를 먼저 부른다. PokeAPI 캐시(`workspace/raw/pokeapi`)만 로컬이다.
- 새 스크립트는 용도에 맞는 `scripts/<그룹>/` 에 둔다. 루트에 파일을 늘리지 않는다.

## 환경
- Python 3.12. 가상환경 `.venv`(수집·학습·평가)와 `.venv-tts`(음성 클로닝, mlx-audio, `requirements-tts.txt`)를 **따로** 둔다. 의존성을 섞지 않는다.
- 안내 음성은 **다른 목소리로 제로샷 클로닝한다** (티니핑 `tts/` 포크함, 레퍼런스 `tts/ref/narr`, DESIGN.md 2.7). TTS·Whisper 모델은 드라이브 `Models/` 의 공용 폴더(`paths.TTS_MODEL`, `paths.STT_MODEL`)를 쓰고 다시 받지 않는다. 학습과 음성 합성은 둘 다 GPU(MPS/MLX)를 써서 동시에 돌리지 않는다.
- Apple Silicon 맥(M4, 24GB). 학습은 `--device mps` 기본.
- 보통 프로젝트 루트에서 `.venv/bin/python scripts/...` 로 실행한다.
- 사전학습 가중치 `workspace/models/pretrained/yolov8n-cls.pt` 는 티니핑에서 복사해 두었다.
- **로그인이 필요한 출처는 쓰지 않는다** (2026-10-24 결정). 지금 후보인 HF·Kaggle 데이터셋은 모두 토큰 없이 받을 수 있다. 토큰이 필요해지면 사용자가 직접 설정하고, 토큰 값을 묻거나 파일에 쓰지 않는다.
- **대상은 처음부터 전국도감 1025종**이다. 1세대 전용 데이터셋은 쓰지 않는다 (`sources.yaml` 의 `skip`).

## 규칙
- **`data/list-poke.csv` 가 클래스의 단일 기준이다** (`enabled=1` 행만 클래스). 클래스 추가·삭제는 CSV 와 이미지만 바꾼다. `class_id` 는 PokeAPI 영문 이름 소문자, `-`→`_`. 폴더명 = 파일명.
- **수집 출처는 `data/sources.yaml` 한곳**. 출처 추가·제외는 yaml 만 바꾼다.
- `paths.RAW`(드라이브, 받은 그대로)와 `workspace/originals/`(공식 아트)는 **수정하지 않는다.** 정리는 `workspace/pool/` 로 하는 정규화 단계에서 한다. `pool/`, `dataset_cls/` 는 재생성 가능한 산출물이다.
- 출처를 넘는 중복이 많다. **pHash 그룹 단위로 분할**해 같은 그림이 train/test 에 걸치지 않게 한다.
- `workspace/eval_real/` 과 `use: eval` 출처는 **학습·합성·none 풀에 넣지 않는다.**
- 데이터셋 test 분할 정확도를 실제 성능으로 말하지 않는다. 출처별·이미지 종류별로 나눠 보고, 실제 성능은 직접 촬영한 이미지로만 말한다.
- 학습 `optimizer` 는 `SGD lr0=0.01` 고정, int8 정확도는 per-channel + Percentile (티니핑 DESIGN.md 4절 근거, 포켓몬에서 재확인 예정).
- `none` 클래스 인덱스를 하드코딩하지 않는다. 라벨 파일에서 찾는다.
- 새 모델은 `--name` 으로 만들고 덮어쓰지 않는다. 현재 모델은 `scripts/paths.py` 의 `RUN_NAME`.
- `scripts/deploy/to-kmodel.py` 는 **미검증**이다.

## 하지 않는다 (사용자 확인 없이는)
- **데이터셋·이미지·모델 다운로드, API 대량 호출, 크롤링** (Kaggle, Hugging Face, PokeAPI 포함). `fetch-sources.py --dry-run` 등으로 출처·용량·라이선스를 먼저 알리고 허락을 받는다.
- 게이트 데이터셋 약관 동의, Kaggle 대회 규칙 동의 (사용자가 직접 한다).
- `git commit`/`push`, 구글 드라이브에 모델 등 산출물 올리기 (허락 받은 데이터셋을 `paths.RAW` 에 받는 것은 예외). 링크 공유는 하지 않는다.
- 포켓몬 이미지, 도감 설명 원문(`data/pokedex-ko.json`), **레퍼런스 음성(클로닝 대상 목소리)과 생성한 음성**을 저장소에 추가하거나 공개·배포하는 것.
- 영상·에피소드에서 음성을 내려받거나 추출하는 것. 클로닝 레퍼런스는 사용자가 직접 마련한 파일만 정리해 쓴다.
- `workspace/` 를 이동·삭제하는 것.

## 알아둘 것
- 포크 원본(teeniping-dex)의 `workspace/bg_pool`(배경 사진)은 none 풀·합성 배경으로 복사해 쓸 수 있다 (다운로드 불필요). 티니핑 원본 이미지를 none 에 넣는 것도 검토 대상.
- 사용자는 한국어로 대화한다. 답변과 문서는 한국어로 쓴다.
