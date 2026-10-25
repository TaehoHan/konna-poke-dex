"""출처별 라벨 -> class_id(+폼) 매핑 초안: data/label-map/<source_id>.csv

  source_label,class_id,form,count,by,note
    source_label  출처의 라벨 그대로 (번호 라벨이면 번호)
    class_id      list-poke.csv 의 종. 비어 있으면 미매칭 → 사람이 채우거나 skip
    form          폼 태그 (PokeAPI 포켓몬 이름에서 종 이름을 뺀 것, '-'→'_'. 기본형은 빈 값). 폼 모델(②)용
    count         그 라벨의 이미지(행) 수
    by            auto | manual. 다시 실행해도 manual 행은 그대로 둔다 (사람이 고친 행은 by 를 manual 로 바꾼다)
    note          매칭 근거·주의

출처별 라벨 방식 (2026-10-24 확인):
  hf-jjmack-gen1-9    parquet `label` = 전국도감 번호(1~1025). `name` 은 'Wo-Chien'→'Wo' 처럼 잘려서 번호를 쓴다
  kg-gen1-9-complete  manifest.csv `label` 1~1290, 파일명 '<label>_<i>_<PokeAPI 포켓몬 이름>.png'. 이름으로 종+폼
  kg-1000-pokemon     pokemon-dataset-1000/dataset/<PokeAPI 포켓몬 이름>/ (train/val/test 는 dataset 을 나눈 사본)
  hf-pokemon-cards    train.csv `name` = 카드 이름 ('Absol G', 'Pikachu V', "Team Rocket's Meowth"). 접두·접미를 떼고 매칭
                      (이미지는 들어 있지 않고 image_url 만 있다 → 따로 받아야 한다)
  serebii             이미 도감 번호로 받았다 (raw/serebii/manifest.csv). 매핑 불필요

이름 매칭 기준: PokeAPI 캐시(workspace/raw/pokeapi/pokemon-species)의 종 이름·영문 이름·폼(varieties) 이름.
정규화 키: 소문자 → 악센트 제거 → ♀/♂ → f/m → 영숫자만.

사용:
  .venv/bin/python scripts/collect/make-label-map.py                 # 받은 출처 전부
  .venv/bin/python scripts/collect/make-label-map.py kg-1000-pokemon
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/ (paths.py)
import paths  # noqa: E402
import argparse
import collections
import csv
import json
import re
import unicodedata

FIELDS = ["source_label", "class_id", "form", "count", "by", "note"]


def norm(s):
    s = s.replace("♀", "f").replace("♂", "m")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


class Dex:
    """PokeAPI 캐시에서 만든 이름표."""

    def __init__(self):
        with paths.CSV_MAIN.open(encoding="utf-8", newline="") as f:
            self.rows = {int(r["dex_no"]): r for r in csv.DictReader(f)}
        self.by_key = {}        # 정규화 키 → (class_id, form)
        self.pokemon = {}       # PokeAPI 포켓몬 이름 → (class_id, form)
        for dex, r in self.rows.items():
            sp = json.loads((paths.POKEAPI_CACHE / "pokemon-species" / f"{dex}.json").read_text(encoding="utf-8"))
            cid = r["class_id"]
            for v in sp["varieties"]:
                pname = v["pokemon"]["name"]
                form = "" if v["is_default"] else self.form_of(sp["name"], pname)
                self.pokemon[pname] = (cid, form)
                self.by_key.setdefault(norm(pname), (cid, form))
            for k in (sp["name"], r["name_en"], cid):
                self.by_key[norm(k)] = (cid, "")

    @staticmethod
    def form_of(species, pname):
        rest = pname[len(species) + 1:] if pname.startswith(species + "-") else pname
        return rest.replace("-", "_")

    def by_dex(self, n):
        r = self.rows.get(n)
        return r["class_id"] if r else ""

    def match_name(self, name):
        if name in self.pokemon:
            return self.pokemon[name]
        return self.by_key.get(norm(name), ("", ""))


# ---------- 출처별 라벨 읽기: [(source_label, count, sample)] ----------
def labels_jjmack(root):
    import pyarrow.parquet as pq
    cnt, sample = collections.Counter(), {}
    for p in sorted(root.glob("*.parquet")):
        t = pq.read_table(p, columns=["label", "name"]).to_pydict()
        for lab, name in zip(t["label"], t["name"]):
            cnt[lab] += 1
            sample.setdefault(lab, name)
    return [(str(k), cnt[k], sample[k]) for k in sorted(cnt)]


def labels_kg_gen19(root):
    cnt, name = collections.Counter(), {}
    with (root / "pokemon_augmented_manifest.csv").open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            lab = int(r["label"])
            cnt[lab] += 1
            name.setdefault(lab, r["filename"].split("_", 2)[2].rsplit(".", 1)[0])
    return [(str(k), cnt[k], name[k]) for k in sorted(cnt)]


def labels_kg_1000(root):
    base = root / "pokemon-dataset-1000" / "dataset"
    return [(d.name, sum(1 for f in d.iterdir() if f.is_file()), d.name)
            for d in sorted(base.iterdir()) if d.is_dir()]


def labels_cards(root):
    cnt = collections.Counter()
    with (root / "train.csv").open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            cnt[r["name"].strip()] += 1
    return [(k, cnt[k], k) for k in sorted(cnt)]


# ---------- 매핑 ----------
CARD_PREFIX = re.compile(
    r"^(?:(?:dark|light|shining|radiant|rocket's|team rocket's|team aqua's|team magma's|"
    r"[a-z.]+'s|alolan|galarian|hisuian|paldean|mega|m|primal|ancient|black|white|"
    r"origin forme|single strike|rapid strike|ice rider|shadow rider|ultra|dusk mane|dawn wings|"
    r"flying|surfing|detective|special delivery|imposter)\s+)+", re.I)
CARD_SUFFIX = re.compile(
    r"(?:[\s-]+(?:vmax|vstar|v-union|v|gx|ex|lv\.?\s?x|lv\.?\s?\d+|gl|g|fb|c|e4|4|break|prime|legend|star|"
    r"tag team|\(.*?\)|\d+)|\s*[δ☆◇])+$", re.I)   # 'Absol-EX', 'Pikachu V', 'Rayquaza ☆'. 'Porygon-Z' 의 z 는 목록에 없다
CARD_REGION = {"alolan": "alola", "galarian": "galar", "hisuian": "hisui", "paldean": "paldea"}


def map_card(dex, name):
    """카드 이름 → (class_id, form, note). '&' 태그팀·여러 마리는 skip 후보로 비운다."""
    if "&" in name or " and " in name.lower():
        return "", "", "여러 포켓몬(태그팀) → skip 후보"
    low = name.lower()
    form = next((f for k, f in CARD_REGION.items() if low.startswith(k + " ") or f" {k} " in f" {low} "), "")
    if re.match(r"^(m|mega)\s", low):
        form = "mega"
    core = CARD_SUFFIX.sub("", CARD_PREFIX.sub("", name)).strip()
    cid, f2 = dex.match_name(core)
    if not cid:   # 'Pikachu Libre' 처럼 뒤에 붙은 말: 앞 단어부터 줄여 가며
        words = core.split()
        for n in range(len(words) - 1, 0, -1):
            cid, f2 = dex.match_name(" ".join(words[:n]))
            if cid:
                break
    return cid, form or f2, (f"카드 이름 '{core}'" if cid else f"미매칭 (core='{core}')")


SOURCES = {
    "hf-jjmack-gen1-9": labels_jjmack,
    "kg-gen1-9-complete": labels_kg_gen19,
    "kg-1000-pokemon": labels_kg_1000,
    "hf-pokemon-cards": labels_cards,
}


def auto_map(dex, sid, label, sample):
    if sid == "hf-jjmack-gen1-9":
        cid = dex.by_dex(int(label))
        ok = norm(sample) and norm(dex.rows[int(label)]["name_en"]).startswith(norm(sample)) if cid else False
        return cid, "", f"도감 번호 (name='{sample}'{'' if ok else ', 이름 불일치 확인'})"
    if sid == "kg-gen1-9-complete":
        cid, form = dex.match_name(sample)
        n = int(label)
        if cid and n <= 1025 and dex.by_dex(n) != cid:
            return cid, form, f"이름 '{sample}' 기준. 번호 {n} 과 종이 다름 → 확인"
        return cid, form, f"이름 '{sample}'" if cid else f"미매칭 (이름 '{sample}')"
    if sid == "kg-1000-pokemon":
        cid, form = dex.match_name(label)
        return cid, form, "PokeAPI 포켓몬 이름" if cid else "미매칭"
    if sid == "hf-pokemon-cards":
        return map_card(dex, label)
    raise KeyError(sid)


def build(dex, sid):
    root = paths.RAW / sid
    if not (root / "_SOURCE.json").exists():
        print(f"{sid}: 받지 않았다 ({root})")
        return
    out = paths.LABEL_MAP / f"{sid}.csv"
    manual = {}
    if out.exists():
        with out.open(encoding="utf-8", newline="") as f:
            manual = {r["source_label"]: r for r in csv.DictReader(f) if r["by"] == "manual"}
    rows = []
    for label, count, sample in SOURCES[sid](root):
        if label in manual:
            r = dict(manual[label])
            r["count"] = count
            rows.append(r)
            continue
        cid, form, note = auto_map(dex, sid, label, sample)
        rows.append({"source_label": label, "class_id": cid, "form": form, "count": count, "by": "auto", "note": note})
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    matched = [r for r in rows if r["class_id"] and r["class_id"] != "skip"]
    unmatched = [r for r in rows if not r["class_id"]]
    check = [r for r in rows if "확인" in r["note"]]
    n_img = sum(int(r["count"]) for r in rows)
    n_ok = sum(int(r["count"]) for r in matched)
    species = {r["class_id"] for r in matched}
    forms = {(r["class_id"], r["form"]) for r in matched if r["form"]}
    print(f"{sid}: 라벨 {len(rows)} → 매칭 {len(matched)} (이미지 {n_ok}/{n_img}, 종 {len(species)}, 폼 {len(forms)}), "
          f"미매칭 {len(unmatched)}, 확인 필요 {len(check)}, manual 유지 {len(manual)}  → {out.relative_to(paths.ROOT)}")
    for r in (unmatched + check)[:15]:
        print(f"    {r['source_label']!r:40} {r['count']:>5}  {r['note']}")
    missing = sorted(set(r["class_id"] for r in dex.rows.values()) - species, key=lambda c: next(
        d for d, r in dex.rows.items() if r["class_id"] == c))
    if missing:
        print(f"    이 출처에 없는 종 {len(missing)}: {', '.join(missing[:15])}{' …' if len(missing) > 15 else ''}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="*", help=f"출처 id (기본: 전부) {list(SOURCES)}")
    args = ap.parse_args()
    paths.require_drive()
    unknown = set(args.sources) - set(SOURCES)
    if unknown:
        sys.exit(f"매핑을 만들 수 없는 출처: {sorted(unknown)} (가능: {list(SOURCES)})")
    dex = Dex()
    for sid in args.sources or SOURCES:
        build(dex, sid)


if __name__ == "__main__":
    main()
