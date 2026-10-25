"""받은 출처(드라이브 paths.RAW) + 라벨 매핑 -> 로컬 workspace/pool/<class_id>/ + workspace/pool/manifest.csv

원본(paths.RAW)은 읽기만 한다. pool 은 다시 만들 수 있는 산출물이다 (--rebuild 로 지우고 새로 만든다).

출처 (use: train 만. eval 출처·카드는 넣지 않는다)
  serebii              raw/serebii/manifest.csv (class_id·form_tag·kind 가 이미 있다)
  hf-jjmack-gen1-9     parquet image_data + label-map. 파일명 꼬리로 image_type·뒷모습(view=back) 기록
  kg-gen1-9-complete   pokemon_augmented_64x64.npz (tar.xz 와 같은 11.6만 장, 64px 도트 + 합성 배경 증강) + label-map
  kg-1000-pokemon      pokemon-dataset-1000/dataset/<이름>/*.png (train/val/test 는 사본이라 안 쓴다) + label-map

규칙 (2026-10-25)
  - 투명 배경 이미지는 그림 영역만 잘라낸다(둘레 4% 여백). 그 뒤 긴 변 < 48px 은 빼고, 짧은 변 < 96px 은 small=1 로 표시만 한다.
    w,h 는 잘라낸 뒤 크기다.
  - RGBA 는 투명도를 살려 PNG 로, 나머지는 JPG(q92)로. 긴 변이 512px 를 넘으면 줄인다.
  - pHash(64bit, 흰 배경 위에서) + 색 서명(8x8 평균색)으로
      * 중복: 같은 종에서 pHash 같고 색도 거의 같으면 하나만 남긴다 (큰 것 우선). pHash 는 모양만 봐서 이로치를 지우지 않으려고 색도 본다
      * 그룹: 해밍 거리 <= 4 를 한 그룹으로 묶고 **그룹 단위로** train/val/test(80/10/10, 그룹 대표 해시로 고정) 를 나눈다
      * 종 충돌: 한 그룹에 다른 종이 섞이면 conflict=1 로 표시하고 split 을 'conflict' 로 둔다 (학습에서 뺀다)
  - kg-gen1-9 는 train 전용 (TRAIN_ONLY): 그룹핑·충돌 판정에서 빼고 split=train
  - 라벨 매핑에서 class_id 가 비었거나 skip 인 것, list-poke.csv 에서 enabled=0 인 종은 뺀다.

manifest.csv: class_id,form,file,source_id,source_ref,image_type,view,shiny,w,h,has_alpha,small,phash,color,group,split,conflict

사용:
  .venv/bin/python scripts/collect/normalize-sources.py --rebuild           # 전부 새로
  .venv/bin/python scripts/collect/normalize-sources.py --rebuild --limit 200   # 출처마다 200장만 (시험)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/ (paths.py)
import paths  # noqa: E402
import argparse
import collections
import csv
import hashlib
import io
import re
import shutil
import time
from multiprocessing import Pool

import numpy as np
import yaml
from PIL import Image, ImageOps

MIN_SIDE, SMALL_SIDE, MAX_SIDE = 48, 96, 512
NEAR = 4            # pHash 해밍 거리 <= NEAR 는 같은 그룹
COLOR_TOL = 12      # 중복 판정용 평균색 차이(0~255, 칸별 평균 절댓값)
SPLIT = (0.8, 0.1)  # train, val (나머지 test)
# train 에만 넣는 출처: kg-gen1-9 는 종마다 몇 장 안 되는 도트에 무늬 배경만 바꾼 증강본(90장/라벨)이라
#  (1) val/test 에 넣으면 사실상 같은 그림이 train 에도 있고 (2) 64px 안 작은 그림이라 pHash 가 배경 무늬를 봐서
#  다른 종끼리 묶인다 (2026-10-25: 종 충돌 5,592장 중 5,523장). 그룹핑·충돌 판정에서 빼고 split=train 으로 둔다.
TRAIN_ONLY = {"kg-gen1-9-complete"}
FIELDS = ["class_id", "form", "file", "source_id", "source_ref", "image_type", "view", "shiny", "w", "h",
          "has_alpha", "small", "phash", "color", "group", "split", "conflict"]

# JJMack 파일명 꼬리 → image_type (2026-10-24 train.parquet 집계 기준)
JJ_TYPES = [
    (r"^(dream|masters|masters-icon|masters-s|masters-icon-s|smile|conquest|dun)$", "art"),
    (r"^(home|home-s|home-menu|go|go-s|snap|stadium|3dpro|battle-rev)$", "render"),
    (r"^anime$", "anime"),
    (r"^(sprite.*|spriteback|rb|rg|ms|bt)$", "sprite"),
]


# ---------- 공통 ----------
def load_label_map(sid):
    with (paths.LABEL_MAP / f"{sid}.csv").open(encoding="utf-8", newline="") as f:
        return {r["source_label"]: (r["class_id"], r["form"]) for r in csv.DictReader(f)}


def enabled_classes():
    with paths.CSV_MAIN.open(encoding="utf-8", newline="") as f:
        return {r["class_id"] for r in csv.DictReader(f) if r["enabled"] == "1"}


def phash_int(img_rgb):
    import imagehash
    return int(str(imagehash.phash(img_rgb)), 16)


CROP_PAD = 0.04   # 잘라낸 그림 둘레 여백 (긴 변 비율)


def process(task):
    """작업자: 이미지 하나 → pool 파일 + 기록. 실패하면 (None, 이유)."""
    try:
        if "bytes" in task:
            im = Image.open(io.BytesIO(task["bytes"]))
        elif "array" in task:
            im = Image.fromarray(task["array"])
        else:
            im = Image.open(task["path"])
        im.seek(0)                      # GIF 등은 첫 프레임
        im.load()
    except Exception as e:  # 받은 파일은 믿지 않는다: 못 읽으면 건너뛴다
        return None, f"읽기 실패: {type(e).__name__}"
    im = ImageOps.exif_transpose(im)
    has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    im = im.convert("RGBA" if has_alpha else "RGB")
    if has_alpha and im.getchannel("A").getextrema()[0] == 255:   # 알파가 있어도 전부 불투명
        im, has_alpha = im.convert("RGB"), False
    if has_alpha:
        # 투명 캔버스에 작은 그림(JJMack 도트: 256px 캔버스 가운데 수십 px)은 그림만 잘라낸다.
        # 안 자르면 pHash 가 빈 캔버스 모양을 봐서 다른 종끼리 같은 그림으로 묶인다 (2026-10-25 확인)
        bbox = im.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
        if bbox is None:
            return None, "빈 이미지"
        pad = int(max(bbox[2] - bbox[0], bbox[3] - bbox[1]) * CROP_PAD)
        bbox = (max(0, bbox[0] - pad), max(0, bbox[1] - pad), min(im.width, bbox[2] + pad), min(im.height, bbox[3] + pad))
        im = im.crop(bbox)
    w, h = im.size
    if max(w, h) < MIN_SIDE:
        return None, f"작음 {w}x{h}"
    if max(w, h) > MAX_SIDE:
        im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    flat = im
    if has_alpha:
        flat = Image.new("RGB", im.size, (255, 255, 255))
        flat.paste(im, mask=im.getchannel("A"))
    ph = phash_int(flat)
    color = np.asarray(flat.resize((8, 8), Image.BILINEAR), dtype=np.uint8)
    ext = "png" if has_alpha else "jpg"
    rel = f"{task['class_id']}/{task['source_id']}__{ph:016x}__{task['uid']}.{ext}"
    out = paths.POOL / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    if has_alpha:
        im.save(out, optimize=True)
    else:
        im.save(out, quality=92)
    rec = {k: task.get(k, "") for k in ("class_id", "form", "source_id", "source_ref", "image_type", "view", "shiny")}
    rec.update(file=rel, w=w, h=h, has_alpha=int(has_alpha), small=int(min(w, h) < SMALL_SIDE),
               phash=f"{ph:016x}", color=color.tobytes().hex())
    return rec, ""


# ---------- 출처별 작업 목록 ----------
def tasks_serebii(limit):
    root = paths.RAW / "serebii"
    with (root / "manifest.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for i, r in enumerate(rows[:limit] if limit else rows):
        yield {"path": str(root / r["file"]), "class_id": r["class_id"], "form": r["form_tag"],
               "source_id": "serebii", "source_ref": r["file"], "image_type": r["image_type"],
               "view": "front", "shiny": r["shiny"], "uid": f"{i:06d}"}


def jj_type(fname):
    tail = re.sub(r"^\d+-[^-]+-?", "", fname).rsplit(".", 1)[0].rstrip("-0123456789").lower()
    view = "back" if "back" in tail else "front"
    t = next((t for pat, t in JJ_TYPES if re.match(pat, tail)), "mixed")
    return t, view


def tasks_jjmack(limit):
    import pyarrow.parquet as pq
    sid = "hf-jjmack-gen1-9"
    lm = load_label_map(sid)
    n = 0
    for p in sorted((paths.RAW / sid).glob("*.parquet")):
        pf = pq.ParquetFile(p)
        for batch in pf.iter_batches(batch_size=256, columns=["label", "file_name", "image_data", "shiny"]):
            d = batch.to_pydict()
            for lab, fn, data, shiny in zip(d["label"], d["file_name"], d["image_data"], d["shiny"]):
                cid, form = lm.get(str(lab), ("", ""))
                t, view = jj_type(fn)
                yield {"bytes": data, "class_id": cid, "form": form, "source_id": sid,
                       "source_ref": f"{p.name}:{fn}", "image_type": t, "view": view,
                       "shiny": int(str(shiny).lower() == "yes"), "uid": f"{n:06d}"}
                n += 1
                if limit and n >= limit:
                    return


def tasks_kg_gen19(limit):
    sid = "kg-gen1-9-complete"
    lm = load_label_map(sid)
    z = np.load(paths.RAW / sid / "pokemon_augmented_64x64.npz")
    imgs, labels, names = z["images"], z["labels"], z["filenames"]
    # npz labels 가 manifest 의 label(1~1290)과 같은 체계인지는 파일명 앞 번호로 확인한다
    for i in range(len(imgs) if not limit else min(limit, len(imgs))):
        fn = str(names[i])
        lab = fn.split("_", 1)[0]
        cid, form = lm.get(lab, ("", ""))
        yield {"array": imgs[i], "class_id": cid, "form": form, "source_id": sid, "source_ref": f"npz:{fn}",
               "image_type": "sprite", "view": "front", "shiny": "", "uid": f"{i:06d}"}


def tasks_kg_1000(limit):
    sid = "kg-1000-pokemon"
    lm = load_label_map(sid)
    base = paths.RAW / sid / "pokemon-dataset-1000" / "dataset"
    n = 0
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        cid, form = lm.get(d.name, ("", ""))
        for f in sorted(d.iterdir()):
            if f.is_file():
                yield {"path": str(f), "class_id": cid, "form": form, "source_id": sid,
                       "source_ref": f"{d.name}/{f.name}", "image_type": "mixed", "view": "front",
                       "shiny": "", "uid": f"{n:06d}"}
                n += 1
                if limit and n >= limit:
                    return


SOURCES = {"serebii": tasks_serebii, "hf-jjmack-gen1-9": tasks_jjmack,
           "kg-gen1-9-complete": tasks_kg_gen19, "kg-1000-pokemon": tasks_kg_1000}


# ---------- 중복·그룹·분할 ----------
class UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[max(a, b)] = min(a, b)


def popcount64(x):
    x = x - ((x >> 1) & 0x5555555555555555)
    x = (x & 0x3333333333333333) + ((x >> 2) & 0x3333333333333333)
    x = (x + (x >> 4)) & 0x0F0F0F0F0F0F0F0F
    return (x * 0x0101010101010101) >> 56


def near_pairs(hashes):
    """해밍 거리 <= NEAR 인 쌍. 64bit 를 NEAR+1 조각으로 나누면 가까운 쌍은 적어도 한 조각이 같다 (비둘기집)."""
    h = np.array(hashes, dtype=np.uint64)
    k = NEAR + 1
    bounds = np.linspace(0, 64, k + 1).astype(int)
    pairs = set()
    for a, b in zip(bounds[:-1], bounds[1:]):
        chunk = (h >> np.uint64(a)) & np.uint64((1 << (b - a)) - 1)
        order = np.argsort(chunk, kind="stable")
        sc = chunk[order]
        cuts = np.flatnonzero(np.diff(sc)) + 1
        for grp in np.split(order, cuts):
            if len(grp) < 2:
                continue
            g = np.sort(grp)
            hv = h[g]
            for i in range(len(g) - 1):
                d = popcount64(hv[i] ^ hv[i + 1:])
                for j in np.flatnonzero(d <= NEAR):
                    pairs.add((int(g[i]), int(g[i + 1 + j])))
    return pairs


def split_of(key):
    v = int(hashlib.sha1(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "train" if v < SPLIT[0] else "val" if v < SPLIT[0] + SPLIT[1] else "test"


def finalize(recs):
    only = [r for r in recs if r["source_id"] in TRAIN_ONLY]
    recs = [r for r in recs if r["source_id"] not in TRAIN_ONLY]
    out_only, n_drop_only = dedupe_train_only(only)
    out, n_drop, n_conflict, n_groups = finalize_grouped(recs)
    return out + out_only, n_drop + n_drop_only, n_conflict, n_groups


def dedupe_train_only(recs):
    """train 전용 출처: 같은 종 + 같은 pHash + 색 거의 같음만 지우고 전부 train."""
    seen, out, drop = {}, [], 0
    for r in recs:
        k = (r["class_id"], r["phash"])
        c = np.frombuffer(bytes.fromhex(r["color"]), dtype=np.uint8).astype(np.int16)
        if any(np.abs(c - c2).mean() <= COLOR_TOL for c2 in seen.get(k, [])):
            (paths.POOL / r["file"]).unlink(missing_ok=True)
            drop += 1
            continue
        seen.setdefault(k, []).append(c)
        out.append(dict(r, group=f"train-only:{r['source_id']}", split="train", conflict=0))
    return out, drop


def finalize_grouped(recs):
    n = len(recs)
    t0 = time.monotonic()
    hashes = [int(r["phash"], 16) for r in recs]
    pairs = near_pairs(hashes)
    print(f"  가까운 쌍 {len(pairs):,} ({time.monotonic() - t0:.0f}s)", flush=True)
    uf = UF(n)
    for a, b in pairs:
        uf.union(a, b)

    # 중복 제거: 같은 종 + 같은 pHash + 색 거의 같음 → 큰 것 하나
    colors = [np.frombuffer(bytes.fromhex(r["color"]), dtype=np.uint8).astype(np.int16) for r in recs]
    drop = set()
    buckets = collections.defaultdict(list)
    for i, r in enumerate(recs):
        buckets[(r["class_id"], r["phash"])].append(i)
    for idx in buckets.values():
        if len(idx) < 2:
            continue
        idx.sort(key=lambda i: -(int(recs[i]["w"]) * int(recs[i]["h"])))
        kept = []
        for i in idx:
            if any(np.abs(colors[i] - colors[j]).mean() <= COLOR_TOL for j in kept):
                drop.add(i)
            else:
                kept.append(i)

    groups = collections.defaultdict(list)
    for i in range(n):
        if i not in drop:
            groups[uf.find(i)].append(i)
    out, n_conflict = [], 0
    for root, idx in groups.items():
        classes = {recs[i]["class_id"] for i in idx}
        gid = recs[root]["phash"] if root not in drop else recs[idx[0]]["phash"]
        conflict = len(classes) > 1
        sp = "conflict" if conflict else split_of(gid)
        for i in idx:
            r = dict(recs[i], group=gid, split=sp, conflict=int(conflict))
            out.append(r)
        n_conflict += len(idx) if conflict else 0
    for i in drop:
        (paths.POOL / recs[i]["file"]).unlink(missing_ok=True)
    return out, len(drop), n_conflict, len(groups)


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true", help="workspace/pool 을 지우고 새로 만든다 (재생성 산출물)")
    ap.add_argument("--only", nargs="+", choices=list(SOURCES), help="이 출처만 (시험용)")
    ap.add_argument("--limit", type=int, default=0, help="출처마다 이만큼만 (시험용)")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    paths.require_drive()
    if paths.POOL.exists() and any(paths.POOL.iterdir()):
        if not args.rebuild:
            sys.exit(f"{paths.POOL} 가 이미 있다. 다시 만들려면 --rebuild (pool 은 재생성 산출물이다).")
        shutil.rmtree(paths.POOL)
    paths.POOL.mkdir(parents=True)

    srcs = {s["id"]: s for s in yaml.safe_load(paths.SOURCES.read_text(encoding="utf-8"))["sources"]}
    ok_classes = enabled_classes()
    recs, skipped = [], collections.Counter()
    for sid in args.only or SOURCES:
        s = srcs[sid]
        if s.get("use") != "train" or not s.get("enabled") or s.get("skip"):
            print(f"{sid}: 건너뜀 (use={s.get('use')}, enabled={s.get('enabled')}, skip={s.get('skip')})")
            continue
        t0 = time.monotonic()
        tasks = (t for t in SOURCES[sid](args.limit)
                 if (t["class_id"] in ok_classes) or skipped.update([(sid, "라벨 없음·skip·비활성")]))
        n_ok = 0
        with Pool(args.workers) as pool:
            for rec, why in pool.imap_unordered(process, tasks, chunksize=32):
                if rec:
                    recs.append(rec)
                    n_ok += 1
                else:
                    skipped[(sid, why.split(" ")[0])] += 1
                if n_ok and n_ok % 20000 == 0:
                    print(f"  {sid}: {n_ok:,}장 ({time.monotonic() - t0:.0f}s)", flush=True)
        print(f"{sid}: {n_ok:,}장 ({(time.monotonic() - t0) / 60:.1f}분)", flush=True)

    print(f"중복·그룹·분할 ({len(recs):,}장)…", flush=True)
    out, n_drop, n_conflict, n_groups = finalize(recs)
    out.sort(key=lambda r: (r["class_id"], r["file"]))
    with paths.POOL_MANIFEST.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)

    # 보고
    by_split = collections.Counter(r["split"] for r in out)
    by_src = collections.Counter(r["source_id"] for r in out)
    per_class = collections.Counter(r["class_id"] for r in out if r["split"] == "train")
    few = sorted((c for c in ok_classes if per_class[c] < 20), key=lambda c: per_class[c])
    print(f"\n완료: pool {len(out):,}장 (중복 제거 {n_drop:,}, 그룹 {n_groups:,}), 분할 {dict(by_split)}")
    print(f"  출처별 {dict(by_src)}")
    print(f"  종 충돌로 뺀 이미지 {n_conflict:,} (split=conflict)")
    print(f"  train 20장 미만 종 {len(few)}: {', '.join(f'{c}({per_class[c]})' for c in few[:25])}{' …' if len(few) > 25 else ''}")
    if skipped:
        print(f"  건너뜀 {dict(skipped)}")
    print(f"  {paths.POOL_MANIFEST}")


if __name__ == "__main__":
    main()
