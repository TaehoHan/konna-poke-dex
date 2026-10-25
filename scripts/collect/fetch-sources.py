"""data/sources.yaml 의 Hugging Face·Kaggle 데이터셋 -> 구글 드라이브 paths.RAW/<id>/ (받은 그대로).

받는 순서: 로컬 paths.STAGING/<id>/ 에 받고 _SOURCE.json 까지 쓴 뒤, 이번 실행에서 받은 것을 모두 받으면
           드라이브 paths.RAW/<id>/ 로 한 번에 옮긴다 (드라이브에 작은 파일을 하나씩 쓰면 드라이브 앱이 느려지고
           DB 잠김 오류가 난다, 2026-10-24). 옮기다 실패하면 STAGING 에 남아 있고, 다시 실행하면 옮기기부터 한다.

로그인 없이 받는다 (2026-10-24 결정). 토큰을 쓰지 않고, 로그인이 필요한 출처(HF 게이트·Kaggle 401/403)는 받지 않는다.
  Hugging Face  huggingface_hub.snapshot_download(token=False) → <id>/   (HF_HOME = paths.HF_CACHE, 로컬)
  Kaggle        GET https://www.kaggle.com/api/v1/datasets/download/<ref> (302 → 서명된 zip) → 풀어서 <id>/
                zip 은 푼 뒤 지운다 (sha256 은 _SOURCE.json 에 남긴다). `kaggle` 패키지는 import 때 토큰을 찾아 쓰지 않는다.
  url           ref 의 zip 을 그대로 받아 풀어서 <id>/ (예: COCO val2017)
pokeapi·crawl(세레비넷) 출처는 각자 스크립트(fetch-pokedex.py, fetch-serebii.py)가 받는다.

허락 관문: 받는 것은 `enabled: true` 이고 `skip` 이 없는 출처뿐이다. --dry-run 은 메타데이터만 조회한다 (다운로드 없음).
디스크 관문: 로컬 STAGING 에 받고(Kaggle 은 zip + 푼 파일이 잠시 같이 있다), 드라이브 앱도 업로드가 끝날 때까지
             로컬에 들고 있다. 이번에 받을 것 합계 x 2 + 5GB 보다 로컬 여유가 적으면 받지 않는다.
             --max-gb(기본 3) 보다 큰 출처는 --allow-large 를 줘야 받는다.
재실행: paths.RAW/<id>/_SOURCE.json 이 있으면 건너뛴다 (--force 로 다시 받기). STAGING 에 다 받은 것이 남아 있으면 옮기기만 한다.

사용:
  .venv/bin/python scripts/collect/fetch-sources.py --dry-run                 # 받을 대상·용량·라이선스·로그인 필요 여부
  .venv/bin/python scripts/collect/fetch-sources.py --dry-run --all           # skip·enabled 와 관계없이 전부 조회
  .venv/bin/python scripts/collect/fetch-sources.py --only hf-jjmack-gen1-9   # 허락 받은(enabled: true) 출처만
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/ (paths.py)
import paths  # noqa: E402
import argparse
import hashlib
import json
import os
import shutil
import time
import zipfile
from datetime import date

os.environ.setdefault("HF_HOME", str(paths.HF_CACHE))   # huggingface_hub import 전에
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

import requests  # noqa: E402
import yaml  # noqa: E402

KINDS = ("huggingface", "kaggle", "url")
KAGGLE_API = "https://www.kaggle.com/api/v1/datasets"
USER_AGENT = "konna-poke-dex/0.1 (personal project)"
DISK_MARGIN = 5e9


# ---------- 메타데이터 ----------
def hf_meta(ref):
    from huggingface_hub import HfApi
    from huggingface_hub.utils import GatedRepoError, RepositoryNotFoundError
    try:
        info = HfApi().dataset_info(ref, files_metadata=True, token=False)
    except GatedRepoError:
        return {"ok": False, "why": "게이트(로그인·약관 동의 필요)"}
    except RepositoryNotFoundError:
        return {"ok": False, "why": "없음 또는 비공개(로그인 필요)"}
    if info.gated:
        return {"ok": False, "why": f"게이트({info.gated})"}
    sib = info.siblings or []
    lic = [t.split(":", 1)[1] for t in (info.tags or []) if t.startswith("license:")]
    return {"ok": True, "bytes": sum(s.size or 0 for s in sib), "files": len(sib),
            "license": ",".join(lic), "revision": info.sha}


def kaggle_meta(ref):
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    r = s.get(f"{KAGGLE_API}/view/{ref}", timeout=30)
    if r.status_code in (401, 403):
        return {"ok": False, "why": f"메타데이터 HTTP {r.status_code} (로그인 필요)"}
    if r.status_code != 200:
        return {"ok": False, "why": f"메타데이터 HTTP {r.status_code}"}
    d = r.json()
    # 다운로드가 로그인 없이 되는지: 리다이렉트만 확인하고 따라가지 않는다 (HEAD 는 404 라 GET 으로)
    r2 = s.get(f"{KAGGLE_API}/download/{ref}", timeout=30, allow_redirects=False, stream=True)
    r2.close()
    if r2.status_code != 302:
        return {"ok": False, "why": f"익명 다운로드 HTTP {r2.status_code} (로그인 필요)"}
    return {"ok": True, "bytes": d.get("totalBytes") or 0, "files": None,
            "license": d.get("licenseName") or "", "revision": str(d.get("currentVersionNumber") or "")}


def url_meta(ref):
    r = requests.head(ref, timeout=30, allow_redirects=True, headers={"User-Agent": USER_AGENT})
    if r.status_code != 200:
        return {"ok": False, "why": f"HTTP {r.status_code}"}
    return {"ok": True, "bytes": int(r.headers.get("content-length") or 0), "files": None, "license": "",
            "revision": r.headers.get("last-modified", "")}


def meta(src):
    if src["kind"] == "url":
        m = url_meta(src["ref"])
        if m["ok"]:
            m["license"] = src.get("license", "")
        return m
    return hf_meta(src["ref"]) if src["kind"] == "huggingface" else kaggle_meta(src["ref"])


# ---------- 다운로드 ----------
def safe_extract(zip_path, dest):
    """받은 zip 은 믿지 않는다: 절대 경로·.. 로 dest 밖에 쓰는 항목이 있으면 멈춘다."""
    dest = dest.resolve()
    with zipfile.ZipFile(zip_path) as z:
        for m in z.infolist():
            target = (dest / m.filename).resolve()
            if not target.is_relative_to(dest):
                raise RuntimeError(f"zip 안에 폴더 밖 경로가 있다: {m.filename}")
        z.extractall(dest)
        return len([m for m in z.infolist() if not m.is_dir()])


def download_kaggle(src, dest):
    return download_zip(f"{KAGGLE_API}/download/{src['ref']}", dest)


def download_zip(url, dest):
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    part = dest / "_download.zip.part"
    h = hashlib.sha256()
    n = 0
    t0 = last = time.monotonic()
    with s.get(url, timeout=60, stream=True) as r:
        if r.status_code != 200:
            raise RuntimeError(f"다운로드 HTTP {r.status_code}")
        total = int(r.headers.get("content-length") or 0)
        with part.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                h.update(chunk)
                n += len(chunk)
                if time.monotonic() - last > 30:
                    last = time.monotonic()
                    pct = f"{n / total * 100:.0f}%" if total else ""
                    print(f"    {n / 1e6:,.0f}MB {pct}  {n / 1e6 / (last - t0):.1f}MB/s", flush=True)
    print(f"    받음 {n / 1e6:,.0f}MB, 푸는 중…", flush=True)
    n_files = safe_extract(part, dest)
    part.unlink()
    return {"files": n_files, "zip_bytes": n, "zip_sha256": h.hexdigest()}


def download_hf(src, dest):
    from huggingface_hub import snapshot_download
    snapshot_download(src["ref"], repo_type="dataset", local_dir=dest, token=False)
    files = [p for p in dest.rglob("*") if p.is_file() and ".cache" not in p.parts]
    return {"files": len(files)}


def folder_bytes(p):
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", metavar="ID", help="이 출처만")
    ap.add_argument("--dry-run", action="store_true", help="메타데이터만 조회 (다운로드 없음)")
    ap.add_argument("--all", action="store_true", help="--dry-run 에서 enabled·skip 과 관계없이 HF·Kaggle 출처 전부 조회")
    ap.add_argument("--max-gb", type=float, default=3.0, help="이보다 큰 출처는 --allow-large 가 있어야 받는다")
    ap.add_argument("--allow-large", action="store_true")
    ap.add_argument("--force", action="store_true", help="_SOURCE.json 이 있어도 다시 받는다")
    args = ap.parse_args()

    paths.require_drive()
    sources = [s for s in yaml.safe_load(paths.SOURCES.read_text(encoding="utf-8"))["sources"] if s["kind"] in KINDS]
    if args.only:
        unknown = set(args.only) - {s["id"] for s in sources}
        if unknown:
            sys.exit(f"sources.yaml 에 없는 HF·Kaggle 출처: {sorted(unknown)}")
        sources = [s for s in sources if s["id"] in args.only]
    elif not (args.dry_run and args.all):
        sources = [s for s in sources if s.get("enabled") and not s.get("skip")]
    if not sources:
        sys.exit("대상 출처가 없다. sources.yaml 에서 허락 받은 출처를 enabled: true 로 바꾸거나, --dry-run --all 로 조회한다.")

    free = shutil.disk_usage(Path.home()).free
    print(f"로컬 디스크 여유 {free / 1e9:.1f}GB, 받을 곳 {paths.RAW}")
    print(f"{'id':22} {'상태':10} {'용량':>9} {'파일':>6}  라이선스 / 비고")
    plan = []
    for s in sources:
        done = (paths.RAW / s["id"] / "_SOURCE.json").exists()
        staged = (paths.STAGING / s["id"] / "_SOURCE.json").exists()
        m = meta(s)
        if not m["ok"]:
            status, size, note = "로그인필요", "", m["why"]
        else:
            size, note = f"{m['bytes'] / 1e9:.2f}GB", m["license"]
            if s.get("skip"):
                status, note = "skip", f"{note} / {s['skip']}"
            elif not s.get("enabled"):
                status = "미허락"
            elif done and not args.force:
                status = "받음"
            elif staged and not args.force:
                status = "옮길것"
                plan.append((s, m))
            elif m["bytes"] > args.max_gb * 1e9 and not args.allow_large:
                status, note = "큼", f"{note} / --allow-large 필요"
            elif free < (sum(pm["bytes"] for _, pm in plan) + m["bytes"]) * 2 + DISK_MARGIN:
                status, note = "디스크부족", f"{note} / 로컬 여유 {(m['bytes'] * 2 + DISK_MARGIN) / 1e9:.1f}GB 필요"
            else:
                status = "받을것"
                plan.append((s, m))
        files = "" if not m.get("files") else m["files"]
        print(f"{s['id']:22} {status:10} {size:>9} {files!s:>6}  {note}")

    if args.dry_run:
        tot = sum(m["bytes"] for _, m in plan)
        print(f"[dry-run] 받을 것 {len(plan)}개, 합계 {tot / 1e9:.2f}GB. 받지 않았다.")
        return

    staged_ok = []
    for s, m in plan:
        dest = paths.STAGING / s["id"]
        if (dest / "_SOURCE.json").exists() and not args.force:
            staged_ok.append(s)
            continue
        if dest.exists():
            shutil.rmtree(dest)   # 지난번에 받다 만 것 (STAGING 은 임시 폴더)
        dest.mkdir(parents=True)
        print(f"== {s['id']} ({s['kind']}: {s['ref']}, {m['bytes'] / 1e9:.2f}GB)", flush=True)
        t0 = time.monotonic()
        try:
            extra = (download_hf(s, dest) if s["kind"] == "huggingface"
                     else download_kaggle(s, dest) if s["kind"] == "kaggle" else download_zip(s["ref"], dest))
        except Exception as e:
            print(f"   실패: {e}  (다시 실행하면 이 출처를 다시 받는다)", flush=True)
            continue
        info = {"id": s["id"], "kind": s["kind"], "ref": s["ref"], "fetched": date.today().isoformat(),
                "revision": m.get("revision", ""), "bytes": folder_bytes(dest), "license": m["license"],
                "license_yaml": s.get("license", ""), **extra}
        (dest / "_SOURCE.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"   완료: 파일 {info['files']}, {info['bytes'] / 1e9:.2f}GB, {(time.monotonic() - t0) / 60:.1f}분", flush=True)
        staged_ok.append(s)

    # 드라이브로 한 번에 옮기기
    if staged_ok:
        print(f"== 드라이브로 옮기기: {[s['id'] for s in staged_ok]}", flush=True)
    for s in staged_ok:
        src_dir, dst_dir = paths.STAGING / s["id"], paths.RAW / s["id"]
        t0 = time.monotonic()
        if dst_dir.exists():
            if args.force or not any(dst_dir.iterdir()):
                shutil.rmtree(dst_dir)
            else:
                print(f"   건너뜀 {s['id']}: 드라이브에 이미 있다 ({dst_dir}). --force 로 덮어쓴다", flush=True)
                continue
        tmp = dst_dir.with_name(dst_dir.name + ".moving")
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(src_dir, tmp)
        tmp.rename(dst_dir)           # 다 복사된 뒤에만 제 이름이 된다
        shutil.rmtree(src_dir)
        print(f"   옮김 {s['id']}: {(time.monotonic() - t0) / 60:.1f}분", flush=True)


if __name__ == "__main__":
    main()
