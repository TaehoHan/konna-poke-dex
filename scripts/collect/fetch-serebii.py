"""세레비넷(serebii.net) -> 드라이브 raw/serebii/ : 종별 공식 아트(폼 포함) + 3D 렌더(HOME/SV/SWSH/ZA, 이로치 포함).

받은 그대로 보존한다 (수정 금지). 정규화는 normalize-sources.py(예정)에서 한다.

출력 (paths.RAW / "serebii"):
  index.html                 /pokemon/ 목록 페이지 (도감 번호 ↔ 세레비넷 slug)
  pages/<slug>.html          종 페이지 /pokemon/<slug>/ (공식 아트 이미지와 alt 텍스트)
  img/<dex4>/<kind>__<파일>  이미지. kind = art | home | sv | sv_shiny | swsh | swsh_shiny | za | za_shiny
  meta/<dex4>.json           종별 결과 (받은 이미지, 없는 URL). 매번 받아 둔 페이지로 다시 만든다 (없는 URL 은 다시 요청하지 않는다)
  manifest.csv               meta 를 모은 표: dex_no,class_id,file,url,kind,image_type,shiny,form_label,form_tag,bytes
  _SOURCE.json

폼 태그: 공식 아트의 alt 텍스트("Alola Form", "Galarian Form", "Gigantamax Meowth" …)에서 읽는다.
         파일명 접미사(-a, -g …)는 종마다 뜻이 달라서 쓰지 않는다 (피카츄 -a 는 얼로라 모자).
렌더는 기본형 번호 URL 만 받는다 (폼별 렌더는 폼 모델 데이터가 부족할 때 추가).

허락 관문: data/sources.yaml 의 `serebii` 가 enabled: true 일 때만 전체를 받는다.
           --only 로 15종 이하를 고르면 표본 수집으로 허용하고, 1025종 예상치를 출력한다.
전체 요청 속도 --rate(건/초, 기본 1, 상한 3), 작업자 --workers(기본 4)가 한 속도 제한을 같이 쓴다.
429/5xx 가 오면 전체 속도를 절반으로 낮추고(성공 100건마다 두 배씩 회복), 연결 끊김은 속도를 유지한 채 재시도, 연속 오류 8회면 멈춘다. 실패한 종은 다시 실행하면 이어서 받는다. 같은 URL 은 다시 요청하지 않는다 (없는 URL 도 meta 에 기록).

사용:
  .venv/bin/python scripts/collect/fetch-serebii.py --only pikachu,meowth,vulpix,charizard,celebi,pecharunt
  .venv/bin/python scripts/collect/fetch-serebii.py --rate 3      # 전체 (enabled: true 필요)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/ (paths.py)
import paths  # noqa: E402
import argparse
import csv
import html
import json
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import requests
import yaml

SOURCE_ID = "serebii"
BASE = "https://www.serebii.net"
USER_AGENT = "Mozilla/5.0 (konna-poke-dex personal project; cached, 1 req/s)"
SAMPLE_MAX = 15

# 기본형 렌더: (kind, URL 틀). {n} = 3자리(1000 이상은 4자리) 도감 번호
RENDERS = [
    ("home", "/pokemonhome/pokemon/{n}.png"),
    ("sv", "/scarletviolet/pokemon/new/{n}.png"),
    ("sv_shiny", "/Shiny/SV/new/{n}.png"),
    ("swsh", "/swordshield/pokemon/{n}.png"),
    ("swsh_shiny", "/Shiny/SWSH/{n}.png"),
    ("za", "/legendsz-a/pokemon/{n}.png"),
    ("za_shiny", "/Shiny/ZA/{n}.png"),
]
FORM_WORDS = [  # alt 텍스트 → 폼 태그 (앞에서부터 처음 맞는 것)
    # 리전폼은 "Alola Form" 처럼 form 이 붙을 때만 ("Alola Cap" 은 모자 피카츄)
    (r"\balolan?\b.*\bform\b", "alola"), (r"\bgalar(ian)?\b.*\bform\b", "galar"),
    (r"\bhisui(an)?\b.*\bform\b", "hisui"), (r"\bpaldea(n)?\b.*\bform\b", "paldea"),
    (r"\bgigantamax\b", "gmax"), (r"\bmega\b.*\bx\b", "mega_x"), (r"\bmega\b.*\by\b", "mega_y"), (r"\bmega\b", "mega"),
]
SWSH_MAX_DEX = 905   # SWSH 렌더는 8세대까지만 있다 (표본: 906, 1025 는 404)
ZA_DEX_RE = re.compile(r"(Lumiose|Hyperspace) : #\d")   # Z-A 본편/DLC 지방도감 번호가 있는 종만 ZA 렌더가 있다
MANIFEST_FIELDS = ["dex_no", "class_id", "file", "url", "kind", "image_type", "shiny", "form_label", "form_tag", "bytes"]


class Client:
    """여러 작업자가 함께 쓰는 클라이언트. 전체 요청 속도를 rate(건/초) 이하로 맞춘다.
    429/5xx(서버가 부담을 알리는 응답)는 전체 속도를 절반으로 낮춘다. 연결 끊김(ConnectionError 등)은
    그 요청만 기다렸다 다시 하고 속도는 낮추지 않는다 (2026-10-24: 5,500건 중 2건, 서버는 정상 응답).
    낮춘 속도는 성공이 RECOVER_AFTER 건 이어질 때마다 두 배씩 원래 rate 까지 돌아온다. 오류가 연속 8회면 멈춘다."""

    MIN_RATE = 0.5
    MAX_CONSEC_ERRORS = 8
    RECOVER_AFTER = 100

    def __init__(self, rate):
        self.base_interval = 1.0 / rate
        self.interval = self.base_interval
        self._ok_streak = 0
        self.lock = threading.Lock()
        self.local = threading.local()
        self.n = 0
        self.bytes = 0
        self.slowdowns = 0
        self._next = 0.0
        self._consec_err = 0

    def _session(self):
        if not hasattr(self.local, "s"):
            self.local.s = requests.Session()
            self.local.s.headers["User-Agent"] = USER_AGENT
        return self.local.s

    def _slot(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self._next)
            self._next = t + self.interval
            self.n += 1
        if t > now:
            time.sleep(t - now)

    def _ok(self):
        with self.lock:
            self._consec_err = 0
            self._ok_streak += 1
            if self.interval > self.base_interval and self._ok_streak >= self.RECOVER_AFTER:
                self.interval = max(self.base_interval, self.interval / 2)
                self._ok_streak = 0
                print(f"  성공이 이어져 전체 속도를 {1.0 / self.interval:.2f}건/초로 되돌림", file=sys.stderr, flush=True)

    def _error(self, err, path, attempt, slow_down):
        with self.lock:
            self._consec_err += 1
            self._ok_streak = 0
            if slow_down and self.interval < 1.0 / self.MIN_RATE:
                self.interval = min(self.interval * 2, 1.0 / self.MIN_RATE)
                self.slowdowns += 1
            n_err, rate = self._consec_err, 1.0 / self.interval
        what = f"전체 속도 {rate:.2f}건/초로 낮춤" if slow_down else "속도 유지"
        print(f"  오류 ({err}) → {what}, 재시도 {attempt + 1}/4: {path}", file=sys.stderr, flush=True)
        if n_err >= self.MAX_CONSEC_ERRORS:
            raise RuntimeError(f"연속 오류 {n_err}회. 사이트가 거부하는 것으로 보고 멈춘다 (마지막: {err})")
        time.sleep(5 * 2 ** attempt)

    def get(self, path, want_image):
        """(bytes 또는 None). 404·이미지가 아닌 응답은 None."""
        for attempt in range(4):
            self._slot()
            try:
                r = self._session().get(BASE + path, timeout=30, stream=True)
            except requests.RequestException as e:
                self._error(type(e).__name__, path, attempt, slow_down=False)
                continue
            ctype = r.headers.get("content-type", "")
            if r.status_code == 404 or (want_image and r.status_code == 200 and not ctype.startswith("image/")):
                r.close()          # 404 페이지 본문(약 36KB)은 받지 않는다
                self._ok()
                return None
            if r.status_code == 200:
                data = r.content
                with self.lock:
                    self.bytes += len(data)
                self._ok()
                return data
            r.close()
            if r.status_code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {r.status_code}: {path}")
            self._error(f"HTTP {r.status_code}", path, attempt, slow_down=True)
        raise RuntimeError(f"요청 실패: {path}")


def dex_str(n):
    return f"{n:03d}" if n < 1000 else str(n)


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def load_classes():
    with paths.CSV_MAIN.open(encoding="utf-8", newline="") as f:
        return {int(r["dex_no"]): r for r in csv.DictReader(f)}


def parse_index(text):
    """'<option value="/pokemon/mr.mime/">122 Mr. Mime</option>' → {122: ('mr.mime', 'Mr. Mime')}"""
    out = {}
    for m in re.finditer(r'<option value="/pokemon/([^"/]+)/">(\d{3,4}) ([^<]+)</option>', text):
        out.setdefault(int(m.group(2)), (m.group(1), html.unescape(m.group(3)).strip()))
    return out


def norm_name(s):
    return re.sub(r"[^a-z0-9]", "", s.lower().replace("♀", "f").replace("♂", "m").replace("é", "e"))


def parse_art(text, dex, species_en):
    """종 페이지에서 /pokemon/art/<dex>*.png 와 alt 텍스트 → [(url, form_label, form_tag)]"""
    alts = {}
    for tag in re.findall(r'<img(?:[^>"]|"[^"]*")*>', text):   # alt 안에 <br /> 가 있다 (켄타로스 품종)
        m = re.search(r'src="(/pokemon/art/' + dex_str(dex) + r'[^"]*\.png)"', tag)
        if not m:
            continue
        a = re.search(r'(?:alt|title)="([^"]*)"', tag)
        lab = html.unescape(re.sub(r"<[^>]+>", " ", a.group(1))) if a else ""
        alts.setdefault(m.group(1), []).append(" ".join(lab.split()))
    out = []
    for url, labels in alts.items():
        label = ""
        for lab in labels:   # "Meowth Artwork"·빈 값보다 "Alola Form" 같은 설명을 고른다
            lab = re.sub(r"\s*Artwork$", "", lab).strip()
            if lab and norm_name(lab) != norm_name(species_en):
                label = lab
                break
        tag = ""
        is_base = url.endswith(f"/{dex_str(dex)}.png")   # 기본형 ("Kantonian Form", "Normal Forme" 이라고 적혀도)
        if label and not is_base:
            low = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode().lower()
            tag = next((t for pat, t in FORM_WORDS if re.search(pat, low)), "")
            if tag in ("alola", "galar", "hisui", "paldea"):   # "Paldean Form - Combat Breed" → paldea_combat_breed
                after = re.sub(r"[^a-z0-9]+", "_", low.split("form", 1)[1]).strip("_")
                tag = f"{tag}_{after}" if after else tag
            if not tag:   # 그 밖의 폼: 종 이름·Form 을 빼고 slug
                rest = re.sub(r"\b" + re.escape(species_en.lower()) + r"\b", "", low)
                rest = re.sub(r"\bforme?\b", "", rest)
                tag = re.sub(r"[^a-z0-9]+", "_", rest).strip("_")
        if not label and not is_base:
            tag = "unlabeled_" + url.rsplit("/", 1)[1][:-4].split("-", 1)[-1]   # alt 없는 폼 아트: 사람이 확인
        out.append((url, label, tag))
    return out


def render_kinds(dex, page_text):
    """404 가 뻔한 렌더는 요청하지 않는다 (표본 14종의 404 와 일치 확인)."""
    plain = html.unescape(" ".join(re.sub(r"<[^>]+>", " ", page_text).split()))
    has_za = bool(ZA_DEX_RE.search(plain))
    out = []
    for kind, t in RENDERS:
        if kind.startswith("swsh") and dex > SWSH_MAX_DEX:
            continue
        if kind.startswith("za") and not has_za:
            continue
        out.append((kind, t))
    return out


def fetch_species(cli, dex, slug, row, root):
    meta_path = root / "meta" / f"{dex:04d}.json"
    # meta 는 다시 만든다 (파싱 규칙을 고쳐도 요청 없이 갱신). 이전 meta 의 missing 은 "없는 URL" 캐시로 쓴다
    known_missing = set()
    if meta_path.exists():
        known_missing = set(json.loads(meta_path.read_text(encoding="utf-8"))["missing"])
    made = 0
    page_path = root / "pages" / f"{slug}.html"
    if page_path.exists():
        page = page_path.read_bytes()
    else:
        page = cli.get(f"/pokemon/{slug}/", want_image=False)
        made += 1
        if page is None:
            raise RuntimeError(f"종 페이지 없음: /pokemon/{slug}/")
        write_atomic(page_path, page)
    text = page.decode("latin1")

    targets = [(url, "art", label, tag) for url, label, tag in parse_art(text, dex, row["name_en"])]
    targets += [(t.format(n=dex_str(dex)), kind, "", "") for kind, t in render_kinds(dex, text)]
    images, missing = [], []
    for url, kind, label, tag in targets:
        fname = f"{kind}__{url.rsplit('/', 1)[1]}"
        out = root / "img" / f"{dex:04d}" / fname
        if out.exists():
            size = out.stat().st_size
        elif url in known_missing:
            missing.append(url)
            continue
        else:
            data = cli.get(url, want_image=True)
            made += 1
            if data is None:
                missing.append(url)
                continue
            write_atomic(out, data)
            size = len(data)
        images.append({"file": f"img/{dex:04d}/{fname}", "url": url, "kind": kind,
                       "image_type": "art" if kind == "art" else "render", "shiny": int(kind.endswith("_shiny")),
                       "form_label": label, "form_tag": tag, "bytes": size})
    meta = {"dex_no": dex, "class_id": row["class_id"], "slug": slug, "page_bytes": len(page),
            "images": images, "missing": missing}
    write_atomic(meta_path, json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8"))
    return meta, made > 0


def write_manifest(root):
    rows = []
    for p in sorted((root / "meta").glob("*.json")):
        m = json.loads(p.read_text(encoding="utf-8"))
        for im in m["images"]:
            rows.append({"dex_no": m["dex_no"], "class_id": m["class_id"], **im})
    with (root / "manifest.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="class_id 목록(쉼표). 15종 이하면 표본 수집으로 허용")
    ap.add_argument("--rate", type=float, default=1.0, help="전체 요청 속도(건/초, 기본 1). 3 초과 금지")
    ap.add_argument("--workers", type=int, default=4, help="동시 작업자 수 (속도는 --rate 가 정한다)")
    args = ap.parse_args()
    if not 0 < args.rate <= 3:
        sys.exit("--rate 는 0 초과 3 이하로 둔다 (2026-10-24 사용자와 정한 상한).")

    paths.require_drive()
    root = paths.RAW / SOURCE_ID
    src = next(s for s in yaml.safe_load(paths.SOURCES.read_text(encoding="utf-8"))["sources"] if s["id"] == SOURCE_ID)
    classes = load_classes()
    by_cid = {r["class_id"]: d for d, r in classes.items()}

    if args.only:
        want = [c.strip() for c in args.only.split(",") if c.strip()]
        unknown = [c for c in want if c not in by_cid]
        if unknown:
            sys.exit(f"list-poke.csv 에 없는 class_id: {unknown}")
        dexes = [by_cid[c] for c in want]
    else:
        dexes = sorted(d for d, r in classes.items() if r["enabled"] == "1")
    sample = len(dexes) <= SAMPLE_MAX and args.only
    if not src.get("enabled") and not sample:
        sys.exit(f"sources.yaml 의 '{SOURCE_ID}' 가 enabled: false 다. 전체 수집은 사용자 허락 후 true 로 바꾸고 실행한다.\n"
                 f"표본은 --only 로 {SAMPLE_MAX}종 이하를 고른다.")

    cli = Client(args.rate)
    idx_path = root / "index.html"
    if not idx_path.exists():
        write_atomic(idx_path, cli.get("/pokemon/", want_image=False))
    index = parse_index(idx_path.read_bytes().decode("latin1"))

    no_slug = [d for d in dexes if d not in index]
    if no_slug:
        print(f"주의: 세레비넷 목록에 없는 도감 번호 {len(no_slug)}: {no_slug[:20]}")
    mismatch = [(d, classes[d]["name_en"], index[d][1]) for d in dexes
                if d in index and norm_name(classes[d]["name_en"]) != norm_name(index[d][1])]
    if mismatch:
        print(f"참고: 영문 이름 표기가 다른 종 {len(mismatch)} (번호로 맞춘다): {mismatch[:10]}")

    t0 = time.monotonic()
    done_new, failed = [], []
    todo = [d for d in dexes if d in index]
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_species, cli, d, index[d][0], classes[d], root): d for d in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            d = futs[fut]
            try:
                meta, new = fut.result()
            except Exception as e:   # 한 종 실패는 기록만 하고 계속 (다시 돌리면 그 종만 받는다)
                failed.append((d, str(e)))
                print(f"  실패 #{d:04d} {classes[d]['class_id']}: {e}", flush=True)
                if "연속 오류" in str(e):
                    for f in futs:
                        f.cancel()
                    break
                continue
            if new:
                done_new.append(meta)
            if i % 25 == 0 or len(todo) <= SAMPLE_MAX:
                el = time.monotonic() - t0
                print(f"  {i}/{len(todo)} #{d:04d} {classes[d]['class_id']}: 이미지 {len(meta['images'])}"
                      f" (없음 {len(meta['missing'])})  요청 {cli.n}, {cli.bytes / 1e6:.1f}MB, {el / 60:.1f}분,"
                      f" {cli.n / max(el, 1e-9):.2f}건/초", flush=True)

    rows = write_manifest(root)
    n_files = sum(1 for p in root.rglob("*") if p.is_file() and not p.name.endswith(".tmp"))
    total = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    write_atomic(root / "_SOURCE.json", json.dumps({
        "id": SOURCE_ID, "ref": BASE, "fetched": date.today().isoformat(), "species": len(list((root / 'meta').glob('*.json'))),
        "images": len(rows), "files": n_files, "bytes": total, "license": src.get("license", ""),
    }, ensure_ascii=False, indent=1).encode("utf-8"))

    if failed:
        print(f"실패 {len(failed)}종 (다시 실행하면 이어서 받는다): {[d for d, _ in failed][:30]}")
    if cli.slowdowns:
        print(f"속도를 낮춘 횟수 {cli.slowdowns} (마지막 {1 / cli.interval:.2f}건/초)")
    print(f"완료: 이번 실행 요청 {cli.n}건, {cli.bytes / 1e6:.1f}MB.  manifest 이미지 {len(rows)}장, 폴더 {total / 1e6:.1f}MB")
    forms = sorted({(r["class_id"], r["form_tag"]) for r in rows if r["form_tag"]})
    if forms:
        print(f"  폼 태그 {len(forms)}: {', '.join(f'{c}:{t}' for c, t in forms[:40])}")
    if sample and done_new:
        n = len(done_new)
        req = cli.n / n
        mb = (sum(m["page_bytes"] for m in done_new) + sum(im["bytes"] for m in done_new for im in m["images"])) / n / 1e6
        imgs = sum(len(m["images"]) for m in done_new) / n
        tot = len([d for d, r in classes.items() if r["enabled"] == "1"])
        print(f"[표본 {n}종] 종당 요청 {req:.1f}건, 이미지 {imgs:.1f}장, {mb:.2f}MB")
        print(f"[1025종 예상] 요청 약 {req * tot:,.0f}건 → {req * tot / args.rate / 3600:.1f}시간 이상, "
              f"이미지 약 {imgs * tot:,.0f}장, 약 {mb * tot / 1e3:.2f}GB (표본은 유명 종 위주라 과대 추정일 수 있다)")


if __name__ == "__main__":
    main()
