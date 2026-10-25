# 수집 설계 (Kaggle · Hugging Face · PokeAPI)

> 상태: **설계만** 했다. 스크립트는 아직 없고, 아무것도 받지 않았다.
> 받기 전에는 항상 출처·용량·라이선스를 사용자에게 알리고 허락을 받는다 (CLAUDE.md).

## 1. 원칙

1. **출처 목록은 `data/sources.yaml` 한곳**. 스크립트는 이 파일만 읽는다. 출처를 바꿀 때 코드를 고치지 않는다.
2. **받은 그대로 보존**: `paths.RAW/<source_id>/` (구글 드라이브 `내 드라이브/Models/konna-poke-dex/raw/`) 는 수정·삭제하지 않는다. 정리는 정규화 단계에서 다른 폴더로 한다.
3. **재실행해도 다시 받지 않는다**: 출처마다 `paths.RAW/<id>/_SOURCE.json` (ref, 받은 날짜, 파일 수, 용량, 해시, 라이선스 문자열)을 남기고, 있으면 건너뛴다.
4. **평가 데이터는 분리**: `use: eval` 출처와 `workspace/eval_real/` 은 학습·합성·none 풀에 넣지 않는다. 정규화 단계에서 pHash 로 학습 풀과 겹치는 평가 이미지를 찾아 학습 쪽에서 뺀다.
5. **저작권**: 포켓몬 이미지는 권리자 소유다. 데이터셋 라이선스와 별개로 저장소에 올리지 않고 배포하지 않는다 (`.gitignore` 에서 `workspace/` 제외).

## 2. 단계와 스크립트 (예정)

| 단계 | 스크립트 (예정) | 입력 → 출력 |
|---|---|---|
| A. 도감 기준 | `scripts/collect/fetch-pokedex.py` | PokeAPI → `data/list-poke.csv`, `data/pokedex-ko.json` |
| B. 공식 아트 | `scripts/collect/fetch-artwork.py` | PokeAPI sprites → `workspace/originals/<id>/<id>.png` |
| C. 데이터셋 받기 | `scripts/collect/fetch-sources.py [--only id ...] [--dry-run [--all]]` (작성됨) | `sources.yaml` → `paths.RAW/<id>/` (드라이브) |
| D. 라벨 매핑 초안 | `scripts/collect/make-label-map.py [id ...]` (작성·실행됨 2026-10-24) | raw 라벨 목록 → `data/label-map/<id>.csv` (자동 매칭 + 미매칭 보고) |
| E. 정규화 | `scripts/collect/normalize-sources.py --rebuild` (작성됨 2026-10-25) | raw + label-map → `workspace/pool/<class_id>/`, `workspace/pool/manifest.csv` |
| F. 검수 | `scripts/collect/review-pool.py` | 클래스별 컨택트 시트(왼쪽 공식 아트 + 번호 붙은 샘플) → `workspace/pool_rejected/` |

`--dry-run` 은 받을 파일 목록과 예상 용량만 출력한다. 사용자 허락을 받을 때 이 출력을 보여준다.

### A. 도감 기준 (`list-poke.csv`, `pokedex-ko.json`)
`data/list-poke.csv` (클래스 단일 기준, 티니핑의 `list-ping.csv` 역할):

```csv
dex_no,class_id,name_ko,name_en,generation,form,enabled
25,pikachu,피카츄,Pikachu,1,,1
122,mr_mime,마임맨,Mr. Mime,1,,1
```
- `class_id`: PokeAPI 이름에서 `-` → `_`, 소문자. 폼 클래스는 `<종>__<폼>` (예약만, 처음엔 사용 안 함).
- `enabled`: 학습 대상. 기본은 1025종 모두 1, 특정 종을 뺄 때만 0. 스크립트는 `enabled=1` 행만 클래스로 쓴다.

`data/pokedex-ko.json` (앱·음성이 쓰는 도감 해석 DB):
```json
{"pikachu": {"dex_no": 25, "name_ko": "피카츄", "genus_ko": "쥐포켓몬", "types": ["electric"],
  "types_ko": ["전기"], "height_m": 0.4, "weight_kg": 6.0,
  "flavor_ko": [{"versions": ["..."], "text": "..."}], "evolution": ["pichu", "pikachu", "raichu"],
  "artwork_url": "https://.../official-artwork/25.png"}}
```
- 도감 설명 텍스트는 게임 원문이라 권리가 있다. JSON 은 개인용이고, 공개 저장소 커밋 여부는 사용자와 정한다 (기본: git 제외 `data/pokedex-ko.json`, 스크립트만 추적).
- PokeAPI 는 요청 간격(예: 0.2초)을 두고, 응답을 `workspace/raw/pokeapi/` 에 캐시한다.
- 한국어 설명이 없는 127종(SV·레전드 아르세우스 전용)은 `data/flavor-ko-translated.json`(영어 설명 번역, git 제외)을 병합한다. 항목에 `translated: true`.
- 같은 문장의 도감 설명은 하나로 합치고 나온 버전을 `versions` 에 모은다. `evolution` 은 진화 트리를 너비 우선으로 펼친 목록(분기 진화도 한 줄).
- `none` 은 CSV 에 넣지 않는다 (학습 데이터 단계에서 붙인다). 재실행하면 기존 행의 `enabled` 값은 유지한다.

### C. 데이터셋 받기
- **Kaggle**: 토큰 없이 `https://www.kaggle.com/api/v1/datasets/download/<ref>` 를 GET (302 → 서명된 zip URL) 해서 `paths.RAW/<id>/` 에 받는다. 로그인이 필요한 출처는 쓰지 않는다. `kaggle` 패키지는 import 때 토큰을 찾아 쓰지 않는다.
- **Hugging Face**: `huggingface_hub.snapshot_download(repo_id, repo_type="dataset", local_dir=paths.RAW/<id>)`. 파케이(parquet) 형식이면 정규화 단계에서 이미지를 풀어낸다. 게이트(로그인·약관 동의) 데이터셋은 쓰지 않는다. HF 캐시는 `HF_HOME=paths.HF_CACHE`(드라이브).
- 받기 전 확인할 것: 실제 용량(Kaggle `kaggle datasets files`, HF API `siblings`/`usedStorage`), 라이선스, 라벨 형식, 해상도.

### D. 라벨 매핑
출처마다 라벨 표기가 다르다 (`Bulbasaur`, `bulbasaur`, `001`, `Mr. Mime`, `MrMime`, `Nidoran♀`, `Farfetch'd`, `Flabébé`).
- 정규화 키: 소문자 → 악센트 제거(é→e) → `♀/♂` → `_f/_m` → 영숫자 외 제거. 같은 방식으로 PokeAPI 영문 이름도 키를 만들어 매칭한다.
- 자동 매칭이 안 되는 라벨은 `class_id` 칸을 비워 두고 보고서를 출력한다. 사람이 채우거나 `skip` 으로 둔다.
- `data/label-map/<id>.csv`: `source_label,class_id,form,count,by,note`. git 추적. `by=manual` 행은 다시 생성해도 유지.
- 결과(2026-10-24): jjmack 1025/1025, kg-gen1-9 1290/1290(폼 265), kg-1000 1000/1000(9세대 일부 25종 없음), 카드 2459 라벨 중 2419 매칭 + 40 skip(태그팀·비포켓몬, 66개는 수동).

### E. 정규화 (`workspace/pool/`)
1. 이미지 읽기 실패·GIF 애니메이션(첫 프레임만)·작은 이미지(짧은 변 < 96px 은 제외, 64px 데이터셋은 별도 플래그) 처리.
2. RGBA 는 흰 배경에 합성하지 않고 그대로 두고(합성 단계에서 배경을 얹는다), 메타에 `has_alpha` 기록.
3. pHash(64bit)로 **출처를 넘나드는 중복**을 묶는다 (해밍 거리 ≤ 4). 그룹 대표 하나만 pool 에 복사.
4. 파일명: `<class_id>/<source_id>__<phash>__<uid>.{png,jpg}`. `manifest.csv`: `class_id,form,file,source_id,source_ref,image_type,view,shiny,w,h,has_alpha,small,phash,color,group,split,conflict`. 투명 배경은 그림 영역만 잘라서 해시·저장한다(빈 캔버스 때문에 다른 종이 묶이는 문제).
5. **분할은 pHash 그룹 단위**로 train/val/test (80/10/10, 시드 고정). 같은 그룹이 두 분할에 걸치지 않는다.
6. 클래스별 개수 보고서: 수량 부족 클래스(< 20장)는 합성으로 채울 대상으로 표시.

### F. 검수
공개 데이터셋은 라벨 오류(진화형 혼동, 다른 포켓몬이 같이 있는 그림, 사람 코스프레)가 꽤 있다고 가정한다.
- 클래스별 컨택트 시트로 눈으로 본다. 1025종이라 세대별로 나눠 서브에이전트 병렬 검수를 한다.
- 틀린 것은 지우지 않고 `workspace/pool_rejected/<class_id>/` 로 옮기고 `data/pool-rejects.csv` 에 기록 (재정규화해도 다시 들어오지 않게).

## 3. 수집 계획 (전국도감 1025종, 2026-10-24 수정)

1. A, B: PokeAPI 1025종 (요청 약 2,600건) + 공식 아트 1025장
2. C: 전 세대 출처만 (1세대 전용·로그인 필요 출처 제외). HF `JJMack/pokemon-classification-gen1-9`(599MB), Kaggle `complete-gen-1-9`(1.1GB, 64px), `1000-pokemon`(816MB), `ultimate-1282`(12GB, 받을지 따로 결정), 평가용 TCG 카드(9MB)
3. C': 세레비넷 `scripts/collect/fetch-serebii.py` (`--only` 15종 이하 = 표본, 전체는 `enabled: true` 필요). 종당 공식 아트(폼 태그 포함) + 3D 렌더(HOME·SV·SWSH·ZA, 이로치). 표본 14종 받음(2026-10-24), 전체 예상 약 13,000건·3.6시간·1GB 미만
4. D~F: 라벨 매핑, 정규화, 검수
5. none 풀: 티니핑 `workspace/bg_pool` 복사 (네트워크 불필요)
6. 실제 평가: 직접 찍은 인형·카드·TV 화면 사진 수십 장 (`workspace/eval_real/`)

## 4. 열린 질문
- Kaggle 출처의 실제 라이선스·용량 (검색 결과만 봤다. `sources.yaml` 의 `verified: listing`)
- `kg-lantian-gen1` 은 존재 자체가 미확인
- 64px 데이터셋을 쓸지 (224 업샘플 학습 시 실제 카메라 분포와 차이)
- TCG 카드: 그림 영역 자르기 규칙(세대별 카드 틀이 다름)
