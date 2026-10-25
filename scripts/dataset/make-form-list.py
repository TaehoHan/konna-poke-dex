"""② 폼 모델의 클래스 목록: data/form-list.csv (PokeAPI 폼 이름이 기준)

  class_id,form,form_class,category,include,by,note
    form        PokeAPI 포켓몬 이름에서 종 이름을 뺀 것 ('-'→'_'). 예: vulpix-alola → alola, charizard-mega-x → mega_x
    form_class  ② 모델의 클래스 이름 = <class_id>__<form>. 기본형은 종 이름 그대로 (이 파일에 행이 없다)
    category    regional | mega | gmax | other
    include     1 = 폼 모델 클래스로 쓴다, 0 = 종으로 합친다 (토템·모자 피카츄·크기·성별·색만 다른 폼 등)
    by          auto | manual. 다시 실행해도 manual 행은 그대로 둔다 (사람이 include 를 바꾸면 by 를 manual 로)

기본 포함 기준 (DESIGN.md 2.1, 2026-10-25): 리전폼(토템 제외), 메가·원시회귀, 거다이맥스, 그리고 OTHER_INCLUDE 의 생김새가 크게 다른 폼.
폼 모델에 들어가는 종 = include=1 인 폼이 하나라도 있는 종. 그 종의 기본형도 클래스가 된다.

사용: .venv/bin/python scripts/dataset/make-form-list.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/ (paths.py)
import paths  # noqa: E402
import csv
import json

FIELDS = ["class_id", "form", "form_class", "category", "include", "by", "note"]
REGIONS = ("alola", "galar", "hisui", "paldea")
EXCLUDE_WORDS = ("totem", "cap", "cosplay", "starter")
# 리전·메가·거다이맥스 밖에서 생김새가 크게 달라 폼 모델로 가르는 폼 (종_폼)
OTHER_INCLUDE = {
    "castform": {"sunny", "rainy", "snowy"},
    "deoxys": {"attack", "defense", "speed"},
    "wormadam": {"sandy", "trash"},
    "rotom": {"heat", "wash", "frost", "fan", "mow"},
    "dialga": {"origin"}, "palkia": {"origin"}, "giratina": {"origin"},
    "shaymin": {"sky"},
    "darmanitan": {"zen"},
    "tornadus": {"therian"}, "thundurus": {"therian"}, "landorus": {"therian"}, "enamorus": {"therian"},
    "kyurem": {"black", "white"},
    "meloetta": {"pirouette"},
    "greninja": {"ash"},
    "aegislash": {"blade"},
    "zygarde": {"10", "complete"},
    "hoopa": {"unbound"},
    "oricorio": {"pom_pom", "pau", "sensu"},
    "lycanroc": {"midnight", "dusk"},
    "wishiwashi": {"school"},
    "necrozma": {"dusk", "dawn", "ultra"},
    "eiscue": {"noice"},
    "morpeko": {"hangry"},
    "zacian": {"crowned"}, "zamazenta": {"crowned"},
    "eternatus": {"eternamax"},
    "urshifu": {"rapid_strike"},
    "calyrex": {"ice", "shadow"},
    "ursaluna": {"bloodmoon"},
    "palafin": {"hero"},
    "tatsugiri": {"droopy", "stretchy"},
    "gimmighoul": {"roaming"},
    "ogerpon": {"wellspring_mask", "hearthflame_mask", "cornerstone_mask"},
    "terapagos": {"terastal", "stellar"},
}


def category(form):
    if form.split("_")[0] in REGIONS or any(f"_{r}" in form for r in REGIONS):
        return "regional"
    if form.startswith("mega") or form == "primal":
        return "mega"
    if "gmax" in form:
        return "gmax"
    return "other"


def default_include(cid, form, cat):
    if any(w in form for w in EXCLUDE_WORDS):
        return 0, "토템·모자·코스프레·스타터는 종으로 합친다"
    if cat in ("regional", "mega", "gmax"):
        return 1, ""
    if form in OTHER_INCLUDE.get(cid, ()):
        return 1, "생김새가 크게 다른 폼"
    return 0, "크기·성별·색·모드처럼 차이가 작아 종으로 합친다"


def main():
    with paths.CSV_MAIN.open(encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["enabled"] == "1"]
    manual = {}
    if paths.FORM_LIST.exists():
        with paths.FORM_LIST.open(encoding="utf-8", newline="") as f:
            manual = {(r["class_id"], r["form"]): r for r in csv.DictReader(f) if r["by"] == "manual"}
    out = []
    for r in rows:
        sp = json.loads((paths.POKEAPI_CACHE / "pokemon-species" / f"{r['dex_no']}.json").read_text(encoding="utf-8"))
        for v in sp["varieties"]:
            if v["is_default"]:
                continue
            name = v["pokemon"]["name"]
            form = (name[len(sp["name"]) + 1:] if name.startswith(sp["name"] + "-") else name).replace("-", "_")
            cid = r["class_id"]
            if (cid, form) in manual:
                out.append(manual[(cid, form)])
                continue
            cat = category(form)
            inc, note = default_include(cid, form, cat)
            out.append({"class_id": cid, "form": form, "form_class": f"{cid}__{form}", "category": cat,
                        "include": inc, "by": "auto", "note": note})
    with paths.FORM_LIST.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(out)
    inc = [r for r in out if str(r["include"]) == "1"]
    species = sorted({r["class_id"] for r in inc})
    by_cat = {}
    for r in inc:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    print(f"PokeAPI 폼 {len(out)}개 중 폼 모델 포함 {len(inc)} {by_cat}, 대상 종 {len(species)} "
          f"→ 클래스 {len(inc) + len(species)} (폼 {len(inc)} + 기본형 {len(species)})  → {paths.FORM_LIST.relative_to(paths.ROOT)}")


if __name__ == "__main__":
    main()
