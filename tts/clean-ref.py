"""레퍼런스 음성(tts/ref/narr.wav)을 클로닝에 쓰기 좋게 정리한 사본(tts/ref/narr_clean.wav)을 만든다. 원본은 건드리지 않는다.

왜: 영상에서 가져온 레퍼런스는 앞 0.25초가 음성이 아닌 광대역 잡음이고 배경 잡음도 있다. 클로닝 모델이 이 잡음을 흉내 내서
    생성 음성 앞에 쉬익 소리가 붙었다. 앞부분을 자르고 잡음을 줄인 레퍼런스로 만든 음성(F 버전)이 비교 청취에서 가장 나았다.
    (비교: A 원본 / B 잡음만 줄임 / F 잡음 줄임 + 앞 0.27초 자름)
방법: highpass 80Hz -> afftdn(nr=20, 잡음 추적) -> lowpass 10kHz -> (잡음을 학습시킨 뒤) 앞 --trim-start 초 자름. 24kHz 모노.
레퍼런스 문장(tts/ref/narr.txt)은 잘라낸 구간에 말이 없어서 그대로 쓴다. 다른 레퍼런스를 쓰면 앞 구간을 스펙트로그램으로 확인하고 --trim-start 를 조정한다.
사용: python tts/clean-ref.py [--src tts/ref/narr.wav] [--out tts/ref/narr_clean.wav] [--trim-start 0.27]
"""
import argparse
import subprocess

ap = argparse.ArgumentParser()
ap.add_argument("--src", default="tts/ref/narr.wav")
ap.add_argument("--out", default="tts/ref/narr_clean.wav")
ap.add_argument("--trim-start", type=float, default=0.27)
a = ap.parse_args()
subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", a.src, "-af",
                f"highpass=f=80,afftdn=nr=20:nf=-40:tn=1,lowpass=f=10000,atrim=start={a.trim_start},asetpts=PTS-STARTPTS",
                "-ar", "24000", "-ac", "1", a.out], check=True)
print("만듦:", a.out)
