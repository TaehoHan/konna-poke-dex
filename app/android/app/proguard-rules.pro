# ONNX Runtime 의 JNI 가 클래스·메서드를 이름으로 찾는다. R8 이 지우면 OrtSession.run 에서 abort 한다.
-keep class ai.onnxruntime.** { *; }
