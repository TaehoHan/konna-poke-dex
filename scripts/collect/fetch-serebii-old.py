"""리전폼(알로라·가라르·히스이·팔데아) 종과 암컷 설명이 따로 있는 종의 세레비넷 7·8세대 도감 페이지에서
폼별 영어 도감 설명을 받는다 (2026-10-25 사용자 허락, 약 115페이지).

  /pokedex-swsh/<slug>/   소드·실드, 브릴리언트 다이아몬드·샤이닝 펄, 레전드 아르세우스
  /pokedex-sm/<NNN>.shtml 썬·문, 울트라썬·울트라문, 레츠고 피카츄·이브이 (809 이하)

  pages/{swsh|sm}_<NNNN>.html  받은 그대로
  flavor_forms_en.json         {class_id: [{"versions": [...], "label", "text", "icon": "-a", "form": "alola"}]}
                               폼 줄(아이콘 접미사가 있는 줄)만. 기본형 설명은 PokeAPI 에 있다.

폼 판정은 fetch-serebii-sv.py 와 같다 (아이콘 접미사 → 세레비넷 아트 manifest form_tag + EXTRA_SUFFIX).
사용: .venv/bin/python scripts/collect/fetch-serebii-old.py [--dry-run]
"""
import argparse
import csv
import html
import importlib.util
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

_spec = importlib.util.spec_from_file_location("sv", Path(__file__).with_name("fetch-serebii-sv.py"))
sv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sv)

BASE = "https://www.serebii.net"
REGIONAL = ("alola", "galar", "hisui", "paldea")
EXTRA_SPECIES = ["meowstic", "indeedee", "basculegion", "slowking"]   # 암컷 설명이 따로 있는 종, 가라르 야도킹(아트 manifest 에 없음)
SLUG_FIX = {"farfetchd": "farfetch'd", "mr_mime": "mr.mime"}   # SV 목록에 없는 종의 세레비넷 슬러그
GAME = {"Sword": "sword", "Shield": "shield", "Brilliant Diamond": "brilliant-diamond", "Shining Pearl": "shining-pearl",
        "Legends: Arceus": "legends-arceus", "Sun": "sun", "Moon": "moon", "Ultra Sun": "ultra-sun",
        "Ultra Moon": "ultra-moon", "Let's Go, Pikachu!": "lets-go-pikachu", "Let's Go, Eevee!": "lets-go-eevee"}


def targets():
    with open(paths.CSV_MAIN, encoding="utf-8") as f:
        rows = {r["class_id"]: int(r["dex_no"]) for r in csv.DictReader(f) if r.get("enabled", "1") == "1"}
    with open(paths.SEREBII_RAW / "manifest.csv", encoding="utf-8") as f:
        reg = {r["class_id"] for r in csv.DictReader(f) if r["kind"] == "art" and r["form_tag"].split("_")[0] in REGIONAL}
    bad = set(json.loads((paths.SEREBII_SV_RAW / "_SOURCE.json").read_text(encoding="utf-8")).get("misnumbered_pages", []))
    slug = {int(p.name[:4]): p.name[5:-5] for p in (paths.SEREBII_SV_RAW / "pages").glob("*.html") if p.name not in bad}
    out = []
    for cid in sorted(reg | set(EXTRA_SPECIES), key=lambda c: rows[c]):
        no = rows[cid]
        out.append((no, cid, slug.get(no, SLUG_FIX.get(cid, cid.replace("_", "")))))
    return out


def parse(page):
    """[(라벨들, 문장, 아이콘 접미사)] — 라벨만 있고 문장 칸이 없는 줄(rowspan 공유)은 앞 문장에 버전을 더한다."""
    out = []
    for table in re.findall(r'Flavor Text</(?:td|h2)>(.*?)</table>', page, re.S):
        suffix, pending = "", None
        for m in re.finditer(r'<tr>\s*(?!<td class="cen")'
                             r'|<img src="/pokedex-(?:swsh|sm)/icon/\d+(-[^"./]+)?\.png"'
                             r'|<td class="fooinfo"[^>]*>(.*?)</td>'
                             r'|<td class="(?!cen|fooinfo)[a-z]+"[^>]*>([^<]+)</td>', table, re.S):
            if m.group(0).startswith("<tr"):                 # 아이콘 칸 없이 시작하는 줄(BDSP 등) = 기본형
                if pending is not None and out:
                    out[-1][0].append(pending)
                suffix, pending = "", None
            elif m.group(0).startswith("<img"):
                suffix, pending = m.group(1) or "", None
            elif m.group(3) is not None:                     # 게임 라벨
                if pending is not None and out:             # 앞 라벨에 문장이 없었다 = 앞 문장 공유
                    out[-1][0].append(pending)
                pending = html.unescape(m.group(3).strip())
            else:                                            # 문장
                t = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", m.group(2))).split())
                if pending is not None and t:
                    out.append(([pending], t, suffix))
                pending = None
        if pending is not None and out:
            out[-1][0].append(pending)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--delay", type=float, default=1.5)
    args = ap.parse_args()
    paths.require_drive()
    tg = targets()
    urls = [(f"swsh_{no:04d}.html", f"{BASE}/pokedex-swsh/{slug}/") for no, _, slug in tg]
    urls += [(f"sm_{no:04d}.html", f"{BASE}/pokedex-sm/{no:03d}.shtml") for no, _, _ in tg if no <= 809]
    print(f"대상 {len(tg)}종, 페이지 {len(urls)}개")
    if args.dry_run:
        print([c for _, c, _ in tg])
        return
    stage, dst = paths.STAGING / "serebii-old", paths.SEREBII_OLD_RAW
    (stage / "pages").mkdir(parents=True, exist_ok=True)
    fails = []
    for name, url in urls:
        p = stage / "pages" / name
        old = dst / "pages" / name
        if p.exists():
            continue
        if old.exists():
            if sv.national_no(old.read_text(encoding="latin-1")) == int(name.split("_")[1][:4]):
                continue
            old.rename(old.with_suffix(".misnumbered"))      # 첫 실행의 슬러그 오류로 받은 다른 종 페이지 (보관만)
        try:
            p.write_bytes(sv.get(url))
        except Exception as e:
            fails.append((name, url, str(e)))
            print(f"[실패] {name}: {e}")
        time.sleep(args.delay)

    sufmap = sv.form_suffixes()
    pages = {q.name: q for d in (dst / "pages", stage / "pages") if d.exists() for q in d.glob("*.html")}
    flavor, unknown_forms, unknown_labels, misnumbered = {}, set(), set(), []
    for no, cid, _ in tg:
        for kind in ("swsh", "sm"):
            q = pages.get(f"{kind}_{no:04d}.html")
            if q is None:
                continue
            page = q.read_text(encoding="latin-1")
            if sv.national_no(page) != no:                   # 다른 종 페이지면 쓰지 않는다
                misnumbered.append(q.name)
                continue
            rows = parse(page)
            # 레전드 아르세우스에 히스이 모습만 나오는 종은 그 줄에 아이콘이 없다 → 히스이 폼 설명으로 본다
            hisui_suf = next((k for k, v in sufmap.get(cid, {}).items() if v == "hisui"), None)
            la = [r for r in rows if "Legends: Arceus" in r[0]]
            if hisui_suf and la and not any(r[2] for r in la):
                rows = [(lb, t, hisui_suf if r in la else sf) for r in rows for lb, t, sf in [r]]
            for labels, text, suf in rows:
                if not suf:
                    continue
                vers = []
                for lab in labels:
                    v = GAME.get(lab)
                    if v is None:
                        unknown_labels.add(lab)
                    else:
                        vers.append(v)
                form = sufmap.get(cid, {}).get(suf)
                if form is None:
                    unknown_forms.add(f"{cid}{suf}")
                flavor.setdefault(cid, []).append({"versions": vers, "label": " / ".join(labels), "text": text,
                                                   "icon": suf, "form": form})
    n = sum(len(v) for v in flavor.values())
    (stage / "flavor_forms_en.json").write_text(json.dumps(flavor, ensure_ascii=False, indent=1), encoding="utf-8")
    (stage / "_SOURCE.json").write_text(json.dumps({
        "source": BASE, "date": str(date.today()), "species": len(flavor), "texts": n, "fails": fails,
        "unknown_labels": sorted(unknown_labels), "unknown_form_icons": sorted(unknown_forms), "misnumbered_pages": misnumbered,
        "note": "개인 앱 시험용. 저장소·배포 금지"}, ensure_ascii=False, indent=1), encoding="utf-8")
    (dst / "pages").mkdir(parents=True, exist_ok=True)
    for q in (stage / "pages").glob("*.html"):
        shutil.move(str(q), dst / "pages" / q.name)
    for nme in ("flavor_forms_en.json", "_SOURCE.json"):
        shutil.copy(stage / nme, dst / nme)
    print(f"번호가 맞지 않아 뺀 페이지 {misnumbered}")
    print(f"폼 설명 있는 종 {len(flavor)} | 문장 {n} | 실패 {len(fails)} {fails[:3]} | 모르는 라벨 {sorted(unknown_labels)} | "
          f"폼 모름 {sorted(unknown_forms)} → {dst}")


if __name__ == "__main__":
    main()
