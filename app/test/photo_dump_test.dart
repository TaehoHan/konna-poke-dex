import 'dart:io';
import 'dart:typed_data';

import 'package:camera/camera.dart';
import 'package:camera_platform_interface/camera_platform_interface.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:konnadex/core/preprocess.dart';

/// 실제 사진을 앱의 전처리(preprocess)에 통과시켜 결과 텐서를 파일로 남긴다 (파이썬 ONNX 와 맞춰 보는 용도).
/// 환경변수 PHOTO(입력 PNG), OUT(출력 f32 파일)이 없으면 건너뛴다.
void main() {
  final src = Platform.environment['PHOTO'];
  final out = Platform.environment['OUT'];
  test('실제 사진 전처리 덤프', () {
    final im = img.decodeImage(File(src!).readAsBytesSync())!;
    final w = im.width, h = im.height;
    final bytes = Uint8List(w * h * 4);
    for (var y = 0; y < h; y++) {
      for (var x = 0; x < w; x++) {
        final p = im.getPixel(x, y);
        final o = (y * w + x) * 4;
        bytes[o] = p.b.toInt();
        bytes[o + 1] = p.g.toInt();
        bytes[o + 2] = p.r.toInt();
        bytes[o + 3] = 255;
      }
    }
    final cam = CameraImage.fromPlatformInterface(CameraImageData(
      format: const CameraImageFormat(ImageFormatGroup.bgra8888, raw: 0),
      planes: [CameraImagePlane(bytes: bytes, bytesPerRow: w * 4, bytesPerPixel: 4)],
      width: w,
      height: h,
    ));
    for (final m in CropMode.values) {
      final t = preprocess(cam, rotation: 0, mode: m);
      File('$out.${m.name}.f32').writeAsBytesSync(t.buffer.asUint8List());
    }
  }, skip: src == null || out == null);
}
