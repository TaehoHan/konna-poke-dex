# konna-poke-dex

포켓몬을 카메라로 비추면 **어떤 포켓몬인지 알아보고 도감 항목(한국어 이름·분류·타입·도감 설명·진화)을 해석해 보여주는** 도감 프로젝트.
[teeniping-dex](../teeniping-dex) 의 분류 파이프라인(YOLOv8n-cls → ONNX → kmodel/앱)을 포크했다.

```
PokeAPI ─> 클래스 목록·한국어 도감 DB·공식 아트
Kaggle / Hugging Face 데이터셋 ─> raw ─> 정규화(라벨 매핑·중복 제거) ─> pool ─┐
                                              공식 아트 합성 + none ───────────┴─> 학습 ─> ONNX ─> 기기
```

설계는 [DESIGN.md](DESIGN.md), 수집 설계는 [docs/COLLECTING.md](docs/COLLECTING.md), 출처 목록은 [data/sources.yaml](data/sources.yaml), 작업 규칙은 [CLAUDE.md](CLAUDE.md).

## 현재 상태 (2026-10-24)

| 항목 | 상태 |
|---|---|
| 설계 | DESIGN.md, COLLECTING.md, sources.yaml 초안 |
| 수집 | **안 함**. 스크립트 미작성 |
| 학습·평가 스크립트 | 티니핑판 복사본 (포켓몬용 수정 전, 미실행) |
| 모델 | 없음 (사전학습 `yolov8n-cls.pt` 만 있음) |
| 안내 음성 | **다른 목소리로 제로샷 클로닝** 예정 (티니핑 `tts/` 포크 전, DESIGN.md 2.7) |
| 앱 | `app/` 포크 완료 (2026-10-25). 모델 없이 도감 목록·상세만 동작, 인식은 모델 학습 후 (DESIGN.md 5절) |
| 가상환경 | 미생성 (`.venv` 학습용, `.venv-tts` 음성용) |

## 구조

```
konna-poke-dex/
├── README.md  CLAUDE.md  DESIGN.md  requirements.txt
├── scripts/
│   ├── paths.py          경로 단일 관리
│   ├── collect/          (예정) fetch-pokedex, fetch-artwork, fetch-sources, make-label-map, normalize-sources, review-pool
│   ├── dataset/          make-dataset.py   (티니핑판, pool 혼합 단계 추가 예정)
│   ├── train/            train-cls.py
│   ├── eval/             verify-onnx.py, verify-stress.py, eval-real.py
│   └── deploy/           to-kmodel.py (미검증), build-app-assets.py (앱 에셋: 모델·라벨·도감 DB·썸네일·음성)
├── data/                 sources.yaml, (예정) list-poke.csv, label-map/, pool-rejects.csv
├── docs/                 COLLECTING.md
├── app/                  Flutter 앱 (티니핑 앱 포크, konnadex). assets/ 는 git 제외 → build-app-assets.py 로 만든다
├── .claude/skills/       konna-poke-dex (이 저장소 작업 절차·함정·점검표)
└── workspace/            git 제외: raw/, pool/, originals/, bg_pool/, eval_real/, dataset_cls/, models/, logs/
```

## 빠른 시작 (환경)

```bash
python3.12 -m venv .venv && .venv/bin/pip install --upgrade pip && .venv/bin/pip install -r requirements.txt
```
Kaggle·Hugging Face 토큰은 각 사이트에서 발급해 직접 설정한다 (`~/.kaggle/kaggle.json` 권한 600, `huggingface-cli login`).

## 저작권
포켓몬 이미지·이름·도감 설명의 권리는 Nintendo / Creatures / GAME FREAK / The Pokémon Company 에 있다. 공개 데이터셋의 라이선스와 별개로 이 저장소에는 이미지와 도감 원문을 올리지 않으며, 개인 학습용으로만 쓴다.
