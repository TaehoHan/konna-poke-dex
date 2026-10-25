import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:camera/camera.dart';
import 'package:image/image.dart' as img;
import 'package:path_provider/path_provider.dart';

import 'recognizer.dart';

/// 진단용 실제 촬영 수집. 앱 Documents/captures/<라벨>/<시각>_{crop.png,frame.jpg,json} 에 저장한다.
/// 라벨: "none"(포켓몬 아님) 또는 정답 class_id. Documents 는 파일 앱(나의 iPhone > 포켓몬 도감)과
/// Finder 파일 공유에 보이고, 맥에서는 scripts/eval/import-captures.py 가 workspace/eval_real/<라벨>/ 로 꺼낸다.
class CaptureStore {
  static Future<Directory> root() async {
    final d = Directory('${(await getApplicationDocumentsDirectory()).path}/captures');
    if (!await d.exists()) await d.create(recursive: true);
    return d;
  }

  static Future<int> count() async {
    final r = await root();
    return r.listSync(recursive: true).whereType<File>().where((f) => f.path.endsWith('.json')).length;
  }

  /// [shot] 을 [label] 폴더에 저장한다. 돌려주는 값은 저장한 파일 이름 앞부분.
  static Future<String> save(Shot shot, String label) async {
    final now = shot.time;
    final stamp = now.toIso8601String().replaceAll(':', '').replaceAll('-', '').replaceAll('.', '_');
    final dir = Directory('${(await root()).path}/$label');
    await dir.create(recursive: true);
    final base = '${dir.path}/$stamp';
    await File('${base}_crop.png').writeAsBytes(img.encodePng(shot.crop));
    if (shot.frame != null) await File('${base}_frame.jpg').writeAsBytes(img.encodeJpg(shot.frame!, quality: 92));
    await File('$base.json').writeAsString(const JsonEncoder.withIndent(' ').convert({
      'label': label,
      'time': now.toIso8601String(),
      'top': [for (final p in shot.top) {'id': p.id, 'p': p.confidence}],
      ...shot.meta,
    }));
    return base;
  }
}

/// 카메라 프레임 한 장의 진단 기록 (프레임 버퍼는 콜백이 끝나면 재사용되니 그 안에서 이미지로 바꿔 둔다).
class Shot {
  Shot({required this.crop, required this.frame, required this.top, required this.meta}) : time = DateTime.now();
  final img.Image crop; // 모델 입력 그대로 (size x size)
  final img.Image? frame; // 원본 프레임 (똑바로 세운 것)
  final List<Prediction> top;
  final Map<String, Object?> meta;
  final DateTime time;

  static Shot from(CameraImage f, int rotation, Float32List input, int size, List<Prediction> top, Map<String, Object?> meta) =>
      Shot(crop: tensorToImage(input, size), frame: frameToImage(f, rotation), top: top, meta: {
        'frame': '${f.width}x${f.height} ${f.format.group.name}',
        'rotation': rotation,
        'input_size': size,
        ...meta,
      });
}

/// NCHW 0..1 → RGB 이미지.
img.Image tensorToImage(Float32List t, int size) {
  final out = img.Image(width: size, height: size);
  final n = size * size;
  for (var i = 0; i < n; i++) {
    out.setPixelRgb(i % size, i ~/ size, (t[i] * 255).round(), (t[n + i] * 255).round(), (t[2 * n + i] * 255).round());
  }
  return out;
}

/// 카메라 프레임 → 똑바로 선 RGB 이미지 (iOS BGRA, Android YUV420). 모르는 형식이면 null.
img.Image? frameToImage(CameraImage f, int rotation) {
  final w = f.width, h = f.height;
  img.Image out;
  if (f.format.group == ImageFormatGroup.bgra8888) {
    final p = f.planes[0];
    out = img.Image.fromBytes(width: w, height: h, bytes: p.bytes.buffer, rowStride: p.bytesPerRow, order: img.ChannelOrder.bgra, numChannels: 4);
  } else if (f.format.group == ImageFormatGroup.yuv420 && f.planes.length >= 3) {
    out = img.Image(width: w, height: h);
    final y = f.planes[0], u = f.planes[1], v = f.planes[2];
    final up = u.bytesPerPixel ?? 1;
    for (var py = 0; py < h; py++) {
      for (var px = 0; px < w; px++) {
        final yy = y.bytes[py * y.bytesPerRow + px].toDouble();
        final uo = (py >> 1) * u.bytesPerRow + (px >> 1) * up;
        final uu = u.bytes[uo] - 128.0, vv = v.bytes[uo] - 128.0;
        out.setPixelRgb(px, py, (yy + 1.402 * vv).clamp(0, 255).round(), (yy - 0.344136 * uu - 0.714136 * vv).clamp(0, 255).round(),
            (yy + 1.772 * uu).clamp(0, 255).round());
      }
    }
  } else {
    return null;
  }
  return rotation == 0 ? out : img.copyRotate(out, angle: rotation);
}
