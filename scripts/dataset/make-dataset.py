"""포켓몬 ① 종 모델 학습 데이터: workspace/pool(정규화된 실제 이미지) + none -> workspace/dataset_cls/{train,val,test}/<class_id>/*.jpg

ultralytics classify 포맷. 클래스 = data/list-poke.csv 의 enabled=1 종 + none. 폼은 종에 합친다 (DESIGN.md 2.1).
teeniping-dex 의 make-dataset.py 에서 포크했다. 증강 함수(sticker_fx, photo_crop, random_background, occlude, paste, augment)는
그대로 쓰고, 원본 1장 합성 대신 pool 의 이미지를 쓴다.

만드는 법
  train  클래스마다 --train 장. pool 의 train 분할 이미지를 출처·이미지 종류가 고르게 돌아가며 고르고(뒷모습은 절반 가중치),
         투명 배경(대부분)은 무작위 배경에 크기·회전·원근·가림·스티커 효과를 주어 얹고, 불투명(kg-gen1-9 64px 등)은 잘라내기·확대.
         --distractor 확률로 다른 종을 작게 뒤에 얹는다. 그 뒤 색·흐림·노이즈·저해상도 증강(augment).
  val    pool 의 val 분할에서 클래스마다 최대 --val 장, 한 이미지당 한 장, 약한 증강(시드 고정)
  test   pool 의 test 분할에서 클래스마다 최대 --test 장, val 과 같은 방식. 출처별·종류별 평가용 (같은 분포라 실제 카메라 성능이 아니다)
  none   배경 사진(bg_pool/photos + bg_pool/coco: COCO val2017 일상 사물·실내 5천 장) · 다른 만화 캐릭터(bg_pool/chars) ·
         티니핑 원본(bg_pool/teeniping, 투명 PNG)을 배경에 얹은 것 · 합성 배경. (train_v1 은 COCO 없이 학습해 세면대·치약을 갈모매로 오탐했다)
         배경 풀 6장 중 1장은 val/test 전용 (누수 방지)

pool 의 분할(pHash 그룹 단위)을 그대로 따르므로 같은 그림이 train 과 val/test 에 걸치지 않는다. split=conflict 는 쓰지 않는다.
manifest: dataset_cls/manifest.csv (split,class_id,file,pool_file,source_id,image_type,view,form,small) — 출처별 평가에 쓴다.

사용:
  .venv/bin/python scripts/dataset/make-dataset.py                      # 기본 train 300 / val 30 / test 50
  .venv/bin/python scripts/dataset/make-dataset.py --limit-classes 20   # 시험
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/ (paths.py)
import paths  # noqa: E402
import argparse
import collections
import csv
import glob
import os
import random
import shutil
import time
from multiprocessing import Pool

import cv2
import numpy as np
from PIL import Image

NONE = "none"


def sticker_fx(sprite, rng):
    """스티커·인쇄물 느낌: 흰 테두리, 광택 줄, 색 탁함/대비 변화, 일부 색조만 바꾸기(리본처럼 한 부분의 색이 다른 인쇄본)."""
    a = np.array(sprite.convert("RGBA"))
    h, w = a.shape[:2]
    rgb = a[..., :3].astype(np.float32)
    if rng.random() < 0.5:                       # 일부 색조만 이동 (HSV 의 한 색상 대역)
        hsv = cv2.cvtColor(a[..., :3], cv2.COLOR_RGB2HSV).astype(np.int16)
        c = rng.randint(0, 179); bw = rng.randint(10, 25); sh = rng.choice([-1, 1]) * rng.randint(15, 45)
        m = (np.minimum(abs(hsv[..., 0] - c), 180 - abs(hsv[..., 0] - c)) < bw) & (hsv[..., 1] > 60)
        hsv[..., 0] = np.where(m, (hsv[..., 0] + sh) % 180, hsv[..., 0])
        rgb = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB).astype(np.float32)
    if rng.random() < 0.6:                       # 인쇄 색: 채도·대비·밝기 변화, 약한 색 틀어짐
        g = rgb.mean(2, keepdims=True)
        rgb = g + (rgb - g) * rng.uniform(0.65, 1.15)
        rgb = (rgb - 128) * rng.uniform(0.85, 1.15) + 128 + rng.uniform(-12, 12)
        rgb += np.array([rng.uniform(-10, 10) for _ in range(3)], np.float32)
    if rng.random() < 0.35:                      # 광택: 비스듬한 흰 줄
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        ang = rng.uniform(0, np.pi); d = (xx * np.cos(ang) + yy * np.sin(ang)) / max(h, w)
        c0 = rng.uniform(0.2, 0.8); band = np.exp(-((d - c0) ** 2) / (2 * rng.uniform(0.02, 0.08) ** 2))
        rgb += band[..., None] * rng.uniform(25, 70)
    a[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    if rng.random() < 0.5:                       # 스티커 흰 테두리
        al = (a[..., 3] > 8).astype(np.uint8) * 255
        r = max(2, int(min(h, w) * rng.uniform(0.02, 0.05)))
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        grown = cv2.dilate(al, k)
        pad = r + 2
        canvas = np.zeros((h + 2 * pad, w + 2 * pad, 4), np.uint8)
        gi = np.pad(grown, pad)
        canvas[..., :3] = 245
        canvas[..., 3] = cv2.GaussianBlur(gi, (3, 3), 0)
        src = np.pad(a, ((pad, pad), (pad, pad), (0, 0)))
        m = src[..., 3:4].astype(np.float32) / 255
        canvas[..., :3] = (src[..., :3] * m + canvas[..., :3] * (1 - m)).astype(np.uint8)
        return Image.fromarray(canvas)
    return Image.fromarray(a)


def real_photo_sample(rng, img, size, bg_photos):
    """실제 사진 한 장(BGR)에서 학습 샘플을 만든다: 70% 는 무작위 확대·잘라내기, 30% 는 작게 줄여 다른 배경 위에 얹는다."""
    if rng.random() < 0.7:
        return photo_crop(rng, img, size)
    canvas = random_background(rng, size, bg_photos)
    s = rng.uniform(0.3, 0.8)
    th = int(size * s)
    tw = int(th * img.shape[1] / img.shape[0])
    tw = max(16, min(size, tw))
    small = cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA)
    x0 = rng.randint(0, max(0, size - tw)); y0 = rng.randint(0, max(0, size - th))
    canvas[y0:y0 + th, x0:x0 + tw] = small[:size - y0, :size - x0]
    return canvas


def photo_crop(rng, img, size):
    """사진(BGR)에서 무작위 영역을 잘라 size x size 로 만든다 (확대·종횡비 변화·좌우 반전 포함)."""
    h, w = img.shape[:2]
    side = rng.uniform(0.35, 1.0) * min(h, w)
    cw = int(min(w, side * rng.uniform(0.8, 1.25)))
    ch = int(min(h, side * rng.uniform(0.8, 1.25)))
    x, y = rng.randint(0, w - cw), rng.randint(0, h - ch)
    out = cv2.resize(img[y:y + ch, x:x + cw], (size, size), interpolation=cv2.INTER_AREA)
    return out[:, ::-1].copy() if rng.random() < 0.5 else out


def random_background(rng, size, photos=None, p_photo=0.6):
    """실제 사진 풀이 있으면 p_photo 확률로 사진을, 아니면 단색/그라디언트/노이즈/색 블록 배경을 만든다."""
    if photos and rng.random() < p_photo:
        return photo_crop(rng, rng.choice(photos), size)
    kind = rng.choice(["solid", "grad", "noise", "blocks", "white"])
    if kind == "white":
        c = rng.randint(220, 255)
        return np.full((size, size, 3), c, np.uint8)
    if kind == "solid":
        return np.full((size, size, 3), [rng.randint(0, 255) for _ in range(3)], np.uint8)
    if kind == "grad":
        c1, c2 = np.array([rng.randint(0, 255) for _ in range(3)]), np.array([rng.randint(0, 255) for _ in range(3)])
        t = np.linspace(0, 1, size)[:, None, None]
        g = (c1 * (1 - t) + c2 * t) * np.ones((1, size, 1))
        if rng.random() < 0.5:
            g = g.transpose(1, 0, 2)
        return g.astype(np.uint8)
    if kind == "noise":
        base = np.array([rng.randint(60, 200) for _ in range(3)])
        n = np.random.RandomState(rng.randint(0, 1 << 30)).randint(-40, 40, (size, size, 3))
        return np.clip(base + n, 0, 255).astype(np.uint8)
    bg = np.full((size, size, 3), [rng.randint(0, 255) for _ in range(3)], np.uint8)
    for _ in range(rng.randint(3, 8)):
        x0, y0 = rng.randint(0, size), rng.randint(0, size)
        x1, y1 = x0 + rng.randint(20, size // 2), y0 + rng.randint(20, size // 2)
        cv2.rectangle(bg, (x0, y0), (x1, y1), [rng.randint(0, 255) for _ in range(3)], -1)
    return bg


def occlude(sprite, rng):
    """스프라이트 알파 일부를 지운 복사본을 돌려준다 (가림 증강). 보이는 면적이 50% 미만이면 원본을 그대로 쓴다."""
    a = np.array(sprite)
    h, w = a.shape[:2]
    orig = float((a[..., 3] > 8).sum())
    if orig == 0:
        return sprite
    if rng.random() < 0.5:   # 한쪽 띠 (너비/높이의 15~45%)
        f = rng.uniform(0.15, 0.45)
        side = rng.choice(["l", "r", "t", "b"])
        if side == "l":
            a[:, :int(w * f), 3] = 0
        elif side == "r":
            a[:, w - int(w * f):, 3] = 0
        elif side == "t":
            a[:int(h * f), :, 3] = 0
        else:
            a[h - int(h * f):, :, 3] = 0
    else:                    # 사각형 1~2개 (bbox 면적 합 15~40%)
        total = rng.uniform(0.15, 0.40)
        n = rng.choice([1, 2])
        for _ in range(n):
            ar = total / n
            rw = min(1.0, max(0.2, (ar * rng.uniform(0.6, 1.6)) ** 0.5))
            rh = min(1.0, ar / rw)
            bw, bh = int(w * rw), int(h * rh)
            x0, y0 = rng.randint(0, max(0, w - bw)), rng.randint(0, max(0, h - bh))
            a[y0:y0 + bh, x0:x0 + bw, 3] = 0
    if (a[..., 3] > 8).sum() < 0.5 * orig:
        return sprite
    return Image.fromarray(a)


def paste(canvas, sprite, rng, scale, rot, persp, flip):
    """canvas(BGR uint8) 위에 sprite(RGBA PIL)를 변형해서 얹는다. 중심 좌표 반환 불필요."""
    size = canvas.shape[0]
    s = sprite.transpose(Image.FLIP_LEFT_RIGHT) if flip else sprite
    w, h = s.size
    k = scale * size / max(w, h)
    s = s.resize((max(8, int(w * k)), max(8, int(h * k))), Image.LANCZOS)
    s = s.rotate(rot, expand=True, resample=Image.BICUBIC)
    sa = np.array(s)
    if persp:
        h2, w2 = sa.shape[:2]
        d = persp * min(w2, h2)
        src = np.float32([[0, 0], [w2, 0], [w2, h2], [0, h2]])
        dst = src + np.float32([[rng.uniform(-d, d), rng.uniform(-d, d)] for _ in range(4)])
        M = cv2.getPerspectiveTransform(src, dst)
        sa = cv2.warpPerspective(sa, M, (w2, h2), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0, 0))
    h2, w2 = sa.shape[:2]
    cx = rng.randint(int(size * 0.3), int(size * 0.7))
    cy = rng.randint(int(size * 0.3), int(size * 0.7))
    x0, y0 = cx - w2 // 2, cy - h2 // 2
    xs, ys = max(0, x0), max(0, y0)
    xe, ye = min(size, x0 + w2), min(size, y0 + h2)
    if xe <= xs or ye <= ys:
        return
    part = sa[ys - y0:ye - y0, xs - x0:xe - x0]
    alpha = part[..., 3:4].astype(np.float32) / 255
    rgb = part[..., :3][..., ::-1].astype(np.float32)  # RGB->BGR
    roi = canvas[ys:ye, xs:xe].astype(np.float32)
    canvas[ys:ye, xs:xe] = (rgb * alpha + roi * (1 - alpha)).astype(np.uint8)


def augment(img, rng):
    img = img.astype(np.float32)
    # 밝기/대비/채도/색온도
    img = img * rng.uniform(0.6, 1.3) + rng.uniform(-30, 30)
    hsv = cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] *= rng.uniform(0.6, 1.3)
    img = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32)
    img *= np.array([rng.uniform(0.9, 1.1) for _ in range(3)])
    # 색 정보 제거/왜곡: 흑백(저조도·흑백·적외선 카메라), 세피아, 강한 색조 변화 (모델이 색에만 의존하지 않게)
    r = rng.random()
    if r < 0.15:       # 흑백
        g = cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_BGR2GRAY)
        img = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR).astype(np.float32)
    elif r < 0.20:     # 세피아
        g = cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)[..., None]
        img = g * np.array([0.55, 0.75, 1.0], np.float32)
    elif r < 0.32:     # 강한 색조 변화 (조명 색)
        h = cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_BGR2HSV)
        h[..., 0] = (h[..., 0].astype(int) + rng.randint(-45, 45)) % 180
        img = cv2.cvtColor(h, cv2.COLOR_HSV2BGR).astype(np.float32)
    elif r < 0.40:     # 채도 크게 낮춤
        g = cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)[..., None]
        a = rng.uniform(0.15, 0.5)
        img = g * (1 - a) + img * a
    # 빛 반사(글레어) / 비네팅
    if rng.random() < 0.25:
        size = img.shape[0]
        g = np.zeros(img.shape[:2], np.float32)
        cv2.circle(g, (rng.randint(0, size), rng.randint(0, size)), rng.randint(20, 80), 1.0, -1)
        g = cv2.GaussianBlur(g, (0, 0), 25)[..., None]
        img = img + g * rng.uniform(40, 120)
    img = np.clip(img, 0, 255).astype(np.uint8)
    # 흐림/모션블러
    r = rng.random()
    if r < 0.3:
        k = rng.choice([3, 5, 7])
        img = cv2.GaussianBlur(img, (k, k), 0)
    elif r < 0.45:
        k = rng.choice([5, 7, 9])
        ker = np.zeros((k, k), np.float32)
        ker[k // 2, :] = 1 / k
        M = cv2.getRotationMatrix2D((k / 2 - 0.5, k / 2 - 0.5), rng.uniform(0, 180), 1)
        img = cv2.filter2D(img, -1, cv2.warpAffine(ker, M, (k, k)))
    # 센서 노이즈
    if rng.random() < 0.5:
        n = np.random.RandomState(rng.randint(0, 1 << 30)).normal(0, rng.uniform(2, 10), img.shape)
        img = np.clip(img + n, 0, 255).astype(np.uint8)
    # 저해상도 카메라 흉내(다운샘플 후 복원) + JPEG
    if rng.random() < 0.4:
        s = img.shape[0]
        f = rng.uniform(0.4, 0.8)
        img = cv2.resize(cv2.resize(img, (int(s * f),) * 2, interpolation=cv2.INTER_AREA), (s, s))
    return img


# ---------- pool 이미지 → 학습 샘플 ----------
def load_pool_image(rel):
    im = Image.open(paths.POOL / rel)
    im.load()
    return im


def compose(rng, rec, size, photos, strong):
    """pool 이미지 한 장 → BGR size x size. strong=False 면 val/test 용 약한 변형."""
    im = load_pool_image(rec["file"])
    if im.mode == "RGBA":
        sprite = im
        if strong and rng.random() < 0.3:
            sprite = sticker_fx(sprite, rng)
        canvas = random_background(rng, size, photos, p_photo=0.6 if strong else 0.4)
        if strong:
            scale = rng.uniform(0.35, 1.0)
            if DISTRACT and rng.random() < DISTRACT_P:
                other = rng.choice(DISTRACT)
                try:
                    paste(canvas, load_pool_image(other).convert("RGBA"), rng, min(rng.uniform(0.3, 0.5), scale * 0.6),
                          rng.uniform(-30, 30), 0.0, rng.random() < 0.5)
                except OSError:
                    pass
            if rng.random() < 0.25:
                sprite = occlude(sprite, rng)
            rot = rng.uniform(-25, 25) if rng.random() < 0.8 else rng.uniform(-80, 80)
            paste(canvas, sprite, rng, scale, rot, rng.choice([0, 0.05, 0.12]), rng.random() < 0.5)
        else:
            paste(canvas, sprite, rng, rng.uniform(0.6, 0.95), rng.uniform(-10, 10), 0.0, rng.random() < 0.5)
        return canvas
    bgr = cv2.cvtColor(np.asarray(im.convert("RGB")), cv2.COLOR_RGB2BGR)
    if strong:
        return photo_crop(rng, bgr, size) if rng.random() < 0.7 else real_photo_sample(rng, bgr, size, photos)
    return cv2.resize(bgr, (size, size), interpolation=cv2.INTER_AREA)


def none_sample(rng, size, split_pool, strong):
    photos, chars, teeni = split_pool["photos"], split_pool["chars"], split_pool["teeniping"]
    r = rng.random()
    if r < 0.55 and photos:    # 실제 장면(티니핑 배경 + COCO 일상 사물·실내)
        return photo_crop(rng, rng.choice(photos), size)
    if r < 0.65 and chars:
        return photo_crop(rng, rng.choice(chars), size)
    if r < 0.85 and teeni:      # 비슷한 그림체의 다른 캐릭터를 포켓몬처럼 배경에 얹는다 → 거부를 배운다
        canvas = random_background(rng, size, photos)
        paste(canvas, rng.choice(teeni), rng, rng.uniform(0.35, 1.0) if strong else 0.8,
              rng.uniform(-25, 25) if strong else 0, 0.0, rng.random() < 0.5)
        return canvas
    return random_background(rng, size, photos if strong else None)


def make_one(job):
    split, class_id, idx, rec, size, out_root, seed = job
    rng = random.Random(f"{seed}-{split}-{class_id}-{idx}")
    sp = BG["train" if split == "train" else "eval"]
    strong = split == "train"
    try:
        canvas = none_sample(rng, size, sp, strong) if class_id == NONE else compose(rng, rec, size, sp["photos"], strong)
    except OSError as e:
        return None, f"{rec.get('file') if rec else 'none'}: {e}"
    canvas = augment(canvas, rng) if strong else canvas
    rel = f"{split}/{class_id}/{idx:04d}.jpg"
    out = Path(out_root) / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), canvas, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(70, 95) if strong else 92])
    return {"split": split, "class_id": class_id, "file": rel, "pool_file": rec.get("file", "") if rec else "",
            "source_id": rec.get("source_id", "none") if rec else "none", "image_type": rec.get("image_type", "") if rec else "",
            "view": rec.get("view", "") if rec else "", "form": rec.get("form", "") if rec else "",
            "small": rec.get("small", "") if rec else ""}, ""


# ---------- 작업자 초기화 (배경 풀, 방해물 후보) ----------
BG = {}
DISTRACT = []
DISTRACT_P = 0.2


class LazyImages:
    """파일 경로 목록을 이미지 목록처럼 쓴다 (rng.choice 로 고를 때 읽는다). COCO 5천 장을 작업자마다 메모리에 올리지 않으려고."""

    def __init__(self, files):
        self.files = list(files)

    def __len__(self):
        return len(self.files)

    def __bool__(self):
        return bool(self.files)

    def __getitem__(self, i):
        img = cv2.imread(self.files[i])
        return img if img is not None else np.full((256, 256, 3), 128, np.uint8)

    def __add__(self, other):
        return LazyImages(self.files + other.files)


def load_bg():
    """배경 풀. 6장 중 1장은 val/test 전용.
    photos = 티니핑 배경 사진 + COCO(bg_pool/coco, scripts/dataset/prep-none-pool.py) — none 과 포켓몬 합성 배경 둘 다에 쓴다."""
    out = {"train": collections.defaultdict(list), "eval": collections.defaultdict(list)}
    for kind, dirs in (("photos", ("photos", "coco")), ("chars", ("chars",))):
        files = sorted(f for d in dirs for f in glob.glob(str(paths.BG_POOL / d / "*.jpg")))
        out["train"][kind] = LazyImages(f for i, f in enumerate(files) if i % 6)
        out["eval"][kind] = LazyImages(f for i, f in enumerate(files) if i % 6 == 0)
    for i, f in enumerate(sorted(glob.glob(str(paths.BG_POOL / "teeniping" / "*.png")))):
        im = Image.open(f).convert("RGBA")
        bbox = im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
        out["eval" if i % 6 == 0 else "train"]["teeniping"].append(im.crop(bbox) if bbox else im)
    return out


def init(distract, p):
    global BG, DISTRACT, DISTRACT_P
    BG, DISTRACT, DISTRACT_P = load_bg(), distract, p


# ---------- 계획 ----------
def pick_balanced(rng, recs, n):
    """출처·이미지 종류가 고르게 돌아가게 n 장을 고른다 (모자라면 반복). 뒷모습은 절반 가중치."""
    groups = collections.defaultdict(list)
    for r in recs:   # 폼 이미지는 따로 묶어 기본형에 묻히지 않게 한다 (train_v1 에서 폼 이미지 종 정확도 0.62)
        groups[(r["source_id"], r["image_type"], bool(r.get("form")))].append(r)
    keys = sorted(groups)
    for k in keys:
        rng.shuffle(groups[k])
    out, i, cursor = [], 0, collections.Counter()
    while len(out) < n:
        k = keys[i % len(keys)]
        i += 1
        g = groups[k]
        r = g[cursor[k] % len(g)]
        cursor[k] += 1
        if r["view"] == "back" and rng.random() < 0.5:
            continue
        out.append(r)
    return out


# ---------- ② 폼 모드 ----------
def canon_form(tag, forms):
    """세레비넷 폼 태그 → PokeAPI 폼 이름. 'dusk_mane'→'dusk', 'galar'→'galar_standard'. 못 맞추면 None."""
    if not tag or tag in BASE_TAGS:
        return ""
    if tag in forms:
        return tag
    t = tag
    for pre in ("unovan_", "kantonian_", "johtonian_", "hoennian_", "sinnohan_", "kalosian_"):   # 원래 지방 = 기본형 계열
        if t.startswith(pre):
            t = t[len(pre):]
    t = t.removesuffix("_mode")
    if t in forms:
        return t
    if t == "mega" and "primal" in forms:   # 세레비넷은 그란돈·가이오가 원시회귀를 Mega 로 적는다
        return "primal"
    cands = [f for f in forms if f.startswith(t + "_") or t.startswith(f + "_")]
    if len(cands) > 1:
        cands = [f for f in cands if f.endswith(("_standard", "_normal"))] or cands
    if len(cands) == 1:
        return cands[0]
    # 못 맞춘 태그: 메가·거다이맥스·리전처럼 생김새가 다른 폼이면 빼고(None), 그 밖(꽃 색·타입 색 등 외형 변형)은 기본형으로
    return None if any(w in t for w in ("mega", "gmax", "alola", "galar", "hisui", "paldea", "primal")) else ""


BASE_TAGS = {"normal", "standard", "ordinary", "average", "plant", "male"}


def form_pool(splits):
    """폼 모델 클래스와 pool 기록. 클래스 = include=1 폼(<종>__<폼>) + 그 종의 기본형(<종>).
    포함하지 않는 폼(토템·모자 등)의 이미지는 기본형으로, 폼 이름을 못 맞춘 이미지(아르세우스 타입 등)는 뺀다."""
    with paths.FORM_LIST.open(encoding="utf-8", newline="") as f:
        fl = list(csv.DictReader(f))
    all_forms = collections.defaultdict(set)
    included = collections.defaultdict(set)
    for r in fl:
        all_forms[r["class_id"]].add(r["form"])
        if str(r["include"]) == "1":
            included[r["class_id"]].add(r["form"])
    species = set(included)
    classes = sorted(species | {f"{c}__{f}" for c, fs in included.items() for f in fs})
    pool = collections.defaultdict(lambda: collections.defaultdict(list))
    unmapped = collections.Counter()
    with paths.POOL_MANIFEST.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            c = r["class_id"]
            if c not in species or r["split"] not in splits:
                continue
            form = canon_form(r["form"], all_forms[c])
            if form is None:
                unmapped[f"{c}:{r['form']}"] += 1
                continue
            cls = f"{c}__{form}" if form in included[c] else c
            pool[r["split"]][cls].append(dict(r, species=c))
    # train 이 없는 폼: val/test 에 있으면 train 으로 옮긴다 (폼 아트 1장뿐인 경우)
    moved = []
    for cls in classes:
        if not pool["train"][cls]:
            for sp in ("val", "test"):
                if pool[sp][cls]:
                    pool["train"][cls] += pool[sp].pop(cls)
                    moved.append(cls)
                    break
    empty = [c for c in classes if not pool["train"][c]]
    classes = [c for c in classes if pool["train"][c]]
    print(f"폼 모드: 클래스 {len(classes)} (이미지 없어 뺀 것 {len(empty)}: {empty[:12]}), "
          f"val/test → train 으로 옮긴 폼 {len(moved)}, 폼 이름을 못 맞춘 이미지 {sum(unmapped.values())} {list(unmapped)[:8]}")
    return classes, pool


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", choices=["species", "form"], default="species",
                    help="species = ① 종 모델(1025종 + none), form = ② 폼 모델(data/form-list.csv, none 없음)")
    ap.add_argument("--out", help="기본: species → workspace/dataset_cls, form → workspace/dataset_form")
    ap.add_argument("--size", type=int, default=224)
    ap.add_argument("--train", type=int, default=300, help="클래스당 train 장수 (DESIGN.md 2.4 상한 300)")
    ap.add_argument("--val", type=int, default=30, help="클래스당 val 최대 장수")
    ap.add_argument("--test", type=int, default=50, help="클래스당 test 최대 장수")
    ap.add_argument("--none-train", type=int, default=3000)
    ap.add_argument("--none-val", type=int, default=300)
    ap.add_argument("--none-test", type=int, default=300)
    ap.add_argument("--distractor", type=float, default=0.2, help="train 에서 다른 종을 작게 뒤에 얹는 확률")
    ap.add_argument("--min-train-pool", type=int, default=1, help="pool train 이미지가 이보다 적은 종은 보고만 한다")
    ap.add_argument("--limit-classes", type=int, default=0, help="앞에서 이만큼 종만 (시험)")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    a.out = a.out or str(paths.DATASET_FORM if a.task == "form" else paths.DATASET_CLS)

    if not paths.POOL_MANIFEST.exists():
        sys.exit(f"{paths.POOL_MANIFEST} 가 없다. 먼저 scripts/collect/normalize-sources.py 를 돌린다.")
    if a.task == "form":
        classes, pool = form_pool(("train", "val", "test"))
        a.none_train = a.none_val = a.none_test = 0
    else:
        with paths.CSV_MAIN.open(encoding="utf-8", newline="") as f:
            classes = [r["class_id"] for r in csv.DictReader(f) if r["enabled"] == "1"]
        cset = set(classes)
        pool = collections.defaultdict(lambda: collections.defaultdict(list))
        with paths.POOL_MANIFEST.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if r["class_id"] in cset and r["split"] in ("train", "val", "test"):
                    pool[r["split"]][r["class_id"]].append(r)
    if a.limit_classes:
        classes = classes[:a.limit_classes]

    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)   # 재생성 산출물
    rng = random.Random(a.seed)
    jobs, no_train = [], []
    for c in classes:
        tr = pool["train"][c]
        if len(tr) < a.min_train_pool:
            no_train.append(c)
        if tr:
            jobs += [("train", c, i, r, a.size, str(out), a.seed) for i, r in enumerate(pick_balanced(rng, tr, a.train))]
        for split, cap in (("val", a.val), ("test", a.test)):
            recs = sorted(pool[split][c], key=lambda r: r["file"])
            random.Random(f"{a.seed}-{split}-{c}").shuffle(recs)
            jobs += [(split, c, i, r, a.size, str(out), a.seed) for i, r in enumerate(recs[:cap])]
    for split, n in (("train", a.none_train), ("val", a.none_val), ("test", a.none_test)):
        jobs += [(split, NONE, i, None, a.size, str(out), a.seed) for i in range(n)]

    # 방해물: 투명 배경 train 이미지 중 무작위 3000장 (작업자마다 파일 경로만 들고 있는다)
    alpha_train = [r["file"] for c in classes for r in pool["train"][c] if r["file"].endswith(".png")]
    distract = random.Random(a.seed).sample(alpha_train, min(3000, len(alpha_train)))

    t0 = time.monotonic()
    print(f"클래스 {len(classes)} + none, 작업 {len(jobs):,}장 → {out}", flush=True)
    rows, errors = [], []
    with Pool(a.workers, initializer=init, initargs=(distract, a.distractor)) as p:
        for k, (row, err) in enumerate(p.imap_unordered(make_one, jobs, chunksize=64), 1):
            if row:
                rows.append(row)
            else:
                errors.append(err)
            if k % 50000 == 0:
                print(f"  {k:,}/{len(jobs):,} ({(time.monotonic() - t0) / 60:.1f}분)", flush=True)
    rows.sort(key=lambda r: r["file"])
    # val/test 에 이미지가 없는 종도 빈 폴더를 만든다. ultralytics 가 split 마다 클래스 수를 맞춰 보고
    # "found 908 classes (requires 1026)" 오류를 낸다 (이름으로 다시 맞추긴 하지만, 2026-10-25)
    for split in ("train", "val", "test"):
        for c in classes + ([NONE] if a.task == "species" else []):
            (out / split / c).mkdir(parents=True, exist_ok=True)
    with (out / "manifest.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    cnt = collections.Counter(r["split"] for r in rows)
    per = {s: collections.Counter(r["class_id"] for r in rows if r["split"] == s) for s in ("val", "test")}
    print(f"완료 ({(time.monotonic() - t0) / 60:.1f}분): {dict(cnt)}, 오류 {len(errors)}")
    print(f"  pool train 이미지가 없는 종 {len(no_train)}: {no_train[:20]}")
    for s in ("val", "test"):
        missing = [c for c in classes if per[s][c] == 0]
        print(f"  {s} 이미지가 없는 종 {len(missing)}: {missing[:15]}{' …' if len(missing) > 15 else ''}")
    if errors:
        print(f"  오류 예: {errors[:3]}")


if __name__ == "__main__":
    main()
