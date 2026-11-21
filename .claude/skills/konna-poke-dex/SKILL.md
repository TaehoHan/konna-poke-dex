---
name: konna-poke-dex
description: 이 저장소(포켓몬 도감: 카메라로 포켓몬을 인식하는 YOLOv8n-cls 분류기 + PokeAPI 한국어 도감 해석 DB → ONNX → 폰 앱/K230)에서 작업할 때의 절차와 함정 모음. 클래스(전국도감 1025종) 목록 관리, Kaggle·Hugging Face 데이터셋 수집 설계와 허락 받기, 라벨 매핑·pHash 중복 제거·분할, 검수, 공식 아트 합성, 학습·평가(출처별·진화 계열 혼동), 도감 해석 DB(한국어 이름·분류·타입·도감 설명·진화), 다른 목소리로 제로샷 클로닝한 안내 음성 합성·검수·후처리, 앱·보드 배포를 할 때 사용한다.
---

# konna-poke-dex 작업 절차

포켓몬 한 마리를 카메라로 비추면 종을 알아보고(인식) 도감 항목을 보여주는(해석) 도감이다. `teeniping-dex` 에서 포크했고, 거기서 얻은 범용 교훈은 이 스킬의 [pitfalls.md](pitfalls.md), [checklists.md](checklists.md) 에 포켓몬 상황에 맞춰 옮겨 두었다.

**현재 상태 (2026-10-25): 첫 모델 학습·평가까지.** `.venv` 있음. PokeAPI 1025종 받음(`list-poke.csv`, `pokedex-ko.json`; 9세대 등 127종은 한국어 도감 설명 없음). 세레비넷 1025종 받음(드라이브 `raw/serebii`, 7,054장, 폼 태그 포함). HF·Kaggle 4개 받음(드라이브 `raw/`), 라벨 매핑 완료(`data/label-map/`), 정규화 완료(pool 152,533장), 데이터셋(train 309,000) 생성, ① 종 모델 `train_v1`·`train_v2`(COCO none 보강, none 거부 75→92%), ② 폼 모델 `form_v1` 학습·평가 완료(2026-10-25, DESIGN.md 6절). 실제 촬영 평가는 앱 수집 프레임이 더 필요. 모델은 2단계(① 종 + none → ② 폼, DESIGN.md 2.1).  아래 "(예정)" 은 아직 없는 스크립트다. 없는 것을 있다고, 돌려 보지 않은 것을 동작한다고 말하지 않는다.

설계 근거: `DESIGN.md`, 수집 설계: `docs/COLLECTING.md`, 출처 목록: `data/sources.yaml`, 규칙: `CLAUDE.md`.

## 티니핑과 다른 점 (먼저 알 것)
| | 티니핑 | 포켓몬 |
|---|---|---|
| 데이터 | 클래스당 원본 1장 → 합성이 중심 | **공개 데이터셋에 종당 수십~수백 장** → 실제 이미지가 중심, 합성은 부족한 종 보충 |
| 수집 방식 | 브라우저로 위키·검색 수집 | **Kaggle/HF API 로 데이터셋 통째로**, PokeAPI 로 목록·도감·공식 아트 |
| 핵심 위험 | 대표 이미지 한 장의 한계 | **라벨 표기 불일치, 출처 간 중복(재업로드), 라벨 오류, test 누수** |
| 클래스 수 | 201 | 1026 (전국도감 1025 + `none`, 처음부터) |
| 도감 정보 | 직접 작성 | PokeAPI 한국어 데이터 (도감 설명은 게임 원문이라 배포 금지) |

## 0. 경로와 기준 파일
- 경로는 `scripts/paths.py` 한곳 (`RAW`, `POOL`, `POOL_MANIFEST`, `ORIGINALS`, `LABEL_MAP`, `SOURCES`, `CSV_MAIN` …). 스크립트에 경로 문자열을 쓰지 않는다.
- 클래스 단일 기준: `data/list-poke.csv` (`dex_no,class_id,name_ko,name_en,generation,form,enabled`). **`enabled=1` 행만 클래스.** 기본은 1025종 모두 1.
- `class_id` = PokeAPI 영문 이름 소문자, `-`→`_` (`mr-mime`→`mr_mime`, `nidoran-f`→`nidoran_f`). 폼 클래스는 `<종>__<폼>` (예약만).
- 출처 단일 기준: `data/sources.yaml`. 출처 추가·제외는 yaml 만 바꾼다.

## 1. 수집 (허락 먼저)
**받기 전에 항상**: 출처·실제 용량·라이선스·파일 수를 사용자에게 보여주고 허락을 받는다. 허락 받은 출처만 `enabled: true` 로 바꾼다.

```bash
.venv/bin/python scripts/collect/fetch-pokedex.py [--dry-run]     # PokeAPI → list-poke.csv, pokedex-ko.json (캐시: workspace/raw/pokeapi)
.venv/bin/python scripts/collect/fetch-serebii.py --only pikachu,vulpix   # 세레비넷 표본(15종 이하). 전체는 sources.yaml enabled: true 후 인자 없이
.venv/bin/python scripts/collect/fetch-artwork.py               # (예정) 공식 아트 → workspace/originals/<id>/<id>.png
.venv/bin/python scripts/collect/fetch-sources.py --dry-run [--all]   # HF·Kaggle 메타데이터만 (용량·라이선스·로그인 필요 여부) → 사용자에게 보여줌
.venv/bin/python scripts/collect/fetch-sources.py --only hf-jjmack-gen1-9    # 허락 받은(enabled: true) 출처만 드라이브에 받는다
```
- **로그인이 필요한 출처는 쓰지 않는다.** HF 는 게이트 없는 공개 데이터셋을 토큰 없이, Kaggle 은 `https://www.kaggle.com/api/v1/datasets/download/<ref>` 를 토큰 없이 GET(302 리다이렉트)으로 받는다. `kaggle` 패키지는 import 할 때 토큰을 찾으므로 쓰지 않는다 (HEAD 요청은 404 라 존재 확인에 쓰지 않는다).
- `skip` 필드가 있는 출처(1세대 전용 등)는 받지 않는다.
- `fetch-sources.py` 디스크 관문: 드라이브 앱이 업로드 전까지 로컬에 파일을 들고 있으므로 로컬 여유 < 용량x2+5GB 면 받지 않는다. 3GB 초과는 `--allow-large`. Kaggle zip 은 경로 검사 후 풀고 지운다(sha256 은 `_SOURCE.json`). `kg-ultimate-1282`(12GB)는 로컬 여유 약 29GB 가 필요해 지금은 못 받는다.
- 세레비넷 폼 태그는 **alt 텍스트**에서 읽는다. 파일명 접미사는 종마다 뜻이 다르다(피카츄 `-a` = 얼로라 **모자**). alt 안에 `<br />` 가 있어(켄타로스 품종) img 태그 정규식은 따옴표를 고려한다. 기본형 파일은 "Kantonian Form" 처럼 적혀도 태그를 비운다. alt 가 없는 폼 아트는 `unlabeled_<접미사>` 로 남겨 사람이 본다.
- 세레비넷 404 회피: SWSH 렌더는 905번 이하만, ZA 렌더는 종 페이지에 `Lumiose : #` 또는 `Hyperspace : #` 번호가 있는 종만 요청한다 (표본 14종의 실제 404 와 정확히 일치). 종 페이지의 게임 링크는 모든 종에 같은 메뉴라 판단에 못 쓴다.
- `fetch-serebii.py` 는 meta 를 매번 받아 둔 페이지로 다시 만든다 → 파싱 규칙을 고친 뒤 다시 돌려도 새 요청이 없다.
- 용량 확인: HF 는 `https://huggingface.co/api/datasets/<ref>` (메타데이터만, 다운로드 아님), Kaggle 은 `https://www.kaggle.com/api/v1/datasets/view/<ref>` 의 `totalBytes`, `licenseName` (토큰 불필요).
- **받은 데이터셋은 구글 드라이브** `paths.RAW` (`내 드라이브/Models/konna-poke-dex/raw/<id>/`), HF 캐시는 `HF_HOME=paths.HF_CACHE`(드라이브). 로컬 디스크 여유가 적다(약 13GB). 홈 폴더 캐시에 쌓지 않는다. 받는 스크립트는 `paths.require_drive()` 로 시작한다.
- 드라이브 파일은 스트리밍이라 학습 중 직접 읽으면 느리다. 정규화 단계에서 `workspace/pool/` 로 줄여 복사한 뒤 학습은 로컬에서 한다.
- PokeAPI 는 요청 간격(약 0.2초)을 두고 응답을 캐시한다. 같은 요청을 반복하지 않는다.
- `paths.RAW/<id>/`(드라이브) 는 받은 그대로 두고, `_SOURCE.json`(ref, 날짜, 파일 수, 용량, 라이선스)을 남긴다.

## 2. 정규화 → pool
```bash
.venv/bin/python scripts/collect/make-label-map.py [source_id ...]   # data/label-map/<id>.csv (source_label,class_id,form,count,by,note) + 미매칭 보고. by=manual 행은 다시 돌려도 유지
.venv/bin/python scripts/collect/normalize-sources.py --rebuild [--limit 300]   # raw(드라이브) → workspace/pool/<class_id>/<source>__<phash>__<uid>.{png,jpg} + manifest.csv
```
- 라벨 정규화 키: 소문자 → 악센트 제거 → `♀/♂`→`f/m` → 영숫자만. 미매칭은 비워 두고 사람이 채우거나 `skip`(사람이 고친 행은 `by=manual`).
- 출처별 라벨 (2026-10-24 확인): JJMack 은 `label` = 도감 번호(`name` 은 'Wo-Chien'→'Wo' 처럼 잘려 못 믿는다, 0번 'test' 라벨은 실제로 없었다). kg-gen1-9 는 `label` 1~1290(1026~ 는 폼), 파일명에 PokeAPI 포켓몬 이름. kg-1000 은 폴더명 = PokeAPI 포켓몬 이름(`aegislash-shield`), `dataset/` 이 전체이고 train/val/test 는 그 사본. PokeAPI 캐시의 species `varieties` 로 포켓몬 이름 → 종+폼을 정확히 맞춘다.
- **hf-pokemon-cards 에는 이미지가 없다** (`image_url` 만, images.pokemontcg.io). 쓰려면 따로 받아야 한다(허락 필요). 태그팀(`&`) 카드는 `skip`.
- **pHash 로 출처를 넘는 중복을 묶고, 그룹 단위로 train/val/test 를 나눈다.** 같은 그림이 train 과 test 에 걸치면 test 정확도가 부풀려진다.
- **투명 배경 이미지는 그림 영역만 잘라낸 뒤 해시·저장한다.** JJMack 도트는 256px 투명 캔버스 가운데 수십 px 그림이라, 안 자르면 pHash 가 빈 캔버스 모양을 봐서 다른 종(블리츨·알통몬·구구…)이 한 그룹으로 묶였다 (2026-10-25).
- 크기: 자른 뒤 긴 변 48px 미만 제외, 짧은 변 96px 미만은 `small=1` 표시만(kg-gen1-9 는 전부 64px). GIF 는 첫 프레임. RGBA 는 PNG(투명 유지), 나머지 JPG q92, 긴 변 512px 로 줄임.
- 중복: 같은 종 + 같은 pHash + 평균색 차이 ≤ 12 일 때만 하나 남김 (pHash 는 모양만 봐서 색이 다른 이로치를 지우지 않으려고 색도 본다). 세레비넷 HOME 렌더와 SV 렌더는 거의 같은 그림이라 대부분 하나로 합쳐진다.
- 그룹(해밍 ≤ 4)에 다른 종이 섞이면 `split=conflict` 로 학습에서 뺀다. 분할은 그룹 대표 해시의 sha1 로 고정(80/10/10).
- kg-gen1-9 는 tar.xz 와 npz 가 같은 11.6만 장(64px 도트 + 체크무늬 등 합성 배경). npz 를 쓴다. tar 안에 macOS `._*` 파일이 같은 수만큼 섞여 있다. **train 전용**: 몇 장의 도트에 배경만 바꾼 증강본이라 val/test 에 넣으면 누수이고, 작은 그림 + 무늬 배경이라 pHash 가 배경을 봐서 다른 종끼리 묶인다(종 충돌 5,592장 중 5,523장이 이 출처였다).
- JJMack 파일명 꼬리로 image_type(dream/masters=art, home/go/snap=render, sprite*/RB/RG/MS=sprite, anime)과 뒷모습(`view=back`, SpriteBack·sprite-back-*)을 기록한다.
- 모든 파일에 출처와 이미지 종류(`art/sprite/render/anime/card/photo/fanart/mixed`)를 남긴다.

## 3. 검수
```bash
.venv/bin/python scripts/collect/review-pool.py --class pikachu  # (예정) 컨택트 시트: 왼쪽 공식 아트 + 번호 붙은 샘플
```
- 공개 데이터셋에는 라벨 오류(진화형 혼동, 여러 마리가 같이 있는 그림, 코스프레·사람, 카드 뒷면)가 있다고 가정한다.
- 틀린 것은 지우지 않고 `workspace/pool_rejected/<class_id>/` 로 옮기고 `data/pool-rejects.csv` 에 기록한다 (재정규화해도 다시 들어오지 않게).
- 1025종은 세대별로 나눠 서브에이전트 병렬 검수(읽기 전용, 결과 파일 경로 지정). 확신 없으면 유지, 명백한 것만 제외.

## 4. 학습 데이터·학습
```bash
.venv/bin/python scripts/dataset/make-dataset.py [--limit-classes 20]   # pool → dataset_cls/{train,val,test} + manifest.csv (2026-10-25 포켓몬용으로 다시 씀)
.venv/bin/python scripts/train/train-cls.py --epochs 12 --name train_v1
.venv/bin/python scripts/dataset/make-form-list.py                  # ② 폼 목록 data/form-list.csv (include 열 수정 가능, by=manual 유지)
.venv/bin/python scripts/dataset/make-dataset.py --task form --train 200   # → workspace/dataset_form
.venv/bin/python scripts/train/train-cls.py --data workspace/dataset_form --epochs 12 --name form_v1
.venv/bin/python scripts/eval/eval-form.py --run form_v1              # 종 안에서 폼 고르기(마스크) 정확도
```
- 구성: 클래스당 train 300장 = pool train 이미지를 출처·이미지 종류가 고르게 돌아가며 골라(뒷모습은 절반 가중치) 증강한 것. pool 이미지가 대부분 투명 배경이라 티니핑 합성(배경 위에 얹기·회전·원근·가림·스티커)을 그대로 쓴다. val/test 는 pool val/test 에서 클래스당 최대 30/50장, 이미지당 한 장, 약한 변형(시드 고정).
- pool 분할은 pHash 그룹 단위라 val/test 가 클래스당 평균 4장 정도로 적다(2026-10-25: val 4,024 / test 4,183). kg-gen1-9 는 train 전용.
- `none`: 티니핑 `bg_pool`(photos 410, chars 115)과 티니핑 원본 200장(`bg_pool/teeniping`, 투명 PNG를 배경에 얹음)을 로컬에 복사해 쓴다. 6장 중 1장은 val/test 전용. 기본 train 1500 / val 150 / test 150.
- **SGD lr0=0.01 고정**, 새 모델은 새 `--name`, 현재 모델은 `paths.RUN_NAME`. 학습은 백그라운드 + `workspace/logs/`, 로그는 `tr '\r' '\n'` 로 읽는다. GPU 작업 동시 실행 금지.
- 1025종 단계에서는 `yolov8s-cls`·입력 256 을 같이 비교한다 (기기 제약과 함께).

## 5. 평가 — 숫자를 정직하게
```bash
.venv/bin/python scripts/eval/eval-test.py --run train_v1 [--split val]   # 출처·종류·뒷모습·폼별 정확도, 진화 계열 혼동, none 거부, 임계값 표 (CPU)
.venv/bin/python scripts/eval/verify-onnx.py --data workspace/dataset_cls/test --n 20   # int8 per-tensor vs per-channel+Percentile
```
- test 분할 정확도는 **같은 분포**다. 실제 카메라 성능이라고 말하지 않는다.
- **출처별·이미지 종류별**로 쪼개 보고한다 (스프라이트만 잘 맞고 굿즈 실사는 못 맞히는 상황을 숨기지 않는다).
- **진화 계열 혼동**: top-1 오답 중 같은 진화 계열 안의 오답 비율을 따로 본다 (`pokedex-ko.json` 의 `evolution`).
- 실제 성능은 `workspace/eval_real/`(직접 찍은 인형·카드·피규어·TV 화면)로만 말하고, 장 수를 함께 말한다. `use: eval` 출처(TCG 카드 등)도 학습 금지.
- 비포켓몬 거부율과 임계값 표, int8(per-channel + Percentile) 손실을 같이 낸다.

## 6. 도감 해석 DB
- `data/pokedex-ko.json`: `name_ko, genus_ko, types(_ko), height_m, weight_kg, flavor_ko[], evolution[]`. 라벨(`class_id`)로 조회한다.
- 도감 설명은 게임 원문이라 **git 제외·배포 금지**(기본). 스크립트만 추적한다.
- PokeAPI 한국어 설명은 **X·Y~소드·실드(레츠고 포함)만** 있다. 스칼렛·바이올렛/레전드 아르세우스에만 나오는 종(9세대 120 + 히스이 7 = 127종)은 한국어 설명이 없다 → **영어 설명(SV 우선)을 번역해 채운다** (2026-10-25 사용자 결정). 번역본은 `data/flavor-ko-translated.json`(git 제외, 영어 원문·버전 같이 보관)에 두고 `fetch-pokedex.py` 가 빈 종에만 병합한다. DB 의 그 항목에는 `translated: true, source_lang: en` 이 붙는다(출처 추적용. 앱 화면에는 표시하지 않는다, 2026-10-25). 번역 규칙: "~한다" 체, 다른 포켓몬은 한국어 공식 이름, 단위는 미터법.
- **1~5세대 게임(레드~블랙2·화이트2) 영어 설명도 번역해 덧붙였다** (2026-10-25 사용자 결정, 앱의 세대별 설명용). 649종 4,570문장(같은 문장은 버전 묶음), 서브에이전트 10개 병렬 번역 후 검증(빠짐·영어 잔여·존댓말 0). `flavor-ko-translated.json` 은 `{class_id: [항목…]}`, 병합은 공식 한국어가 없는 버전만 덧붙인다. **세대별 설명은 글로만 제공**하고 음성은 종당 1문장(`tts/make-text.py`, 공식 최신 한국어 설명 → 없으면 SV/레전드 번역).

## 7. 안내 음성 (다른 목소리로 클로닝한다)
티니핑 `tts/` 를 포크했다 (2026-10-25, `.venv-tts` = `requirements-tts.txt`, mlx-audio 0.6.1). 레퍼런스는 **로미와 다른 목소리**, 사용자가 마련한 파일만.
- **현재 레퍼런스 `narr`** (2026-10-25): 사용자가 준 성우 김영선 도감 낭독 샘플 영상(15초, 터검니·팽도리). 배경음악·효과음이 계속 깔려 있어 demucs 로 목소리만 분리 → `tts/ref/narr.wav`(14.4초 전체) + `narr.txt`, 정리본 `narr_clean.wav`. 구간별 `narr_a`(터검니)·`narr_b`(팽도리)도 있다. 시험 합성 CER: 전체(AB) 0.00 / A 0.15 / B 0.16 → **전체 15초가 가장 낫다**. `narr.txt` 는 Whisper 받아쓰기에서 이름(턱없니→터검니, 펭귄놀이→팽도리)만 고친 것이라 사용자 확인 전이다. 실제 인물 목소리라 생성 음성은 개인 기기 밖으로 내보내지 않는다.
- 낭독 형식이 "이름. 분류포켓몬. 설명" 이라 안내 문장도 같은 형식이 어울린다 (`data/tts_text.json` 은 아직 없음, 형식 미정).
- **모델은 받지 않는다.** 구글 드라이브 `내 드라이브/Models/` 에 티니핑과 같이 쓰는 모델이 있다: `paths.TTS_MODEL`(Qwen3-TTS-12Hz-1.7B-Base-bf16, 4.2GB), `paths.STT_MODEL`(whisper-large-v3-turbo-mlx, 1.5GB). 티니핑 `tts/` 는 2026-10-24 에 기본값을 `paths.TTS_MODEL`/`paths.STT_MODEL` 로 바꿨다 (포크하면 그대로 따라온다). `tts/` 스크립트는 `scripts/` 밖이라 `sys.path` 에 `scripts/` 를 넣고 `import paths` 한다.
- 드라이브는 스트리밍이라 처음 불러올 때 내려받느라 느리다. 드라이브에서 이 두 폴더를 "오프라인 사용 가능"으로 두면 로컬 디스크를 약 5.7GB 쓴다 (디스크 여유와 같이 본다). mlx-audio 는 경로가 존재하면 로컬 폴더를 쓰고 없을 때만 허브에서 받는다 (`mlx_audio.utils.get_model_path`, 티니핑 `.venv-tts` 소스로 확인). 드라이브가 안 붙어 있으면 경로가 없어서 허브에서 다시 받으려 하므로 주의.
```bash
.venv-tts/bin/python tts/prep-ref.py --src <사용자 파일> [--vocals --demucs-python <demucs python>]   # 레퍼런스 자르기·정리(·목소리 분리) → tts/ref/
.venv-tts/bin/python tts/clean-ref.py [--trim-start 0]                   # 잡음 줄인 사본 narr_clean.wav (demucs 를 거쳤으면 앞 자르기 0)
.venv-tts/bin/python tts/qwen3_synth.py [--only id ...] [--force]        # data/tts_text.json → tts/raw/<id>.wav (문장당 6~20초)
.venv-tts/bin/python tts/check-audio.py --report tts/check_report.json   # Whisper CER
.venv-tts/bin/python tts/regen-best.py --report tts/check_report.json --min-cer 0.08 --tries 3
```
- 레퍼런스의 **대사 텍스트를 정확히** 적는다. 앞 잡음 구간은 잘라낸 사본으로 클로닝한다 (pitfalls.md 음성 절).
- 문장은 `pokedex-ko.json` 에서 만든 `data/tts_text.json`. 포켓몬 이름(고유명사)은 받아쓰기 CER 이 틀리기 쉬우니 의심 목록으로만 쓰고 사람이 듣는다.
- 1025문장은 수 시간 걸린다 → 세대 단위로 나눠, 백그라운드 + 로그. **학습과 동시에 돌리지 않는다.**
- 후처리: `.venv/bin/python tts/build-audio.py --engine dir --raw-dir tts/raw --no-voice-trim` → -45dB 무음 자르기, 잡음 감소, 2패스 선형 loudnorm(-16 LUFS), 16kHz 모노 PCM → `workspace/audio/` (2026-10-25: 1025개, 243MB). 유성음 자르기(기본값)는 말소리를 잘라 먹어서 끈다.
- 레퍼런스·생성 음성은 저장소에 올리지 않고 공개·배포하지 않는다. 영상에서 음성을 내려받거나 추출하지 않는다.

## 8. 앱·보드
**앱**: 티니핑 `app/` 을 포크해 `app/`(konnadex, "포켓몬 도감", `kr.hansolo.konnadex`)으로 고쳤다 (2026-10-25). 설계·에셋 계약은 DESIGN.md 5절. 함정(옛 모델 캐시, 카메라 회전, 진단 보기)은 [pitfalls.md](pitfalls.md).
```bash
.venv/bin/python scripts/deploy/build-app-assets.py                     # 모델·라벨·도감 DB·음성을 한 번에 app/assets/ 로 (없는 것은 경고)
.venv/bin/python scripts/deploy/build-app-assets.py --thumbs serebii    # 썸네일도 (드라이브 raw/serebii 아트, 또는 originals)
.venv/bin/python scripts/deploy/build-app.py --install-ios <UDID>      # 에셋 + iOS release(+아이폰 설치) + 안드로이드 APK 보관 (workspace/builds/konnadex_<시각>.apk, 최근 3개)
cd app && flutter pub get && flutter analyze && flutter test
flutter build ios --simulator --debug                                   # 모델 없이 도감 확인용. 시뮬레이터엔 카메라가 없다
```
- 모델·라벨·도감 DB 를 **같은 스크립트가 함께** 만든다. 라벨은 ONNX 메타데이터 `names` 에서 읽고, 앱도 메타데이터와 라벨 파일이 다르면 모델을 거부한다.
- 모델이 없으면 앱은 도감만, 폼 모델이 없으면 종만 판정한다. 새 모델을 넣을 때는 `paths.RUN_NAME`(①)·`paths.FORM_RUN_NAME`(②)을 바꾸고 스크립트를 다시 돌린 뒤 앱을 다시 빌드한다.
- 앱 에셋(모델·도감 원문·썸네일·음성)은 git 제외. `.gitkeep` 만 추적한다.
- 다른 모습(폼·이로치)은 세레비넷 manifest 에서 만든다: 폼 = 공식 아트 `form_tag`, 이로치 = `sv_shiny` 렌더 (`--thumbs serebii`, 약 60MB). 폼 한국어 이름은 `FORM_KO`(세레비넷 폼 234개 전부, 직접 정리)에 있고, 새 폼이 생겨 표에 없으면 영문 그대로 나온다 → 표에 추가.
- 9세대 이전 종의 스칼렛·바이올렛·레전드 Z-A 설명은 세레비넷 SV 도감(`fetch-serebii-sv.py`, 드라이브 `raw/serebii-sv/flavor_en.json`)을 번역해 넣는다. **목록의 번호는 지역 도감 번호가 섞여 있다** → 페이지 안 National 번호로 확인한다 (2026-10-25 첫 실행에서 84종이 엉뚱한 종에 붙었다). 폼 설명은 표 아이콘 접미사(-m, -a …) → 폼 태그, 앱에서 다른 모습을 고르면 그 폼 설명(`f`)을 보여 준다.
- 번역한 도감 설명은 데이터에만 `tr: true` 가 남고 앱 화면에는 "번역" 표시를 하지 않는다 (2026-10-25 사용자 결정).
- 실기기 인식, Android 빌드, ② 폼 경로(단위 테스트만)는 **미검증**. 인식 임계값 0.6 은 티니핑 값이라 포켓몬 모델로 다시 정한다.

**보드**: K230 kmodel 변환(`scripts/deploy/to-kmodel.py`)은 미검증. 1026 클래스면 출력층·크기를 다시 확인한다.

## 9. 하지 않는다 (사용자 확인 없이는)
데이터셋·이미지·모델(TTS 가중치 포함) 다운로드와 API 대량 호출, 약관 동의, 커밋·푸시·드라이브 업로드, 포켓몬 이미지·도감 원문·레퍼런스 음성·생성 음성의 저장소 추가나 공개, 영상에서 음성 추출, `workspace/` 이동·삭제.
