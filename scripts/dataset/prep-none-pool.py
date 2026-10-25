"""none 풀 준비: 드라이브의 none_pool 출처(sources.yaml use: none_pool) → 로컬 workspace/bg_pool/<id 짧은 이름>/ (짧은 변 320px JPG)

지금은 COCO val2017 (5,000장, 일상 사물·실내·거리) → bg_pool/coco/.
make-dataset.py 가 none 클래스(포켓몬이 아닌 이미지)와 포켓몬 합성 배경으로 쓴다. 6장 중 1장은 val/test 전용.
원본(paths.RAW)은 읽기만 한다. bg_pool/coco 는 다시 만들 수 있다.

사용: .venv/bin/python scripts/dataset/prep-none-pool.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/ (paths.py)
import paths  # noqa: E402
from concurrent.futures import ProcessPoolExecutor

from PIL import Image

SHORT = 320
TARGETS = {"coco-val2017": "coco"}


def shrink(job):
    src, dst = job
    try:
        im = Image.open(src)
        im.load()
        im = im.convert("RGB")
        s = SHORT / min(im.size)
        if s < 1:
            im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
        im.save(dst, quality=90)
        return 1
    except Exception:  # 받은 파일은 믿지 않는다: 못 읽으면 건너뛴다
        return 0


def main():
    paths.require_drive()
    for sid, name in TARGETS.items():
        src = sorted((paths.RAW / sid).rglob("*.jpg"))
        dst = paths.BG_POOL / name
        dst.mkdir(parents=True, exist_ok=True)
        jobs = [(p, dst / p.name) for p in src if not (dst / p.name).exists()]
        with ProcessPoolExecutor(6) as ex:
            n = sum(ex.map(shrink, jobs, chunksize=50))
        print(f"{sid} → bg_pool/{name}: 새로 {n}/{len(jobs)}, 전체 {len(list(dst.glob('*.jpg')))}장")


if __name__ == "__main__":
    main()
