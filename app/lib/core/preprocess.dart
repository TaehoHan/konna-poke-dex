import 'dart:typed_data';

import 'package:camera/camera.dart';

/// 모델 입력 한 변 기본값. 실제 값은 모델 메타데이터 imgsz 에서 읽는다 (Classifier.inputSize).
const int modelSize = 224;

/// 모델 입력(정사각형)에 맞추는 방법. iOS 샘플 앱, eval-real.py(stretch)와 같은 의미다 (티니핑 앱에서 가져왔다).
enum CropMode {
  centerCrop('가운데 자르기'), // 화면의 네모 가이드와 같은 영역
  stretch('늘리기'); // 전체 프레임을 224x224 로 늘림

  const CropMode(this.label);
  final String label;
}

/// 카메라 프레임 -> Float32 NCHW RGB (0..1, 1x3xSxS, S=[size]). 정규화는 모델 안에 없고 학습도 0..1 이다.
/// [rotation]: 프레임을 똑바로 세우려고 시계 방향으로 돌려야 하는 각도 (0/90/180/270).
/// 세로 모드 후면 카메라는 센서 방향 값(iPhone 90)과 같다.
/// 줄일 때 계단이 생기지 않도록 출력 픽셀마다 3x3 점을 평균한다.
///
/// [tile] 이 있으면 [mode] 대신 그 영역(똑바로 세운 프레임 기준 픽셀 좌표)을 잘라 넣는다 (여러 마리 찾기).
/// [samples] 는 출력 픽셀마다 평균내는 점의 한 변 개수 (기본 3 = 3x3, 타일은 속도 때문에 2).
Float32List preprocess(CameraImage img, {required int rotation, required CropMode mode, int size = modelSize, Tile? tile, int samples = 3}) {
  final w = img.width, h = img.height;
  // 영역(rx0, ry0, rw, rh)은 원본 버퍼 좌표.
  late final int rx0, ry0, rw, rh;
  if (tile != null) {
    final r = tile.toRaw(w, h, rotation);
    rx0 = r.x;
    ry0 = r.y;
    rw = r.w;
    rh = r.h;
  } else if (mode == CropMode.centerCrop) {
    final s = w < h ? w : h;
    rw = rh = s;
    rx0 = (w - s) ~/ 2;
    ry0 = (h - s) ~/ 2;
  } else {
    rx0 = ry0 = 0;
    rw = w;
    rh = h;
  }
  final swap = rotation == 90 || rotation == 270;
  final uw = (swap ? rh : rw).toDouble();
  final uh = (swap ? rw : rh).toDouble();

  final plane0 = img.planes[0];
  final bgra = img.format.group == ImageFormatGroup.bgra8888;
  final yBytes = plane0.bytes;
  final yStride = plane0.bytesPerRow;
  Uint8List? uBytes, vBytes;
  int uStride = 0, uPix = 1;
  if (!bgra) {
    uBytes = img.planes[1].bytes;
    vBytes = img.planes[2].bytes;
    uStride = img.planes[1].bytesPerRow;
    uPix = img.planes[1].bytesPerPixel ?? 1;
  }

  final out = Float32List(3 * size * size);
  final plane = size * size;
  final n = samples;
  final inv = 1.0 / (n * n * 255.0);

  for (var oy = 0; oy < size; oy++) {
    for (var ox = 0; ox < size; ox++) {
      var r = 0, g = 0, b = 0;
      for (var j = 0; j < n; j++) {
        final uy = (oy + (j + 0.5) / n) / size * uh;
        for (var i = 0; i < n; i++) {
          final ux = (ox + (i + 0.5) / n) / size * uw;
          double sx, sy;
          switch (rotation) {
            case 90:
              sx = uy;
              sy = rh - ux;
            case 180:
              sx = rw - ux;
              sy = rh - uy;
            case 270:
              sx = rw - uy;
              sy = ux;
            default:
              sx = ux;
              sy = uy;
          }
          var px = rx0 + sx.floor();
          var py = ry0 + sy.floor();
          px = px.clamp(0, w - 1);
          py = py.clamp(0, h - 1);
          if (bgra) {
            final o = py * yStride + px * 4;
            b += yBytes[o];
            g += yBytes[o + 1];
            r += yBytes[o + 2];
          } else {
            final y = yBytes[py * yStride + px].toDouble();
            final uo = (py >> 1) * uStride + (px >> 1) * uPix;
            final u = uBytes![uo] - 128.0;
            final v = vBytes![uo] - 128.0;
            r += _clamp(y + 1.402 * v);
            g += _clamp(y - 0.344136 * u - 0.714136 * v);
            b += _clamp(y + 1.772 * u);
          }
        }
      }
      final p = oy * size + ox;
      out[p] = r * inv;
      out[plane + p] = g * inv;
      out[2 * plane + p] = b * inv;
    }
  }
  return out;
}

int _clamp(double v) => v < 0 ? 0 : (v > 255 ? 255 : v.round());

/// 똑바로 세운 프레임 안의 정사각형 영역 (픽셀). 여러 마리 찾기에서 화면을 나눠 분류할 때 쓴다.
class Tile {
  const Tile(this.x, this.y, this.side);
  final int x, y, side;

  /// 원본(센서 방향) 버퍼 좌표의 영역. [rotation] 은 프레임을 똑바로 세우려고 시계 방향으로 돌리는 각도,
  /// [rawW]x[rawH] 는 원본 버퍼 크기. preprocess 의 회전 규칙의 역변환이다.
  ({int x, int y, int w, int h}) toRaw(int rawW, int rawH, int rotation) {
    switch (rotation) {
      case 90:
        return (x: y, y: rawH - (x + side), w: side, h: side);
      case 180:
        return (x: rawW - (x + side), y: rawH - (y + side), w: side, h: side);
      case 270:
        return (x: rawW - (y + side), y: x, w: side, h: side);
      default:
        return (x: x, y: y, w: side, h: side);
    }
  }
}

/// 똑바로 세운 [uw]x[uh] 프레임을 덮는 분류 영역들: 가운데 정사각형(기존 방식) 하나 + 짧은 변의 [ratio] 크기 타일을 겹치게 깐 것.
/// 가운데가 첫째다 (진단·수집·폼 판정의 기준).
List<Tile> frameTiles(int uw, int uh, {double ratio = 0.7}) {
  final short = uw < uh ? uw : uh;
  final c = Tile((uw - short) ~/ 2, (uh - short) ~/ 2, short);
  final side = (short * ratio).round();
  List<int> pos(int len) {
    final n = ((len - side) / side).ceil() + 1; // 겹치게: 한 변 길이만큼 이하의 간격
    if (n <= 1) return [(len - side) ~/ 2];
    return [for (var i = 0; i < n; i++) ((len - side) * i / (n - 1)).round()];
  }
  return [c, for (final y in pos(uh)) for (final x in pos(uw)) Tile(x, y, side)];
}

/// 사용자가 탭한 지점 (똑바로 세운 [uw]x[uh] 프레임 안의 정규화 좌표 [nx],[ny] = 0..1) 을 가운데로 하는 정사각형들.
/// 짧은 변의 [ratios] 크기 (큰 것 → 작은 것). 프레임 밖으로 나가면 안쪽으로 밀어 넣는다.
List<Tile> focusTiles(int uw, int uh, double nx, double ny, {List<double> ratios = const [0.55, 0.4, 0.28]}) {
  final short = uw < uh ? uw : uh;
  final cx = nx.clamp(0.0, 1.0) * uw, cy = ny.clamp(0.0, 1.0) * uh;
  return [
    for (final r in ratios)
      () {
        final side = (short * r).round();
        final x = (cx - side / 2).round().clamp(0, uw - side);
        final y = (cy - side / 2).round().clamp(0, uh - side);
        return Tile(x, y, side);
      }(),
  ];
}

