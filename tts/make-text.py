"""도감 DB(data/pokedex-ko.json) -> 안내 음성 문장 data/tts_text.json (종당 1개, 2026-10-25 사용자 결정).

형식: "<이름>. <분류>. <대표 도감 설명>"  (레퍼런스 narr 의 낭독 형식과 같다: "터검니. 이빨포켓몬. …")
대표 설명: 공식 한국어 설명 중 가장 최근 게임 것 (소드·실드 > 울트라썬·문 > 썬·문 > 레츠고 > ORAS > X·Y).
           공식 한국어가 없으면(스칼렛·바이올렛·레전드 아르세우스 전용 127종) 그 게임 영어 설명의 번역본.
           1~5세대 번역본(세대별 설명)은 음성에 쓰지 않는다 (글로만 제공).
출력: [{"class_id", "name", "text", "versions", "translated"}]  — qwen3_synth.py / check-audio.py / build-audio.py 가 읽는다.
도감 설명 원문이 들어 있어 git 제외, 배포 금지.

사용: .venv/bin/python tts/make-text.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))   # scripts/ (paths.py)
import paths  # noqa: E402

PREFER = ["sword", "shield", "ultra-sun", "ultra-moon", "sun", "moon", "lets-go-pikachu", "lets-go-eevee",
          "omega-ruby", "alpha-sapphire", "x", "y", "scarlet", "violet", "legends-arceus"]


def rank(fl):
    return min((PREFER.index(v) for v in fl["versions"] if v in PREFER), default=len(PREFER))


def main():
    db = json.loads(paths.POKEDEX_KO.read_text(encoding="utf-8"))
    items, no_desc = [], []
    for cid, d in sorted(db.items(), key=lambda kv: kv[1]["dex_no"]):
        base = [f for f in d["flavor_ko"] if not f.get("form")]   # 폼(메가·리전 등) 설명은 음성에 쓰지 않는다
        official = [f for f in base if not f.get("translated")]
        recent_tr = [f for f in base if f.get("translated") and rank(f) < len(PREFER)]   # SV·레전드 번역본
        pick = min(official, key=rank) if official else (min(recent_tr, key=rank) if recent_tr else None)
        text = f"{d['name_ko']}. {d['genus_ko']}."
        if pick:
            text += " " + pick["text"].strip()
        else:
            no_desc.append(cid)
        items.append({"class_id": cid, "name": d["name_ko"], "text": text,
                      "versions": pick["versions"] if pick else [], "translated": bool(pick and pick.get("translated"))})
    paths.TTS_TEXT.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    n_tr = sum(i["translated"] for i in items)
    lens = sorted(len(i["text"]) for i in items)
    print(f"{paths.TTS_TEXT.relative_to(paths.ROOT)}: {len(items)}문장 (번역 설명 {n_tr}, 설명 없음 {len(no_desc)}), "
          f"글자 수 중앙 {lens[len(lens) // 2]} 최대 {lens[-1]}")


if __name__ == "__main__":
    main()
