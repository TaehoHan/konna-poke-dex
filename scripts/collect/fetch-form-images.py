"""앱 "다른 모습"에 없던 그림을 받는다 (2026-10-25 사용자 허락): 암컷(암수 생김새가 다른 102종)과 누락된 폼.

  female/<dex_no>.png        PokeAPI sprites HOME 암컷 렌더 (pokemon-species has_gender_differences=true 인 종)
  forms/<class_id>__<tag>.png PokeAPI HOME 렌더(비기본 variety 의 pokemon id) 또는 세레비넷 SV 그림(마휘핑 크림)
  manifest.json              [{"class_id", "tag", "name_ko", "file", "source"}] — build-app-assets.py 가 다른 모습으로 쓴다

로컬 STAGING 에 받고 드라이브 paths.FORM_IMG_RAW 로 옮긴다. 이미 있는 파일은 건너뛴다.
사용: .venv/bin/python scripts/collect/fetch-form-images.py [--dry-run]
"""
import argparse
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/ (paths.py)
import paths  # noqa: E402

HOME = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/home"
SEREBII = "https://www.serebii.net"
UA = "Mozilla/5.0 (konna-poke-dex personal research; low rate)"

# 누락된 폼: (class_id, tag, 한국어 이름, PokeAPI pokemon 이름 또는 세레비넷 경로)
# 토템·특성 폼(지우개굴닌자 전 단계)·스타터 피카츄/이브이는 DESIGN 원칙대로 넣지 않는다.
FORMS = [
    ("kyogre", "primal", "원시회귀", "kyogre-primal"),
    ("groudon", "primal", "원시회귀", "groudon-primal"),
    ("slowking", "galar", "가라르의 모습", "slowking-galar"),
    ("necrozma", "ultra", "울트라네크로즈마", "necrozma-ultra"),
    ("meowstic", "mega", "메가진화 (수컷)", "meowstic-male-mega"),
    ("meowstic", "female_mega", "메가진화 (암컷)", "meowstic-female-mega"),
    ("tatsugiri", "curly_mega", "메가진화", "tatsugiri-curly-mega"),
    ("koraidon", "swimming_build", "유영형태", "koraidon-swimming-build"),
    ("miraidon", "aquatic_mode", "아쿠아모드", "miraidon-aquatic-mode"),
    *[("alcremie", tag, name, f"/scarletviolet/pokemon/new/869-{suf}.png") for suf, tag, name in [
        ("rc", "ruby_cream", "루비크림"), ("mac", "matcha_cream", "말차크림"), ("mic", "mint_cream", "민트크림"),
        ("lc", "lemon_cream", "레몬크림"), ("sc", "salted_cream", "솔티드크림"), ("rs", "ruby_swirl", "루비스월"),
        ("cs", "caramel_swirl", "캐러멜스월"), ("ras", "rainbow_swirl", "트리플스월")]],
]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def species_index():
    out = {}
    for f in paths.POKEAPI_CACHE.joinpath("pokemon-species").glob("[0-9]*.json"):
        s = json.loads(f.read_text(encoding="utf-8"))
        out[s["name"]] = s
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    paths.require_drive()
    sp = species_index()
    pid = {v["pokemon"]["name"]: int(v["pokemon"]["url"].rstrip("/").split("/")[-1])
           for s in sp.values() for v in s["varieties"]}
    jobs = []   # (url, 상대 경로, manifest 항목)
    for s in sorted(sp.values(), key=lambda s: s["id"]):
        if s.get("has_gender_differences"):
            cid = s["name"].replace("-", "_")
            rel = f"female/{s['id']}.png"
            jobs.append((f"{HOME}/female/{s['id']}.png", rel,
                         {"class_id": cid, "tag": "female", "name_ko": "암컷", "file": rel, "source": "pokeapi-home"}))
    for cid, tag, name, ref in FORMS:
        rel = f"forms/{cid}__{tag}.png"
        if ref.startswith("/"):
            url, src = SEREBII + ref, "serebii-sv"
        elif ref in pid:
            url, src = f"{HOME}/{pid[ref]}.png", "pokeapi-home"
        else:
            print(f"[건너뜀] PokeAPI 에 {ref} 가 없다")
            continue
        jobs.append((url, rel, {"class_id": cid, "tag": tag, "name_ko": name, "file": rel, "source": src}))
    print(f"받을 그림 {len(jobs)}장 (암컷 {sum(j[2]['tag'] == 'female' for j in jobs)})")
    if args.dry_run:
        return
    stage, dst = paths.STAGING / "form-images", paths.FORM_IMG_RAW
    man, fails = [], []
    for url, rel, item in jobs:
        out = stage / rel
        if not (dst / rel).exists() and not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            try:
                out.write_bytes(get(url))
            except Exception as e:
                fails.append((rel, url, str(e)))
                print(f"[실패] {rel}: {e}")
                continue
            time.sleep(0.5)
        man.append(item)
    for q in stage.rglob("*.png"):
        t = dst / q.relative_to(stage)
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(q), t)
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    (dst / "_SOURCE.json").write_text(json.dumps({"home": HOME, "serebii": SEREBII, "count": len(man), "fails": fails,
        "note": "개인 앱 시험용. 저장소·배포 금지"}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"받음 {len(man)} | 실패 {len(fails)} {fails[:5]} → {dst}")


if __name__ == "__main__":
    main()
