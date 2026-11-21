"""프로젝트 경로를 한곳에서 관리한다. 경로를 바꿀 일이 있으면 여기만 고친다.

모든 스크립트는 `import paths` 로 이 값을 쓰므로 **어느 폴더에서 실행해도** 된다.
teeniping-dex 의 paths.py 에서 포크했다. 가져온 스크립트(dataset/train/eval)가 쓰는 이름은 그대로 유지한다.

구조:
  data/       클래스 목록 CSV, 수집 출처(sources.yaml), 라벨 매핑 (git 추적)
  docs/       문서
  scripts/    실행 스크립트 (collect, dataset, train, eval, deploy)
  workspace/  용량 큰 산출물 (git 제외): pool, originals, bg_pool, eval_real, dataset_cls, models, logs, PokeAPI 캐시
  DRIVE/      구글 드라이브 `내 드라이브/Models/konna-poke-dex/` : 받은 데이터셋 원본(raw). 로컬 STAGING 에 받은 뒤 한 번에 옮긴다

  공용 모델   구글 드라이브 `내 드라이브/Models/` 바로 밑 (티니핑과 같이 쓴다): Qwen3-TTS, Whisper(mlx)

DRIVE 는 환경변수 KONNA_DRIVE 로 바꿀 수 있다. 없으면 ~/Library/CloudStorage/GoogleDrive-*/내 드라이브/Models/konna-poke-dex 를 찾는다.
데이터셋을 받는 스크립트는 require_drive() 를 먼저 불러서, 드라이브가 없을 때 로컬 디스크에 몰래 받지 않게 한다.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DOCS = ROOT / "docs"
SCRIPTS = ROOT / "scripts"
WORK = ROOT / "workspace"


def _find_drive():
    env = os.environ.get("KONNA_DRIVE")
    if env:
        return Path(env).expanduser()
    for d in sorted(Path.home().glob("Library/CloudStorage/GoogleDrive-*")):
        cand = d / "내 드라이브" / "Models" / "konna-poke-dex"
        if cand.exists():
            return cand
    return None


DRIVE = _find_drive()                        # 없으면 None


def require_drive():
    """구글 드라이브 폴더가 있어야 하는 스크립트(데이터셋 받기)가 처음에 부른다."""
    if DRIVE is None or not DRIVE.exists():
        raise SystemExit("구글 드라이브 폴더를 찾지 못했다: 내 드라이브/Models/konna-poke-dex (또는 KONNA_DRIVE 환경변수).\n"
                         "로컬 디스크 여유가 적어 데이터셋은 드라이브에만 받는다.")
    return DRIVE


# 공용 모델 (구글 드라이브 Models/ 바로 밑, 티니핑과 같이 쓴다. 환경변수 KONNA_SHARED_MODELS 로 바꿀 수 있다)
SHARED_MODELS = Path(os.environ["KONNA_SHARED_MODELS"]).expanduser() if os.environ.get("KONNA_SHARED_MODELS") \
    else (DRIVE.parent if DRIVE else WORK / "_drive_missing")
TTS_MODEL = SHARED_MODELS / "Qwen3-TTS-12Hz-1.7B-Base-bf16"   # mlx-audio 음성 클로닝 (4.2GB)
STT_MODEL = SHARED_MODELS / "whisper-large-v3-turbo-mlx"      # mlx-audio 받아쓰기 검수 (1.5GB)
HF_HUB_SHARED = SHARED_MODELS / "hf-hub"   # 허깅페이스 캐시 구조(심볼릭 링크 없이 실제 파일)로 둔 공용 모델. HF_HUB_CACHE 로 가리킨다
                                           # 지금: models--adefossez--HTDemucs (demucs 목소리 분리, 84MB, tts/prep-ref.py --vocals)

# 목록/데이터 (git 추적)
CSV_MAIN = DATA / "list-poke.csv"            # 클래스 목록 (단일 기준, enabled=1 행만 클래스)
SOURCES = DATA / "sources.yaml"              # 수집 출처 목록 (Kaggle, Hugging Face, PokeAPI)
LABEL_MAP = DATA / "label-map"               # 출처별 라벨 → class_id 매핑: <source_id>.csv
POOL_REJECTS = DATA / "pool-rejects.csv"     # 검수에서 뺀 이미지 기록
POKEDEX_KO = DATA / "pokedex-ko.json"        # 한국어 도감 해석 DB (git 제외 기본)
FORM_LIST = DATA / "form-list.csv"            # ② 폼 모델 클래스 목록 (scripts/dataset/make-form-list.py, include 열은 사람이 고칠 수 있다)
TTS_TEXT = DATA / "tts_text.json"            # 안내 음성 문장 (종당 1개, git 제외: 도감 설명 원문 포함). tts/make-text.py 가 만든다
FLAVOR_KO_TRANSLATED = DATA / "flavor-ko-translated.json"   # 한국어 설명이 없는 종(SV·레전드 아르세우스 전용 127종)의 영어 설명 번역 (git 제외)

# 수집 (git 제외)
_DRIVE_OR_UNSET = DRIVE if DRIVE else WORK / "_drive_missing"   # 드라이브가 없으면 require_drive() 가 막는다
RAW = _DRIVE_OR_UNSET / "raw"                # 받은 데이터셋 그대로: <source_id>/ (수정 금지, 구글 드라이브)
HF_CACHE = WORK / ".hf_cache"                # HF_HOME (로컬, 받는 동안만 쓴다)
POKEAPI_CACHE = WORK / "raw" / "pokeapi"     # PokeAPI 응답 캐시 (로컬, 191MB): <endpoint>/<key>.json (재요청 금지)
POOL = WORK / "pool"                         # 정규화된 실제 이미지: <class_id>/<source_id>__<phash>.jpg
POOL_MANIFEST = POOL / "manifest.csv"
POOL_REJECTED = WORK / "pool_rejected"       # 검수에서 뺀 이미지

# 이미지/데이터셋 (git 제외)
ORIGINALS = WORK / "originals"               # 공식 아트 1장: <class_id>/<class_id>.png (합성용)
BG_POOL = WORK / "bg_pool"                   # 학습용 배경 사진 풀 (photos/, chars/) — teeniping 것을 복사해 쓴다
EVAL_REAL = WORK / "eval_real"               # 평가용 실제 이미지. 학습에 쓰지 않는다
DATASET_CLS = WORK / "dataset_cls"           # 학습 데이터 (재생성 가능)
DATASET_FORM = WORK / "dataset_form"         # ② 폼 모델 학습 데이터 (재생성 가능)

# 모델
MODELS = WORK / "models"
PRETRAINED = MODELS / "pretrained" / "yolov8n-cls.pt"
RUN_NAME = "train_v2"                        # 현재 모델의 폴더 이름 (① 종 모델, train_v1 은 COCO 없이 학습한 이전 버전)
RUN = MODELS / RUN_NAME
BEST_ONNX = RUN / "weights" / "best.onnx"
BEST_PT = RUN / "weights" / "best.pt"

# 출력
LOGS = WORK / "logs"
TMP = WORK / "tmp"
STAGING = TMP / "staging"                    # 데이터셋을 로컬에 먼저 받는 곳 → 다 받으면 RAW(드라이브)로 한 번에 옮긴다

# 스크립트 위치 (다른 스크립트를 불러올 때)
MAKE_DATASET = SCRIPTS / "dataset" / "make-dataset.py"

# ===== 앱 (Flutter, app/) — scripts/deploy/build-app-assets.py 가 쓴다 =====
FORM_RUN_NAME = "form_v1"                   # ② 폼 모델의 폴더 이름 (아직 없음, DESIGN.md 2.1)
FORM_ONNX = MODELS / FORM_RUN_NAME / "weights" / "best.onnx"
SEREBII_RAW = RAW / "serebii"                # 세레비넷 원본 (manifest.csv + img/). 앱 썸네일 옵션 출처
AUDIO = WORK / "audio"                       # 후처리 끝난 안내 음성: <class_id>.wav (아직 없음)
THUMB_CACHE = WORK / "thumbs"                # 앱 썸네일 캐시 (160px PNG, 드라이브에서 매번 다시 읽지 않으려고)
APP = ROOT / "app"                           # Flutter 앱
APP_ASSETS = APP / "assets"                  # 앱 에셋 (git 제외, build-app-assets.py 가 다시 만든다)
SEREBII_SV_RAW = RAW / "serebii-sv"          # 세레비넷 SV 도감 페이지(9세대 이전 종) + 파싱한 영어 도감 설명 flavor_en.json (2026-10-25 사용자 허락)
# (이전) 앱 아이콘 원본: WORK / "app_icon" / "serebii_sv_rotomphone.png" (스마트폰 로토무) → 사용자가 1세대 도감으로 바꿈 (2026-10-25)
APP_ICON_SRC = RAW / "bulbapedia-icon" / "RG_Pokedex.png"   # 앱 아이콘 원본: 레드·그린 공식 아트 도감 (2026-10-25 사용자 허락, 1장)
FORM_IMG_RAW = RAW / "form-images"           # 암컷·누락 폼 그림 (PokeAPI HOME 렌더 + 세레비넷 SV 마휘핑) + manifest.json (2026-10-25 사용자 허락)
SEREBII_OLD_RAW = RAW / "serebii-old"        # 리전폼 종의 세레비넷 SM·SwSh 도감 페이지 + 폼 설명 flavor_forms_en.json (2026-10-25 사용자 허락)
CAPTURES_IN = TMP / "captures_from_phone"    # 폰 앱 Documents/captures 를 꺼내 두는 곳 (import-captures.py → EVAL_REAL/<라벨>/)
APP_BUNDLE_ID = "kr.hansolo.konnadex.flutter"
AUDIO_APP_CACHE = WORK / "audio_m4a"         # 앱용으로 줄인 음성 (AAC 32kbps, build-app-assets.py 가 paths.AUDIO 의 wav 에서 만든다)
APP_BUILDS = WORK / "builds"                 # 앱 빌드 보관: 안드로이드 APK (konnadex_<시각>.apk, 최근 몇 개만 남긴다)
