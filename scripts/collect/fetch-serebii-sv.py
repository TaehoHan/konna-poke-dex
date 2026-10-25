"""세레비넷 스칼렛·바이올렛 도감(serebii.net/pokedex-sv/<slug>/)에서 9세대 이전 종의 영어 도감 설명을 받는다.

PokeAPI 에는 스칼렛·바이올렛 설명이 9세대 신종 120종 것뿐이라, 이상해씨처럼 SV·DLC·레전드 Z-A 에 다시 나온 종의
9세대 설명이 없다. 이 페이지에는 Scarlet / Violet / Legends: Z-A 설명이 영어로 있다 (2026-10-25 사용자 허락).

  pages/<NNNN>_<slug>.html   받은 그대로 (수정 금지)
  flavor_en.json             {class_id: [{"versions": ["scarlet"], "label": "Scarlet", "text": "...", ("icon": "-m", "form": "mega")}]}
                             폼 설명은 표의 아이콘 접미사 → 세레비넷 아트 manifest 의 form_tag 로 form 을 붙인다
  _SOURCE.json               출처·날짜·개수

로컬 STAGING 에 받고(이어 받기 가능) 다 받으면 드라이브 paths.SEREBII_SV_RAW 로 옮긴다. 요청 사이 --delay 초 쉰다.
번역(→ pokedex-ko.json)은 fetch-pokedex.py 쪽 번역 단계에서 한다.

사용: .venv/bin/python scripts/collect/fetch-serebii-sv.py --dry-run
      .venv/bin/python scripts/collect/fetch-serebii-sv.py [--delay 1.5] [--limit N]
"""
import argparse
import csv
import html
import json
import re
import shutil
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/ (paths.py)
import paths  # noqa: E402

BASE = "https://www.serebii.net"
INDEX = BASE + "/pokedex-sv/"
UA = "Mozilla/5.0 (konna-poke-dex personal research; low rate)"
GAME = {"Scarlet": "scarlet", "Violet": "violet", "Legends: Z-A": "legends-za"}
MAX_DEX = 905                                    # 906 이상(9세대 신종)은 PokeAPI 에 이미 있다


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def targets():
    """[(dex_no, slug, class_id)] — SV 도감 목록 중 전국도감 905 이하."""
    idx = get(INDEX).decode("latin-1")
    # 목록 뒤쪽의 "Paldea Pokédex" 는 지역 도감 번호라 쓰지 않는다 (전국도감 구간 "Kanto: 001 - 151" … "Paldea: 906-1025" 만).
    # (2026-10-25 첫 실행에서 이것을 섞어 84페이지가 엉뚱한 종 번호로 저장됐다 → 파싱 때 페이지의 National 번호로 다시 확인한다)
    cut = idx.find("A - G</option>")
    idx = idx[:cut] if cut > 0 else idx
    by_no = {}
    for slug, no in re.findall(r'<option value="/pokedex-sv/([a-z0-9.\-]+)/">(\d{3,4}) ', idx):
        by_no.setdefault(int(no), slug)
    with open(paths.CSV_MAIN, encoding="utf-8") as f:
        cls = {int(r["dex_no"]): r["class_id"] for r in csv.DictReader(f) if r.get("enabled", "1") == "1"}
    return [(n, s, cls[n]) for n, s in sorted(by_no.items()) if n <= MAX_DEX and n in cls]


def parse_flavor(page):
    """Flavor Text 표의 (게임 라벨, 문장, 아이콘 접미사) 목록. 폼별 줄은 앞 칸 아이콘 파일(예: 003-m.png, 128-p.png)의
    접미사로 구분된다 (rowspan 으로 여러 줄에 걸친다). 접미사가 없으면 기본형 ""."""
    out = []
    for table in re.findall(r'<td class="fooevo"[^>]*>Flavor Text</td>(.*?)</table>', page, re.S):
        suffix = ""
        for m in re.finditer(r'<img src="/pokedex-sv/icon/[^"]*?/\d+(-[^"./]+)?\.png"'
                             r'|<td class="[a-z]+"[^>]*>([^<]+)</td>\s*<td class="fooinfo"[^>]*>(.*?)</td>', table, re.S):
            if m.group(2) is None:
                suffix = m.group(1) or ""
                continue
            t = html.unescape(re.sub(r"<[^>]+>", " ", m.group(3)))
            t = " ".join(t.split())
            if t:
                out.append((html.unescape(m.group(2).strip()), t, suffix))
    return out


def national_no(page):
    m = re.search(r"<b>National</b>: </td><td>#(\d+)", page)
    return int(m.group(1)) if m else None


# 아트 manifest 에 없는 SV 아이콘 접미사 (직접 확인해 정함). 태그는 build-app-assets.py 의 폼 키와 같은 형식
EXTRA_SUFFIX = {
    "alcremie": {"-rc": "ruby_cream", "-mac": "matcha_cream", "-mic": "mint_cream", "-lc": "lemon_cream",
                 "-sc": "salted_cream", "-rs": "ruby_swirl", "-cs": "caramel_swirl", "-ras": "rainbow_swirl"},
    "basculegion": {"-f": "female"}, "indeedee": {"-f": "female"}, "meowstic": {"-f": "female"},
    "genesect": {"-b": "fire", "-c": "ice", "-d": "water", "-s": "electric"},
    "groudon": {"-p": "primal"}, "kyogre": {"-p": "primal"},
    "magearna": {"-mo": "original_color"}, "meloetta": {"-p": "pirouette"},
    "pikachu": {"-o": "original_cap", "-h": "hoenn_cap", "-s": "sinnoh_cap", "-u": "unova_cap", "-k": "kalos_cap",
                "-a": "alola_cap", "-p": "partner_cap", "-w": "world_cap"},
}


def form_suffixes():
    """{class_id: {"-m": "mega", …}} — 세레비넷 아트 manifest 의 파일 접미사 → form_tag (SV 아이콘과 같은 규칙) + EXTRA_SUFFIX."""
    man = paths.SEREBII_RAW / "manifest.csv"
    out = {k: dict(v) for k, v in EXTRA_SUFFIX.items()}
    if man.exists():
        with open(man, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                m = re.search(r"art__\d+(-[^./]+)\.png$", r["file"])
                if r["kind"] == "art" and r["form_tag"] and m:
                    out.setdefault(r["class_id"], {}).setdefault(m.group(1), r["form_tag"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="목록만 받아 개수를 보여 준다 (페이지는 받지 않음)")
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    paths.require_drive()

    tg = targets()
    print(f"대상 {len(tg)}종 (SV 도감 목록 중 전국도감 {MAX_DEX} 이하)")
    if args.dry_run:
        print("예:", tg[:3], "…", tg[-2:])
        return
    stage = paths.STAGING / "serebii-sv"
    (stage / "pages").mkdir(parents=True, exist_ok=True)
    if args.limit:
        tg = tg[:args.limit]
    fails = []
    for i, (no, slug, cid) in enumerate(tg, 1):
        p = stage / "pages" / f"{no:04d}_{slug}.html"
        done = (paths.SEREBII_SV_RAW / "pages" / p.name).exists()
        if p.exists() or done:
            continue
        try:
            p.write_bytes(get(f"{INDEX}{slug}/"))
        except Exception as e:
            fails.append((no, slug, str(e)))
            print(f"[실패] {no} {slug}: {e}")
        if i % 50 == 0:
            print(f"{i}/{len(tg)}", flush=True)
        time.sleep(args.delay)

    # 파싱 (STAGING + 이미 드라이브에 있는 것)
    flavor, n_txt, unknown, unknown_forms, misnumbered = {}, 0, set(), set(), []
    sufmap = form_suffixes()
    pages_all = {q.name: q for d in (paths.SEREBII_SV_RAW / "pages", stage / "pages") if d.exists() for q in d.glob("*.html")}
    with open(paths.CSV_MAIN, encoding="utf-8") as f:
        cls = {int(r["dex_no"]): r["class_id"] for r in csv.DictReader(f) if r.get("enabled", "1") == "1"}
    cid_of = {n: cls.get(int(n[:4])) for n in pages_all if int(n[:4]) <= MAX_DEX}
    for name, q in sorted(pages_all.items()):
        cid = cid_of.get(name)
        if cid is None:
            continue
        page = q.read_text(encoding="latin-1")
        if national_no(page) != int(name[:4]):      # 파일 번호와 페이지의 전국도감 번호가 다르면 쓰지 않는다
            misnumbered.append(name)
            continue
        rows = []
        for label, text, suf in parse_flavor(page):
            game = next((v for k, v in GAME.items() if label.startswith(k)), None)
            if game is None:
                unknown.add(label)
            row = {"versions": [game] if game else [], "label": label, "text": text}
            if suf:   # 폼 설명: form = 세레비넷 form_tag (모르면 None, icon 에 접미사를 남긴다)
                row["icon"] = suf
                row["form"] = sufmap.get(cid, {}).get(suf)
                if row["form"] is None:
                    unknown_forms.add(f"{cid}{suf}")
            rows.append(row)
        if rows:
            flavor[cid] = rows
            n_txt += len(rows)
    (stage / "flavor_en.json").write_text(json.dumps(flavor, ensure_ascii=False, indent=1), encoding="utf-8")
    (stage / "_SOURCE.json").write_text(json.dumps({
        "source": INDEX, "date": str(date.today()), "species": len(flavor), "texts": n_txt,
        "fails": fails, "unknown_labels": sorted(unknown), "unknown_form_icons": sorted(unknown_forms), "misnumbered_pages": misnumbered,
        "note": "개인 학습·앱 시험용. 저장소·배포 금지 (권리는 Nintendo/Creatures/GAME FREAK/The Pokémon Company, 페이지는 Serebii.net)",
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    # 드라이브로 옮기기
    dst = paths.SEREBII_SV_RAW
    (dst / "pages").mkdir(parents=True, exist_ok=True)
    for q in (stage / "pages").glob("*.html"):
        shutil.move(str(q), dst / "pages" / q.name)
    for n in ("flavor_en.json", "_SOURCE.json"):
        shutil.copy(stage / n, dst / n)
    n_form = sum(1 for v in flavor.values() for x in v if "icon" in x)
    print(f"번호가 맞지 않아 뺀 페이지 {len(misnumbered)}개 (_SOURCE.json misnumbered_pages)")
    print(f"설명 있는 종 {len(flavor)} | 문장 {n_txt} (폼 {n_form}) | 실패 {len(fails)} | 모르는 라벨 {sorted(unknown)} | "
          f"폼 모름 {len(unknown_forms)} {sorted(unknown_forms)[:20]} → {dst}")


if __name__ == "__main__":
    main()
