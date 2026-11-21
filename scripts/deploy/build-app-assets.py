"""Flutter 앱(app/)의 에셋을 app/assets/ 에 한 번에 만든다 (git 제외, 언제든 다시 만들 수 있다).

모델·라벨·도감 DB 를 **이 스크립트 하나가 함께** 만든다 (라벨과 모델이 어긋나 엉뚱한 답이 나오는 것을 막으려고).
없는 것은 건너뛰고 경고로 알린다. 모델이 아직 없어도 도감 DB 와 라벨 자리는 만들어진다.

  dex.json                 도감 DB (앱용으로 줄인 것). data/list-poke.csv(enabled=1) + data/pokedex-ko.json
                           + 성비 gr (PokeAPI 캐시 gender_rate: 암컷 8분의 n, -1 = 성별 없음)
  species_labels.txt       ① 종 모델 출력 순서의 class_id (1025종 + none). 모델이 있으면 ONNX 메타데이터 names 에서 읽는다.
                           모델이 없으면 정렬된 class_id + none 자리표시 (Ultralytics 분류 폴더 순서 = 이름순)
  models/species.onnx      ① 종 모델 (paths.BEST_ONNX). 없으면 앱은 인식 없이 도감만 보여 준다
  model_info.json          어느 모델인지 (run 폴더 이름·크기·수정 시간·해시·클래스 수). 앱 설정·진단 화면에 표시한다
  models/forms.onnx        ② 폼 모델 (paths.FORM_ONNX). 없으면 앱은 ② 없이 동작한다
  form_labels.txt          ② 폼 모델 출력 순서 (`<종>__<폼>` 과 기본형 종 이름). 폼 모델이 있을 때만
  thumbs/<id>.png          (선택 --thumbs) 160px 썸네일. 다른 모습은 thumbs/<종>__<폼>.png, 이로치는 thumbs/<종>__shiny.png
                           (serebii: 폼은 공식 아트의 form_tag, 이로치는 스칼렛·바이올렛 렌더 sv_shiny)
  audio/<class_id>.m4a     (있으면) paths.AUDIO 의 안내 음성 wav 를 AAC 32kbps 로 줄인 것 (afconvert, 캐시 paths.AUDIO_APP_CACHE)
  (앱 아이콘)              paths.APP_ICON_SRC 가 있으면 iOS·Android 아이콘을 그것으로 만든다 (git 제외)

사용:
  .venv/bin/python scripts/deploy/build-app-assets.py                     # 모델·DB·라벨·음성 (썸네일은 기존 것 유지)
  .venv/bin/python scripts/deploy/build-app-assets.py --thumbs originals  # workspace/originals 공식 아트로 썸네일
  .venv/bin/python scripts/deploy/build-app-assets.py --thumbs serebii    # 드라이브 raw/serebii 아트로 썸네일 (드라이브 읽기)

다운로드는 하지 않는다. 썸네일은 로컬/드라이브에 이미 있는 이미지에서만 만든다.
포켓몬 이미지·도감 설명 원문·음성은 권리자/목소리 소유라 개인 기기 시험용으로만 쓰고 저장소에 올리지 않는다.
"""
import argparse
import ast
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/ (paths.py)
import paths  # noqa: E402

NONE = "none"
THUMB = 160

# 도감 설명의 게임 버전 표시 이름 (없는 키는 그대로 보여 준다)
VERSION_KO = {
    "red": "레드", "blue": "블루", "yellow": "피카츄", "gold": "골드", "silver": "실버", "crystal": "크리스탈",
    "ruby": "루비", "sapphire": "사파이어", "emerald": "에메랄드", "firered": "파이어레드", "leafgreen": "리프그린",
    "diamond": "다이아몬드", "pearl": "펄", "platinum": "Pt", "heartgold": "하트골드", "soulsilver": "소울실버",
    "black": "블랙", "white": "화이트", "black-2": "블랙2", "white-2": "화이트2", "x": "X", "y": "Y",
    "omega-ruby": "오메가루비", "alpha-sapphire": "알파사파이어", "sun": "썬", "moon": "문",
    "ultra-sun": "울트라썬", "ultra-moon": "울트라문", "lets-go-pikachu": "레츠고! 피카츄", "lets-go-eevee": "레츠고! 이브이",
    "sword": "소드", "shield": "실드", "legends-arceus": "레전드 아르세우스", "scarlet": "스칼렛", "violet": "바이올렛",
    "brilliant-diamond": "브릴리언트 다이아몬드", "shining-pearl": "샤이닝 펄", "legends-za": "레전드 Z-A",
}

# 게임 버전 → 세대 (앱의 도감 설명 세대 드롭다운용). 없는 키는 세대를 모르는 것으로 둔다
VERSION_GEN = {
    **dict.fromkeys(["red", "blue", "yellow"], 1), **dict.fromkeys(["gold", "silver", "crystal"], 2),
    **dict.fromkeys(["ruby", "sapphire", "emerald", "firered", "leafgreen"], 3),
    **dict.fromkeys(["diamond", "pearl", "platinum", "heartgold", "soulsilver"], 4),
    **dict.fromkeys(["black", "white", "black-2", "white-2"], 5),
    **dict.fromkeys(["x", "y", "omega-ruby", "alpha-sapphire"], 6),
    **dict.fromkeys(["sun", "moon", "ultra-sun", "ultra-moon", "lets-go-pikachu", "lets-go-eevee"], 7),
    **dict.fromkeys(["sword", "shield", "brilliant-diamond", "shining-pearl", "legends-arceus"], 8),
    **dict.fromkeys(["scarlet", "violet", "legends-za"], 9),
}


def split_by_gen(versions):
    """[(세대, 그 세대 버전들)] 세대 오름차순. 세대를 모르는 버전은 (None, …) 로 맨 뒤."""
    groups = {}
    for v in versions:
        groups.setdefault(VERSION_GEN.get(v), []).append(v)
    return sorted(groups.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))


def flavor_gen(versions):
    gens = [VERSION_GEN[v] for v in versions if v in VERSION_GEN]
    return min(gens) if gens else None


# 폼 이름 (폼 목록 파일이 생기기 전 임시 규칙: `<종>__<폼>` 의 <폼> 부분 → 한국어). 없으면 <폼> 그대로.
FORM_KO = {
    "alola": "알로라의 모습", "galar": "가라르의 모습", "hisui": "히스이의 모습", "paldea": "팔데아의 모습",
    "mega": "메가진화", "mega_x": "메가진화 X", "mega_y": "메가진화 Y", "gmax": "거다이맥스",
}
TYPE_KO = {"normal": "노말", "fire": "불꽃", "water": "물", "grass": "풀", "electric": "전기", "ice": "얼음",
           "fighting": "격투", "poison": "독", "ground": "땅", "flying": "비행", "psychic": "에스퍼", "bug": "벌레",
           "rock": "바위", "ghost": "고스트", "dragon": "드래곤", "dark": "악", "steel": "강철", "fairy": "페어리"}
# 세레비넷 form_tag 의 한국어 폼 이름 (여러 종에 같은 태그면 공통, 종마다 다르면 "<종>__<태그>"). 2026-10-25 직접 정리, 공식 표기와 다를 수 있다
FORM_KO.update({
    "origin": "오리진폼", "therian": "영물폼", "summer": "여름의 모습", "autumn": "가을의 모습", "winter": "겨울의 모습",
    "east_sea": "동쪽바다의 모습", "sandy_cloak": "모래땅도롱", "trash_cloak": "슈레도롱", "antique": "진품의 모습",
    "small_size": "작은 사이즈", "average_size": "보통 사이즈", "large_size": "큰 사이즈", "super_size": "특대 사이즈",
    "red_flower": "빨간꽃", "yellow_flower": "노란꽃", "orange_flower": "오렌지색꽃", "blue_flower": "파란꽃",
    "white_flower": "하얀꽃", "eternal_flower": "영원의 꽃",
    **{f"{t}_type": f"{k} 타입" for t, k in TYPE_KO.items()},          # 아르세우스
    **{f"type_{t}": f"{k} 타입" for t, k in TYPE_KO.items()},          # 실버디
    # 피카츄
    "original_cap": "오리지널캡", "hoenn_cap": "호연캡", "sinnoh_cap": "신오캡", "unova_cap": "하나캡",
    "kalos_cap": "칼로스캡", "alola_cap": "알로라캡", "partner_cap": "너로 정했다캡", "world_cap": "월드캡",
    "captain": "캡틴 피카츄", "mr_windychu": "윈디츄 씨", "ms_waveychu": "웨이비츄 양", "belle": "마담피카츄",
    "libre": "마스크드피카츄", "ph_d": "닥터피카츄", "pop_star": "아이돌피카츄", "rock_star": "하드록피카츄",
    "paldea_combat_breed": "팔데아의 모습 컴뱃종", "paldea_blaze_breed": "팔데아의 모습 블레이즈종",
    "paldea_aqua_breed": "팔데아의 모습 워터종",
    "armored": "아머드 뮤츠", "spiky_eared": "삐쭉귀 피츄", "slowking__slowbro": "야도란",
    "castform__normal": "노말", "sunny": "태양의 모습", "rainy": "빗방울의 모습", "snowy": "설운의 모습",
    "attack": "어택폼", "defense": "디펜스폼", "speed": "스피드폼",
    "no_cloak": "망토 없음", "plant_cloak": "초목도롱", "sunshine": "포지폼",
    "fan": "스핀로토무", "frost": "프로스트로토무", "heat": "히트로토무", "mow": "커트로토무", "wash": "워시로토무",
    "stereo": "스테레오로토무", "pokedex": "로토무 도감", "phone": "스마트폰 로토무", "drone": "드론 로토무",
    "rotomi": "로토미", "phone_paldea_ver": "스마트폰 로토무 (팔데아)",
    "sky": "스카이폼", "white_striped": "하얀줄무늬의 모습", "blue_striped": "파랑줄무늬의 모습",
    "galar_standard_mode": "가라르의 모습 노말모드", "unovan_zen_mode": "달마모드", "galar_zen_mode": "가라르의 모습 달마모드",
    "kyurem__black": "블랙큐레무", "kyurem__white": "화이트큐레무", "resolute": "각오의 모습", "pirouette": "스텝폼",
    "genesect__normal": "노말", "genesect__electric": "번개카세트", "genesect__fire": "블레이즈카세트",
    "genesect__ice": "프리즈카세트", "genesect__water": "아쿠아카세트", "ash": "지우개굴닌자",
    "meadow_pattern": "화원의 모양", "polar_pattern": "설원의 모양", "tundra_pattern": "설국의 모양",
    "continental_pattern": "대륙의 모양", "garden_pattern": "정원의 모양", "elegant_pattern": "고풍의 모양",
    "icy_snow_pattern": "빙설의 모양", "modern_pattern": "모던의 모양", "marine_pattern": "마린의 모양",
    "archipelago_pattern": "군도의 모양", "high_plains_pattern": "황야의 모양", "sandstorm_pattern": "사진의 모양",
    "river_pattern": "대하의 모양", "monsoon_pattern": "스콜의 모양", "savanna_pattern": "사바나의 모양",
    "sun_pattern": "태양의 모양", "ocean_pattern": "오션의 모양", "jungle_pattern": "정글의 모양",
    "fancy_pattern": "팬시한 모양", "poke_ball_pattern": "볼의 모양",
    "furfrou__natural": "야생의 모습", "heart_trim": "하트컷", "star_trim": "스타컷", "diamond_trim": "다이아컷",
    "debutante_trim": "레이디컷", "matron_trim": "마담컷", "dandy_trim": "젠틀컷", "la_reine_trim": "퀸컷",
    "kabuki_trim": "가부키컷", "pharaoh_trim": "킹덤컷", "blade": "블레이드폼",
    "10": "10%폼", "complete": "퍼펙트폼", "cell": "지가르데 셀", "core": "지가르데 코어", "unbound": "굴레를 벗어난 후파",
    "pom_pom_style": "파칙파칙스타일", "pa_u_style": "훌라훌라스타일", "sensu_style": "하늘하늘스타일",
    "midnight": "한밤중의 모습", "dusk": "황혼의 모습", "school": "군집의 모습", "meteor": "유성의 모습",
    "blue_core": "파란색 코어", "green_core": "초록색 코어", "indigo_core": "남색 코어", "orange_core": "주황색 코어",
    "red_core": "빨간색 코어", "violet_core": "보라색 코어", "yellow_core": "노란색 코어",
    "disguised": "둔갑한 모습", "busted": "들킨 모습", "dusk_mane": "황혼의 갈기", "dawn_wings": "새벽의 날개",
    "original_color": "500년 전의 색", "gulping": "그대로 삼킨 모습", "gorging": "통째로 삼킨 모습",
    "low_key": "로우한 모습", "noice_face": "나이스페이스", "hangry_mode": "배고픈 모양",
    "crowned_sword": "검왕", "crowned_shield": "방패왕", "eternamax": "무한다이맥스", "rapid_strike_style": "연격의 태세",
    "dada": "아빠", "ice_rider": "백마 탄 모습", "shadow_rider": "흑마 탄 모습", "bloodmoon": "붉은달",
    "family_of_four": "네 식구", "blue_plumage": "블루 페더", "yellow_plumage": "옐로 페더", "white_plumage": "화이트 페더",
    "hero": "마이티폼", "droopy": "늘어진 모습", "stretchy": "뻗은 모습", "three_segment": "세 마디폼",
    "roaming": "도보폼", "limited_build": "제한형태", "sprinting_build": "질주형태", "gliding_build": "활공형태",
    "low_power_mode": "저전력모드", "drive_mode": "드라이브모드", "glide_mode": "글라이드모드",
    "wellspring_mask": "우물의 가면", "hearthflame_mask": "화덕의 가면", "cornerstone_mask": "주춧돌의 가면",
    "wearing_teal_mask": "벽록의 가면", "wearing_cornerstone_mask": "주춧돌의 가면", "teal_mask_tera": "벽록의 가면 테라스탈",
    "wellspring_mask_tera": "우물의 가면 테라스탈", "hearthflame_mask_tera": "화덕의 가면 테라스탈",
    "cornerstone_mask_tera": "주춧돌의 가면 테라스탈", "terastal": "테라스탈폼", "stellar": "스텔라폼",
    # SV 도감 설명에만 나오는 폼 (fetch-serebii-sv.py 의 EXTRA_SUFFIX)
    "ruby_cream": "루비크림", "matcha_cream": "말차크림", "mint_cream": "민트크림", "lemon_cream": "레몬크림",
    "salted_cream": "솔티드크림", "ruby_swirl": "루비스월", "caramel_swirl": "캐러멜스월", "rainbow_swirl": "트리플스월",
    "female": "암컷의 모습", "primal": "원시회귀",
})


def form_ko(species, tag, fallback):
    return FORM_KO.get(f"{species}__{tag}") or FORM_KO.get(tag) or fallback


SHINY = "shiny"                              # 이로치 썸네일 키: <종>__shiny
SHINY_KO = "색이 다른 모습"

warnings = []


def warn(msg):
    warnings.append(msg)
    print(f"[경고] {msg}")


def onnx_names(path):
    """Ultralytics 가 ONNX 메타데이터에 넣는 names ({0: 'a', 1: 'b'} 문자열) → 출력 순서 라벨 목록."""
    import onnx
    meta = {p.key: p.value for p in onnx.load(str(path), load_external_data=False).metadata_props}
    if "names" not in meta:
        raise SystemExit(f"{path} 에 메타데이터 names 가 없다. 라벨 순서를 알 수 없어 넣지 않는다.")
    names = ast.literal_eval(meta["names"])
    return [names[i] for i in range(len(names))], meta


def load_classes():
    with open(paths.CSV_MAIN, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("enabled", "1").strip() == "1"]
    return rows


def serebii_rows():
    """세레비넷 manifest.csv 행 (드라이브). 없으면 빈 목록."""
    man = paths.SEREBII_RAW / "manifest.csv"
    if not man.exists():
        return None
    with open(man, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def variant_sources(srows, ids):
    """{종: [(썸네일 키, 표시 이름, 원본 경로)]} — 공식 아트의 폼(form_tag)과 이로치(sv_shiny 렌더). 기본 모습은 빼고."""
    out = {}
    seen = set()
    for r in srows or []:
        cid = r["class_id"]
        if cid not in ids:
            continue
        if r["kind"] == "art" and r["shiny"] == "0" and r["form_tag"]:
            key, name = f"{cid}__{r['form_tag']}", form_ko(cid, r["form_tag"], r["form_label"] or r["form_tag"])
        elif r["kind"] == "sv_shiny" and not r["form_tag"]:
            key, name = f"{cid}__{SHINY}", SHINY_KO
        else:
            continue
        if key in seen:          # 같은 폼의 그림이 여러 장이면 첫 장
            continue
        seen.add(key)
        out.setdefault(cid, []).append((key, name, paths.SEREBII_RAW / r["file"]))
    # 암컷·누락 폼 (fetch-form-images.py: PokeAPI HOME 렌더 + 세레비넷 SV 그림)
    man = paths.FORM_IMG_RAW / "manifest.json"
    if man.exists():
        for it in json.loads(man.read_text(encoding="utf-8")):
            key = f"{it['class_id']}__{it['tag']}"
            if it["class_id"] in ids and key not in seen and (paths.FORM_IMG_RAW / it["file"]).exists():
                seen.add(key)
                out.setdefault(it["class_id"], []).append((key, it["name_ko"], paths.FORM_IMG_RAW / it["file"]))
    else:
        warn(f"{man} 가 없다 → 암컷·누락 폼 없이 만든다 (fetch-form-images.py)")
    for v in out.values():       # 이로치, 암컷을 앞에
        v.sort(key=lambda x: (not x[0].endswith(f"__{SHINY}"), not x[0].endswith("__female")))
    return out


def gender_rates():
    """{전국도감 번호: PokeAPI gender_rate} — 암컷 비율 (8분의 n), -1 = 성별 없음. PokeAPI 캐시(로컬)에서 읽는다."""
    out = {}
    for f in (paths.POKEAPI_CACHE / "pokemon-species").glob("[0-9]*.json"):
        s = json.loads(f.read_text(encoding="utf-8"))
        out[s["id"]] = s.get("gender_rate", -1)
    if not out:
        warn(f"{paths.POKEAPI_CACHE} 에 pokemon-species 가 없다 → 성비 없이 만든다")
    return out


def build_dex(rows, form_labels, variants):
    gender_rate = gender_rates()
    db = json.load(open(paths.POKEDEX_KO, encoding="utf-8")) if paths.POKEDEX_KO.exists() else None
    if db is None:
        warn(f"{paths.POKEDEX_KO} 가 없다 → 도감 DB 는 CSV 의 이름만으로 만든다 (fetch-pokedex.py)")
        db = {}
    ids = {r["class_id"] for r in rows}
    species, no_flavor, missing = [], 0, []
    for r in sorted(rows, key=lambda r: int(r["dex_no"])):
        cid = r["class_id"]
        e = db.get(cid)
        if e is None:
            missing.append(cid)
            e = {}
        # tr: 영어 설명을 번역한 것 (SV·레전드 아르세우스 전용 127종, data/flavor-ko-translated.json). 앱에서 "번역" 표시용
        # 같은 문장이 여러 세대 버전에 묶여 있으면(레드·블루·리프그린) 세대마다 따로 나눠 각 세대 드롭다운에 나오게 한다
        flavor = [{"v": " · ".join(VERSION_KO.get(v, v) for v in vs), "t": fl["text"],
                   **({"g": g} if g else {}),
                   **({"tr": True} if fl.get("translated") else {}),
                   **({"f": f"{cid}__{fl['form']}"} if fl.get("form") else {})}   # 폼 설명: 다른 모습 키와 같은 <종>__<폼>
                  for fl in e.get("flavor_ko", []) if fl.get("text")
                  for g, vs in split_by_gen(fl["versions"])]
        # 세대 → 게임 발매 순 (VERSION_KO 의 순서)
        order = {k: i for i, k in enumerate(VERSION_KO.values())}
        flavor.sort(key=lambda f: (f.get("g") or 99, order.get(f["v"].split(" · ")[0], 999)))
        no_flavor += not flavor
        species.append({
            "id": cid,
            "no": int(r["dex_no"]),
            "gen": int(r["generation"]) if r.get("generation") else None,
            "name": e.get("name_ko") or r["name_ko"],
            "name_en": r.get("name_en", ""),
            "genus": e.get("genus_ko", ""),
            "types": e.get("types", []),
            "types_ko": e.get("types_ko", []),
            "h": e.get("height_m"),
            "w": e.get("weight_kg"),
            "flavor": flavor,
            "evo": [x for x in e.get("evolution", []) if x in ids],
            "variants": [{"id": k, "name": n} for k, n, _ in variants.get(cid, [])],
            **({"gender": True} if any(k.endswith("__female") for k, _, _ in variants.get(cid, [])) else {}),
            **({"gr": gender_rate[int(r["dex_no"])]} if int(r["dex_no"]) in gender_rate else {}),
        })
    if missing:
        warn(f"도감 DB 에 없는 클래스 {len(missing)}개: {missing[:5]}…")
    forms = {k: n for v in variants.values() for k, n, _ in v if not k.endswith(f"__{SHINY}")}
    # ② 폼 모델 라벨(PokeAPI 폼 이름) → 다른 모습 키(세레비넷 태그). 앱은 이 키로 폼 그림·이름·설명을 찾는다
    form_variant, unmatched = {}, []
    for lab in form_labels:
        if "__" not in lab:
            continue
        sp, fm = lab.split("__", 1)
        key = match_variant(sp, fm, [k for k, _, _ in variants.get(sp, [])])
        if key:
            form_variant[lab] = key
            forms.setdefault(lab, forms.get(key, form_ko(sp, fm, fm)))
        else:
            unmatched.append(lab)
            forms.setdefault(lab, form_ko(sp, fm, fm))
    if unmatched:
        warn(f"폼 모델 라벨 중 다른 모습(그림)과 못 맞춘 것 {len(unmatched)}개: {unmatched[:10]}")
    return {"version": 1, "species": species, "forms": forms, "form_variant": form_variant}, no_flavor


FORM_VARIANT_FIX = {"darmanitan__zen": "darmanitan__unovan_zen_mode"}   # 규칙으로 하나로 못 정하는 것


def match_variant(sp, fm, keys):
    """폼 라벨의 폼 부분(PokeAPI, 예 ice) → 다른 모습 키(예 calyrex__ice_rider). 정확히 → 밑줄 뺀 접두어 → 포함 → 거다이맥스."""
    if f"{sp}__{fm}" in FORM_VARIANT_FIX:
        return FORM_VARIANT_FIX[f"{sp}__{fm}"]
    tags = {k.split("__", 1)[1]: k for k in keys if not k.endswith(f"__{SHINY}") and not k.endswith("__female")}
    if fm in tags:
        return tags[fm]
    n = fm.replace("_", "")
    for test in (lambda t: t.replace("_", "").startswith(n), lambda t: n in t.replace("_", "")):
        hit = [k for t, k in tags.items() if test(t)]
        if len(hit) == 1:
            return hit[0]
    if fm.endswith("gmax") and "gmax" in tags:
        return tags["gmax"]
    return None


def make_thumb(src, dst):
    from PIL import Image
    im = Image.open(src).convert("RGBA")
    bbox = im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    if bbox:
        im = im.crop(bbox)
    im.thumbnail((THUMB, THUMB))
    canvas = Image.new("RGBA", (THUMB, THUMB), (0, 0, 0, 0))
    canvas.alpha_composite(im, ((THUMB - im.width) // 2, (THUMB - im.height) // 2))
    dst.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dst, optimize=True)


def make_icons(src_path):
    """원본 그림을 하늘색 그라데이션 정사각형 가운데에 놓아 iOS(1024)·Android(mipmap) 아이콘을 만든다."""
    from PIL import Image, ImageDraw
    src = Image.open(src_path).convert("RGBA")
    src = src.crop(src.getchannel("A").getbbox())
    S = 1024
    bg = Image.new("RGBA", (S, S))
    d = ImageDraw.Draw(bg)
    for y in range(S):
        t = y / S
        d.line((0, y, S, y), fill=(int(120 + 135 * t), int(200 + 55 * t), 255, 255))
    k = S * 0.82 / max(src.size)
    im = src.resize((int(src.width * k), int(src.height * k)), Image.LANCZOS)
    bg.alpha_composite(im, ((S - im.width) // 2, (S - im.height) // 2))
    big = bg.convert("RGB")
    big.save(paths.APP / "ios" / "Runner" / "Assets.xcassets" / "AppIcon.appiconset" / "icon.png")
    for p in (paths.APP / "android" / "app" / "src" / "main" / "res").glob("mipmap-*/ic_launcher.png"):
        w = Image.open(p).size[0]
        big.resize((w, w), Image.LANCZOS).save(p)


def thumb_sources(kind, ids, srows, variants):
    """{썸네일 id: 원본 이미지 경로}. 다운로드하지 않고 이미 있는 파일만 쓴다."""
    out = {}
    if kind == "originals":
        for cid in ids:
            p = paths.ORIGINALS / cid / f"{cid}.png"
            if p.exists():
                out[cid] = p
    elif kind == "serebii":
        if srows is None:
            warn(f"{paths.SEREBII_RAW / 'manifest.csv'} 가 없다 (드라이브 연결 확인) → 썸네일을 만들지 않는다")
            return out
        for r in srows:
            if r["kind"] == "art" and r["shiny"] == "0" and not r["form_tag"] and r["class_id"] in ids:
                out.setdefault(r["class_id"], paths.SEREBII_RAW / r["file"])
        for v in variants.values():
            for key, _, src in v:
                out.setdefault(key, src)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--thumbs", choices=["keep", "originals", "serebii"], default="keep",
                    help="썸네일 출처 (기본 keep: 이미 만든 것 그대로)")
    args = ap.parse_args()

    out = paths.APP_ASSETS
    for d in ("models", "thumbs", "audio"):
        (out / d).mkdir(parents=True, exist_ok=True)
        (out / d / ".gitkeep").touch()
    # 이전 실행의 모델·라벨은 지운다 (모델이 사라졌는데 옛 모델이 남아 라벨과 어긋나는 것을 막는다)
    for p in [*(out / "models").glob("*.onnx"), out / "species_labels.txt", out / "form_labels.txt", out / "dex.json", out / "model_info.json"]:
        p.unlink(missing_ok=True)

    rows = load_classes()
    ids = [r["class_id"] for r in rows]
    expected = sorted(ids + [NONE])

    model_info = {}   # 앱 설정·진단 화면에 보여 줄 모델 정보 (어느 모델인지 구분하려고: 파일 크기가 같아도 해시가 다르다)

    def describe(path, labels, meta):
        import hashlib
        import time
        return {
            "run": path.parent.parent.name,                       # 예: train_v2
            "bytes": path.stat().st_size,
            "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime)),
            "sha": hashlib.sha1(path.read_bytes()).hexdigest()[:8],
            "classes": len(labels),
            "imgsz": meta.get("imgsz", ""),
        }

    # ① 종 모델 + 라벨
    if paths.BEST_ONNX.exists():
        labels, meta = onnx_names(paths.BEST_ONNX)
        if NONE not in labels:
            warn("종 모델 라벨에 none 이 없다 → 앱은 확신도 임계값으로만 거부한다")
        if set(labels) != set(expected):
            extra, lack = set(labels) - set(expected), set(expected) - set(labels)
            warn(f"종 모델 라벨이 list-poke.csv 와 다르다: 모델에만 {sorted(extra)[:5]} / CSV 에만 {sorted(lack)[:5]}")
        shutil.copy(paths.BEST_ONNX, out / "models" / "species.onnx")
        model_info["species"] = describe(paths.BEST_ONNX, labels, meta)
        print(f"종 모델: {paths.BEST_ONNX} (imgsz={meta.get('imgsz', '?')}, 클래스 {len(labels)})")
    else:
        labels = expected
        warn(f"종 모델이 없다: {paths.BEST_ONNX} → species_labels.txt 는 정렬된 자리표시 (앱은 인식 없이 도감만)")
    (out / "species_labels.txt").write_text("\n".join(labels) + "\n", encoding="utf-8")

    # ② 폼 모델 + 라벨 (선택)
    form_labels = []
    if paths.FORM_ONNX.exists():
        form_labels, fmeta = onnx_names(paths.FORM_ONNX)
        bad = [lab for lab in form_labels if lab.split("__", 1)[0] not in ids]
        if bad:
            warn(f"폼 라벨의 종이 클래스 목록에 없다: {bad[:5]}")
        shutil.copy(paths.FORM_ONNX, out / "models" / "forms.onnx")
        model_info["forms"] = describe(paths.FORM_ONNX, form_labels, fmeta)
        (out / "form_labels.txt").write_text("\n".join(form_labels) + "\n", encoding="utf-8")
        print(f"폼 모델: {paths.FORM_ONNX} (imgsz={fmeta.get('imgsz', '?')}, 폼 클래스 {len(form_labels)})")
    else:
        warn(f"폼 모델이 없다: {paths.FORM_ONNX} → 앱은 ② 없이 종만 판정한다")

    (out / "model_info.json").write_text(json.dumps(model_info, ensure_ascii=False, indent=1), encoding="utf-8")

    # 다른 모습(폼·이로치) 목록. 세레비넷 manifest 에서 만들고, 앱은 썸네일이 있는 것만 보여 준다
    srows = serebii_rows()
    if srows is None:
        warn(f"{paths.SEREBII_RAW / 'manifest.csv'} 가 없다 → 다른 모습(폼·이로치) 목록 없이 만든다")
    variants = variant_sources(srows, set(ids))

    # 도감 DB
    dex, no_flavor = build_dex(rows, form_labels, variants)
    with open(out / "dex.json", "w", encoding="utf-8") as f:
        json.dump(dex, f, ensure_ascii=False, separators=(",", ":"))

    # 썸네일 (선택). 캐시(paths.THUMB_CACHE/<출처>/)에 만들고 복사한다
    if args.thumbs != "keep":
        for p in (out / "thumbs").glob("*.png"):
            p.unlink()
        cache = paths.THUMB_CACHE / args.thumbs
        srcs = thumb_sources(args.thumbs, set(ids), srows, variants)
        for key, src in srcs.items():
            c = cache / f"{key}.png"
            if not c.exists():
                try:
                    make_thumb(src, c)
                except Exception as e:  # 깨진 이미지 하나 때문에 전체를 멈추지 않는다
                    warn(f"썸네일 실패 {key}: {e}")
                    continue
            shutil.copy(c, out / "thumbs" / c.name)
        n_var = sum(len(v) for v in variants.values())
        print(f"다른 모습 {n_var}개 ({len(variants)}종)")
        lack = [i for i in ids if i not in srcs]
        if lack:
            warn(f"썸네일 원본이 없는 종 {len(lack)}개 ({args.thumbs}): {lack[:5]}…")
    n_thumb = len(list((out / "thumbs").glob("*.png")))
    if n_thumb == 0:
        warn("썸네일이 없다 → 앱은 번호 자리표시를 보여 준다 (--thumbs originals|serebii)")

    # 음성 (있으면). wav(16kHz PCM, 1025개 243MB) 그대로는 앱이 너무 커져서 macOS afconvert 로 AAC 32kbps m4a 로 줄여 넣는다.
    # 캐시 paths.AUDIO_APP_CACHE 에 만들고 wav 가 더 새로우면 다시 만든다. 원본 wav 는 건드리지 않는다.
    for p in [*(out / "audio").glob("*.wav"), *(out / "audio").glob("*.m4a")]:
        p.unlink()
    n_audio = 0
    if paths.AUDIO.exists():
        paths.AUDIO_APP_CACHE.mkdir(parents=True, exist_ok=True)
        conv = shutil.which("afconvert")
        if conv is None:
            warn("afconvert 가 없다 (macOS 전용) → wav 를 그대로 넣는다 (앱이 크다)")
        for cid in ids:
            w = paths.AUDIO / f"{cid}.wav"
            if not w.exists():
                continue
            if conv is None:
                shutil.copy(w, out / "audio" / w.name)
            else:
                m = paths.AUDIO_APP_CACHE / f"{cid}.m4a"
                if not m.exists() or m.stat().st_mtime < w.stat().st_mtime:
                    subprocess.run([conv, "-f", "m4af", "-d", "aac", "-b", "32000", str(w), str(m)],
                                   check=True, capture_output=True)
                shutil.copy(m, out / "audio" / m.name)
            n_audio += 1
    if n_audio == 0:
        warn(f"안내 음성이 없다: {paths.AUDIO} → 앱은 음성 없이 동작한다")

    # 앱 아이콘 (원본이 포켓몬 공식 그림이라 git 제외. 원본이 없으면 지금 아이콘을 그대로 둔다)
    if paths.APP_ICON_SRC.exists():
        make_icons(paths.APP_ICON_SRC)
    else:
        warn(f"아이콘 원본이 없다: {paths.APP_ICON_SRC} → 앱 아이콘을 바꾸지 않는다")

    mb = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 1e6
    none_idx = labels.index(NONE) if NONE in labels else None
    print(f"\n{out}")
    print(f"도감 {len(dex['species'])}종 (설명 없음 {no_flavor}) | 종 라벨 {len(labels)} (none 인덱스 {none_idx}) | "
          f"폼 라벨 {len(form_labels)} | 썸네일 {n_thumb} | 음성 {n_audio} | {mb:.1f}MB | 경고 {len(warnings)}")


if __name__ == "__main__":
    main()
