"""학습 때 보지 않은 변형(작게, 큰 회전, 가림, 저해상도, 색조, 흑백)과 음성 입력으로 모델의 견고함을 점검한다.

합성 데이터 기반이라 실제 성능은 아니지만, 어떤 변형에 약한지 찾는 용도다 (실제 성능은 eval-real.py).
모델의 클래스는 ONNX 메타데이터에서 읽는다. 사용: python scripts/eval/verify-stress.py [onnx 경로]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # scripts/ (paths.py)
import paths  # noqa: E402
import ast, csv, importlib.util, os, random, sys
import cv2, numpy as np, onnxruntime as ort
from PIL import Image
spec=importlib.util.spec_from_file_location("md",str(paths.MAKE_DATASET)); md=importlib.util.module_from_spec(spec); spec.loader.exec_module(md)
ONNX=sys.argv[1] if len(sys.argv)>1 else str(paths.BEST_ONNX)   # 인자로 다른 모델 지정 가능
s=ort.InferenceSession(ONNX,providers=["CPUExecutionProvider"]); inp=s.get_inputs()[0].name
names=ast.literal_eval(s.get_modelmeta().custom_metadata_map["names"]); names=[names[i] for i in sorted(names)]
GROUPS=[{"coffeeping","muffingping"},{"noraping","noriping"}]
names_cls=[n for n in names if n!="none"]
md.init(names_cls)   # 캐릭터 클래스만 (none 은 스프라이트가 없다)
def prob(img):
    x=cv2.cvtColor(img,cv2.COLOR_BGR2RGB).astype(np.float32)/255
    return s.run(None,{inp:x.transpose(2,0,1)[None]})[0][0]
def same(a,b): return a==b or any(a in g and b in g for g in GROUPS)
def solid(c): return np.full((224,224,3),c,np.uint8)
def occlude(sp,rng):
    a=np.array(sp); h,w=a.shape[:2]; side=rng.choice(["l","r","t","b"]); f=0.4
    if side=="l": a[:,:int(w*f),3]=0
    if side=="r": a[:,int(w*(1-f)):,3]=0
    if side=="t": a[:int(h*f),:,3]=0
    if side=="b": a[int(h*(1-f)):,:,3]=0
    return Image.fromarray(a)
def hue(img,deg):
    h=cv2.cvtColor(img,cv2.COLOR_BGR2HSV); h[...,0]=(h[...,0].astype(int)+deg//2)%180; return cv2.cvtColor(h,cv2.COLOR_HSV2BGR)
def make(kind,c,i):
    rng=random.Random(f"stress-{kind}-{c}-{i}"); sp=md.SPRITES[c]
    if kind=="깨끗(흰배경,증강없음)": cv=solid((255,255,255)); md.paste(cv,sp,rng,0.9,0,0,False); return cv
    if kind=="깨끗(회색배경)": cv=solid((128,128,128)); md.paste(cv,sp,rng,0.9,0,0,False); return cv
    if kind=="작게(25~40%)": cv=md.random_background(rng,224); md.paste(cv,sp,rng,rng.uniform(.25,.4),rng.uniform(-10,10),0,False); return cv
    if kind=="큰 회전(±60~90°)": cv=md.random_background(rng,224); md.paste(cv,sp,rng,0.7,rng.choice([-1,1])*rng.uniform(60,90),0,False); return cv
    if kind=="40% 가려짐": cv=md.random_background(rng,224); md.paste(cv,occlude(sp,rng),rng,0.8,0,0,False); return cv
    if kind=="강한 저해상도+블러": cv=md.random_background(rng,224); md.paste(cv,sp,rng,0.8,0,0,False); cv=cv2.resize(cv2.resize(cv,(40,40),interpolation=cv2.INTER_AREA),(224,224)); return cv2.GaussianBlur(cv,(9,9),0)
    if kind=="색조 ±30° 변화": cv=md.random_background(rng,224); md.paste(cv,sp,rng,0.8,0,0,False); return hue(cv,rng.choice([-30,30]))
    if kind=="흑백": cv=md.random_background(rng,224); md.paste(cv,sp,rng,0.8,0,0,False); g=cv2.cvtColor(cv,cv2.COLOR_BGR2GRAY); return cv2.cvtColor(g,cv2.COLOR_GRAY2BGR)
    if kind=="학습 분포(대조군)": cv=md.random_background(rng,224); md.paste(cv,sp,rng,rng.uniform(.55,.98),rng.uniform(-25,25),0.05,rng.random()<.5); return md.augment(cv,rng)
kinds=["학습 분포(대조군)","깨끗(흰배경,증강없음)","깨끗(회색배경)","작게(25~40%)","큰 회전(±60~90°)","40% 가려짐","강한 저해상도+블러","색조 ±30° 변화","흑백"]
N=3
print(f"{'조건':<22}{'top1':>7}{'보정top1':>9}{'정답 신뢰도':>11}")
fail_by={}
for k in kinds:
    ok=adj=0; confs=[]; n=0
    for c in names_cls:
        for i in range(N):
            p=prob(make(k,c,i)); t=names[int(p.argmax())]; n+=1
            ok+=t==c; adj+=same(t,c); confs.append(p.max())
            if not same(t,c): fail_by.setdefault(k,[]).append((c,t))
    print(f"{k:<22}{ok/n:7.3f}{adj/n:9.3f}{np.mean(confs):11.3f}  (n={n})")
for k in ["작게(25~40%)","큰 회전(±60~90°)","40% 가려짐","흑백"]:
    f=fail_by.get(k,[]); 
    from collections import Counter
    print(f"\n[{k}] 틀린 클래스 {len(f)}건, 상위:",Counter(c for c,_ in f).most_common(6))
# 음성: 배경만 / 노이즈 / 다른 사물 느낌(체크무늬, 줄무늬)
rng=random.Random(7); mx=[]
for i in range(300):
    r=i%3
    if r==0: img=md.augment(md.random_background(rng,224),rng)
    elif r==1: img=np.random.RandomState(i).randint(0,255,(224,224,3)).astype(np.uint8)
    else:
        img=solid((200,200,200)); [cv2.rectangle(img,(x,0),(x+14,224),(rng.randint(0,255),rng.randint(0,255),rng.randint(0,255)),-1) for x in range(0,224,28)]
    pr=prob(img); mx.append(0.0 if names[int(pr.argmax())]=="none" else pr.max())
mx=np.array(mx); print(f"\n음성(배경/노이즈/줄무늬 300장) 최대 신뢰도: 평균 {mx.mean():.3f}, 95분위 {np.percentile(mx,95):.3f}, 최대 {mx.max():.3f}, 0.5 이상 {np.mean(mx>=.5):.3f}")
