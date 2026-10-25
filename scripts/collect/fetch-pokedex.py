"""PokeAPI -> data/list-poke.csv (클래스 단일 기준) + data/pokedex-ko.json (한국어 도감 해석 DB).

응답은 workspace/raw/pokeapi/<endpoint>/<key>.json 에 캐시하고, 캐시가 있으면 다시 요청하지 않는다.
네트워크 요청 사이에는 --delay 초(기본 0.2) 간격을 둔다.

허락 관문: data/sources.yaml 의 `pokeapi` 출처가 `enabled: true` 일 때만 네트워크 요청을 한다.
           --dry-run 은 네트워크 없이 캐시 상태와 필요한 요청 수만 출력한다.

사용:
  .venv/bin/python scripts/collect/fetch-pokedex.py --dry-run             # 요청 수만 확인
  .venv/bin/python scripts/collect/fetch-pokedex.py                       # 전국도감 1~1025 전부, 모두 enabled=1

CSV 에는 전국도감 전 종을 넣는다. 첫 학습부터 1025종 전부가 대상이다 (2026-10-24 결정). enabled 는 특정 종을 뺄 때만 0.
PokeAPI 의 종 수가 --max-dex 보다 많으면(새 세대 추가) 경고한다.

요청 대상 (종 하나당): pokemon-species/<n>, pokemon/<기본 폼>, evolution-chain/<id>(계열당 1회),
                       그리고 type/<name> 18개(한국어 타입 이름).

enabled 열: 기존 CSV 에 있는 종은 그 값을 유지한다 (사람이 바꾼 단계 설정을 덮어쓰지 않는다).
            새로 생기는 행은 1 (--enable-gen 을 주면 그 세대 이하만 1). --reset-enabled 로 전부 다시 계산한다.
none 클래스는 CSV 에 넣지 않는다 (이미지 클래스가 아니라 학습 데이터 단계에서 붙는다).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/ (paths.py)
import paths  # noqa: E402
import argparse
import csv
import json
import time
from datetime import date

import requests
import yaml

API = "https://pokeapi.co/api/v2/"
SOURCE_ID = "pokeapi"
CACHE = paths.POKEAPI_CACHE
USER_AGENT = "konna-poke-dex/0.1 (personal pokedex project; cached, rate-limited)"
ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}
TYPES = ["normal", "fighting", "flying", "poison", "ground", "rock", "bug", "ghost", "steel",
         "fire", "water", "grass", "electric", "psychic", "ice", "dragon", "dark", "fairy"]
CSV_FIELDS = ["dex_no", "class_id", "name_ko", "name_en", "generation", "form", "enabled"]


class Client:
    """캐시 우선 PokeAPI 클라이언트. offline=True 면 캐시에 없을 때 None 을 돌려준다."""

    def __init__(self, delay, offline):
        self.delay = delay
        self.offline = offline
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.n_net = 0
        self.n_cache = 0
        self.missing = []
        self._last = 0.0

    def get(self, endpoint, key, url=None):
        path = CACHE / endpoint / f"{key}.json"
        if path.exists():
            self.n_cache += 1
            return json.loads(path.read_text(encoding="utf-8"))
        if self.offline:
            self.missing.append(f"{endpoint}/{key}")
            return None
        data = self._fetch(url or f"{API}{endpoint}/{key}/")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        self.n_net += 1
        return data

    def _fetch(self, url):
        for attempt in range(5):
            wait = self._last + self.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.session.get(url, timeout=30)
            except requests.RequestException as e:
                err = str(e)
            else:
                if r.status_code == 200:
                    return r.json()
                if r.status_code not in (429, 500, 502, 503, 504):
                    r.raise_for_status()
                err = f"HTTP {r.status_code}"
            backoff = 2 ** attempt * 2
            print(f"  재시도 {attempt + 1}/5 ({err}) {backoff}s 후: {url}", file=sys.stderr)
            time.sleep(backoff)
        raise RuntimeError(f"요청 실패: {url}")


def id_from_url(url):
    return int(url.rstrip("/").rsplit("/", 1)[1])


def class_id_of(species_name):
    return species_name.lower().replace("-", "_")


def pick_lang(entries, lang, field="name"):
    for e in entries:
        if e["language"]["name"] == lang:
            return e[field]
    return ""


def clean_text(s):
    # 게임 원문의 줄바꿈·페이지 넘김(\f)·소프트 하이픈을 공백 하나로
    s = s.replace("­", "").replace("\f", " ").replace("\n", " ")
    return " ".join(s.split())


def flavor_ko(species):
    """한국어 도감 설명. 같은 문장은 하나로 합치고 나온 버전을 모은다."""
    out, index = [], {}
    for e in species["flavor_text_entries"]:
        if e["language"]["name"] != "ko":
            continue
        text = clean_text(e["flavor_text"])
        ver = e["version"]["name"]
        if text in index:
            index[text]["versions"].append(ver)
        else:
            index[text] = {"versions": [ver], "text": text}
            out.append(index[text])
    return out


def flatten_chain(node):
    """진화 트리를 너비 우선으로 펼친 class_id 목록 (분기 진화도 한 줄로)."""
    out, queue = [], [node]
    while queue:
        n = queue.pop(0)
        out.append(class_id_of(n["species"]["name"]))
        queue.extend(n["evolves_to"])
    return out


def load_sources():
    cfg = yaml.safe_load(paths.SOURCES.read_text(encoding="utf-8"))
    for s in cfg["sources"]:
        if s["id"] == SOURCE_ID:
            return s
    sys.exit(f"{paths.SOURCES} 에 '{SOURCE_ID}' 출처가 없다.")


def read_existing_enabled():
    if not paths.CSV_MAIN.exists():
        return {}
    with paths.CSV_MAIN.open(encoding="utf-8", newline="") as f:
        return {r["class_id"]: r["enabled"] for r in csv.DictReader(f)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-dex", type=int, default=1025, help="전국도감 1번부터 이 번호까지 (기본 1025)")
    ap.add_argument("--enable-gen", type=int, default=99, help="새 행은 이 세대 이하만 enabled=1 (기본: 전부)")
    ap.add_argument("--reset-enabled", action="store_true", help="기존 CSV 의 enabled 값을 무시하고 다시 계산")
    ap.add_argument("--delay", type=float, default=0.2, help="네트워크 요청 간격(초)")
    ap.add_argument("--dry-run", action="store_true", help="네트워크 없이 캐시 상태와 필요한 요청 수만 출력")
    args = ap.parse_args()

    src = load_sources()
    if not args.dry_run and not src.get("enabled"):
        sys.exit(f"sources.yaml 의 '{SOURCE_ID}' 가 enabled: false 다. 사용자 허락을 받은 뒤 true 로 바꾸고 실행한다.\n"
                 f"먼저 --dry-run 으로 요청 수를 확인한다.")

    cli = Client(args.delay, offline=args.dry_run)
    dex_range = range(1, args.max_dex + 1)

    # 종 수 확인 (목록 1건). 캐시 키 _index
    index = cli.get("pokemon-species", "_index", url=f"{API}pokemon-species/?limit=1")
    if index is not None and index["count"] > args.max_dex:
        print(f"주의: PokeAPI 종 수 {index['count']} > --max-dex {args.max_dex}. 새 종을 포함하려면 --max-dex 를 올린다.")

    # 타입 한국어 이름 (18개)
    types_ko = {}
    for t in TYPES:
        td = cli.get("type", t)
        if td is not None:
            types_ko[t] = pick_lang(td["names"], "ko")

    rows, db, chains = [], {}, {}
    for n in dex_range:
        sp = cli.get("pokemon-species", n)
        if sp is None:
            continue
        default = next(v for v in sp["varieties"] if v["is_default"])
        pk = cli.get("pokemon", id_from_url(default["pokemon"]["url"]))
        chain_id = id_from_url(sp["evolution_chain"]["url"])
        if chain_id not in chains:
            ch = cli.get("evolution-chain", chain_id)
            chains[chain_id] = flatten_chain(ch["chain"]) if ch else []
        if pk is None:
            continue

        cid = class_id_of(sp["name"])
        gen = ROMAN[sp["generation"]["name"].split("-", 1)[1]]
        types = [t["type"]["name"] for t in sorted(pk["types"], key=lambda t: t["slot"])]
        name_ko = pick_lang(sp["names"], "ko")
        rows.append({"dex_no": sp["id"], "class_id": cid, "name_ko": name_ko,
                     "name_en": pick_lang(sp["names"], "en"), "generation": gen, "form": ""})
        db[cid] = {
            "dex_no": sp["id"],
            "name_ko": name_ko,
            "genus_ko": pick_lang(sp["genera"], "ko", "genus"),
            "types": types,
            "types_ko": [types_ko.get(t, "") for t in types],
            "height_m": pk["height"] / 10,
            "weight_kg": pk["weight"] / 10,
            "flavor_ko": flavor_ko(sp),
            "evolution": chains[chain_id],
            "artwork_url": pk["sprites"]["other"]["official-artwork"]["front_default"],
        }
        if not args.dry_run and n % 25 == 0:
            print(f"  {n}/{args.max_dex}  (요청 {cli.n_net}, 캐시 {cli.n_cache})")

    if args.dry_run:
        n_sp_missing = sum(1 for m in cli.missing if m.startswith("pokemon-species/") and not m.endswith("_index"))
        n_cached_sp = len(dex_range) - n_sp_missing
        # 캐시에 종이 없으면 pokemon/evolution-chain 요청 수는 알 수 없으니 추정한다 (전국 1025종 = 계열 약 540개)
        est_chain = round(n_sp_missing * 0.53)
        est = len(cli.missing) + n_sp_missing + est_chain
        print(f"[dry-run] 대상: 전국도감 1~{args.max_dex}  캐시된 종 {n_cached_sp}/{len(dex_range)}")
        print(f"[dry-run] 캐시에 없는 확인된 요청 {len(cli.missing)}건"
              f" + 종 정보를 받아야 알 수 있는 pokemon {n_sp_missing}건, evolution-chain 약 {est_chain}건")
        print(f"[dry-run] 예상 네트워크 요청 약 {est}건, 간격 {args.delay}s → 약 {est * args.delay / 60:.1f}분 이상")
        print(f"[dry-run] 캐시 위치: {CACHE}")
        print(f"[dry-run] sources.yaml '{SOURCE_ID}' enabled: {src.get('enabled')}")
        return

    # list-poke.csv
    old = {} if args.reset_enabled else read_existing_enabled()
    for r in rows:
        r["enabled"] = old.get(r["class_id"], "1" if r["generation"] <= args.enable_gen else "0")
    rows.sort(key=lambda r: r["dex_no"])
    paths.CSV_MAIN.parent.mkdir(parents=True, exist_ok=True)
    with paths.CSV_MAIN.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)

    # 영어 도감 설명 번역본을 덧붙인다 (2026-10-25 사용자 결정). PokeAPI 한국어 설명은 X·Y~소드·실드뿐이라
    #  - 스칼렛·바이올렛/레전드 아르세우스에만 나오는 종(127)은 한국어 설명이 없다 → 그 게임 설명 번역
    #  - 1~5세대 게임(레드~블랙2·화이트2) 설명도 한국어가 없다 → 번역 (세대별 설명, 글로만 제공)
    # 형식: {class_id: [{versions, text, source_lang, source_text, [form], ...}]}. 이미 한국어 설명이 있는 버전은 건너뛴다
    # (form 이 있는 항목은 폼 설명이라 예외).
    # 번역본은 translated: true 로 공식 문장과 구분한다
    n_tr = 0
    if paths.FLAVOR_KO_TRANSLATED.exists():
        tr = json.loads(paths.FLAVOR_KO_TRANSLATED.read_text(encoding="utf-8"))
        for cid, d in db.items():
            have = {v for f in d["flavor_ko"] for v in f["versions"]}
            for t in tr.get(cid, []):
                if t.get("form"):   # 폼(메가·리전·품종 등) 설명: 기본형과 버전이 겹쳐도 따로 둔다. 앱은 그 폼을 볼 때만 보여 준다
                    d["flavor_ko"].append({"versions": t["versions"], "text": t["text"], "translated": True,
                                           "source_lang": t["source_lang"], "form": t["form"]})
                    n_tr += 1
                    continue
                vers = [v for v in t["versions"] if v not in have]
                if vers:
                    d["flavor_ko"].append({"versions": vers, "text": t["text"], "translated": True,
                                           "source_lang": t["source_lang"]})
                    have.update(vers)
                    n_tr += 1

    # pokedex-ko.json (git 제외: 도감 설명은 게임 원문)
    paths.POKEDEX_KO.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")

    # _SOURCE.json
    n_files = sum(1 for _ in CACHE.rglob("*.json") if _.name != "_SOURCE.json")
    size = sum(p.stat().st_size for p in CACHE.rglob("*.json"))
    (CACHE / "_SOURCE.json").write_text(json.dumps({
        "id": SOURCE_ID, "ref": API, "fetched": date.today().isoformat(), "max_dex": args.max_dex,
        "files": n_files, "bytes": size, "license": src.get("license", ""),
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    # 보고
    n_en = sum(1 for r in rows if r["enabled"] == "1")
    no_ko = [r["class_id"] for r in rows if not r["name_ko"]]
    no_flavor = [c for c, d in db.items() if not d["flavor_ko"]]
    if n_tr:
        print(f"  도감 설명 번역본 {n_tr}항목 덧붙임 ({paths.FLAVOR_KO_TRANSLATED.relative_to(paths.ROOT)})")
    no_art = [c for c, d in db.items() if not d["artwork_url"]]
    print(f"완료: {len(rows)}종 (enabled=1: {n_en})  네트워크 요청 {cli.n_net}, 캐시 {cli.n_cache}")
    print(f"  {paths.CSV_MAIN.relative_to(paths.ROOT)}, {paths.POKEDEX_KO.relative_to(paths.ROOT)}")
    if len(rows) != args.max_dex:
        print(f"  주의: {args.max_dex}종 중 {len(rows)}종만 만들었다")
    for label, lst in (("한국어 이름 없음", no_ko), ("한국어 도감 설명 없음", no_flavor), ("공식 아트 URL 없음", no_art)):
        if lst:
            print(f"  {label} {len(lst)}: {', '.join(lst[:20])}{' …' if len(lst) > 20 else ''}")


if __name__ == "__main__":
    main()
