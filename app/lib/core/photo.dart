import 'dart:math' as math;
import 'dart:typed_data';

import 'package:image/image.dart' as img;

import 'preprocess.dart';
import 'recognizer.dart';

/// 정지 사진 한 장을 모델 입력들로 만드는 일 (별도 isolate 에서 돌린다: 12MP JPEG 디코딩이 무겁다).
class PhotoJob {
  const PhotoJob(this.bytes, this.size, {this.maxShort = 3072, this.focusX, this.focusY, this.zoom = 1});
  final Uint8List bytes; // 촬영한 JPEG (EXIF 방향 포함)
  final int size; // 모델 입력 한 변
  final int maxShort; // 짧은 변을 이 크기로 줄여서 쓴다 (3072: 12MP 사진(3024)까지는 그대로, 그보다 큰 사진(48MP 등)만 줄여 디코딩·메모리를 아낀다)
  final double zoom; // 사진을 이만큼 확대해서 보다가 탭했다: 클수록 판별 영역이 작아진다 (prepFocusOnly)
  final double? focusX, focusY; // 사용자가 탭한 영역의 가운데 (사진 안 정규화 좌표). 있으면 그 둘레 타일을 앞에 둔다
}

class PhotoPrep {
  const PhotoPrep(this.preview, this.width, this.height, this.tiles, this.inputs, {this.focusIdx = const [], this.srcWidth = 0, this.srcHeight = 0});
  final int srcWidth, srcHeight; // 카메라가 준 원본 사진 크기 (똑바로 세운 뒤, 줄이기 전)
  final List<int> focusIdx; // tiles 중 선택한 영역 둘레 타일의 인덱스 (없으면 빈 목록)
  final Uint8List preview; // 화면에 보여 줄 JPEG (똑바로 세운 것)
  final int width, height; // preview 크기
  final List<Tile> tiles; // 첫째는 가운데 정사각형, 나머지는 겹치는 타일 (크게 → 작게)
  final List<Float32List> inputs; // tiles 마다의 모델 입력 (NCHW 0..1, size x size)
}

/// 이미 똑바로 세운 사진(미리보기 JPEG)에서 탭한 지점 둘레 타일들만 입력으로 만든다 (결과 화면에서 영역을 다시 고를 때).
/// [PhotoJob.focusX]/[focusY] 필수. 돌려주는 PhotoPrep.tiles 는 큰 것 → 작은 것 선택 영역 타일들이다.
PhotoPrep prepFocusOnly(PhotoJob j) {
  final im = img.decodeImage(j.bytes);
  if (im == null) throw StateError('사진을 읽지 못했어요');
  final tiles = focusTiles(im.width, im.height, j.focusX!, j.focusY!, ratios: zoomRatios(math.min(im.width, im.height), j.zoom));
  final inputs = [for (final t in tiles) imageToTensor(img.copyCrop(im, x: t.x, y: t.y, width: t.side, height: t.side), j.size)];
  return PhotoPrep(Uint8List(0), im.width, im.height, tiles, inputs, focusIdx: [for (var i = 0; i < tiles.length; i++) i]);
}

/// 사진 → 가운데 + 타일(짧은 변의 70%, 50%) 입력들. 톱레벨 함수라 compute() 에 넘길 수 있다.
PhotoPrep prepPhoto(PhotoJob j) {
  var im = img.decodeImage(j.bytes);
  if (im == null) throw StateError('사진을 읽지 못했어요');
  im = img.bakeOrientation(im);
  final srcW = im.width, srcH = im.height;
  final short = math.min(im.width, im.height);
  if (short > j.maxShort) {
    im = im.width < im.height
        ? img.copyResize(im, width: j.maxShort, interpolation: img.Interpolation.average)
        : img.copyResize(im, height: j.maxShort, interpolation: img.Interpolation.average);
  }
  final base = [...frameTiles(im.width, im.height, ratio: 0.7), ...frameTiles(im.width, im.height, ratio: 0.5).skip(1)];
  final focus = (j.focusX != null && j.focusY != null) ? focusTiles(im.width, im.height, j.focusX!, j.focusY!) : <Tile>[];
  final tiles = [base.first, ...focus, ...base.skip(1)]; // 가운데, 선택 영역 둘레, 나머지
  final inputs = [for (final t in tiles) imageToTensor(img.copyCrop(im, x: t.x, y: t.y, width: t.side, height: t.side), j.size)];
  return PhotoPrep(Uint8List.fromList(img.encodeJpg(im, quality: 88)), im.width, im.height, tiles, inputs,
      focusIdx: [for (var i = 0; i < focus.length; i++) 1 + i], srcWidth: srcW, srcHeight: srcH);
}

/// 정사각형 이미지 → 모델 입력 (size 로 줄여 NCHW 0..1).
Float32List imageToTensor(img.Image sq, int size) {
  final r = sq.width == size && sq.height == size
      ? sq
      : img.copyResize(sq, width: size, height: size, interpolation: img.Interpolation.average);
  final n = size * size;
  final out = Float32List(3 * n);
  for (var y = 0; y < size; y++) {
    for (var x = 0; x < size; x++) {
      final p = r.getPixel(x, y);
      final i = y * size + x;
      out[i] = p.r / 255.0;
      out[n + i] = p.g / 255.0;
      out[2 * n + i] = p.b / 255.0;
    }
  }
  return out;
}

/// 한 종에 대한 사진 판정: 영역들 중 가장 높았던 원시 확률과 그 영역.
class Candidate {
  const Candidate(this.id, this.prob, this.tile, this.noneProb);
  final String id;
  final double prob; // 모델 원시 확률 (softmax 그대로, "포켓몬 아님"을 빼고 다시 나누지 않는다)
  final int tile; // 가장 높게 나온 영역 번호 (PhotoPrep.tiles 의 인덱스)
  final double noneProb; // 그 영역에서의 "포켓몬 아님" 확률
}

/// 영역마다의 확률 벡터에서 종별 최고 확률을 모아 내림차순으로. "포켓몬 아님"(none)은 뺀다.
/// none 인덱스는 라벨에서 찾고 가정하지 않는다.
/// [only] 가 있으면 그 영역들만 쓴다 (Candidate.tile 은 항상 tileProbs 기준 인덱스).
List<Candidate> bestPerSpecies(List<List<double>> tileProbs, List<String> labels, {List<int>? only}) {
  final noneIdx = labels.indexOf(noneLabel);
  final out = <Candidate>[];
  final use = only ?? List<int>.generate(tileProbs.length, (i) => i);
  for (var i = 0; i < labels.length; i++) {
    if (i == noneIdx) continue;
    var best = -1.0, bt = use.first;
    for (final t in use) {
      if (tileProbs[t][i] > best) {
        best = tileProbs[t][i];
        bt = t;
      }
    }
    out.add(Candidate(labels[i], best, bt, noneIdx >= 0 ? tileProbs[bt][noneIdx] : 0));
  }
  out.sort((a, b) => b.prob.compareTo(a.prob));
  return out;
}

/// 한 기준(전체 또는 선택한 영역)으로 본 사진 판별.
class PhotoView {
  const PhotoView({required this.top3, required this.judged, required this.forms, required this.tiles, this.region});
  final List<Tile> tiles; // Candidate.tile 이 가리키는 영역 목록
  final Tile? region; // 선택한 영역(가장 큰 타일). 사진 전체 기준이면 null
  final List<Candidate> top3; // "포켓몬 아님"을 뺀 가장 가까운 3종
  final List<Candidate> judged; // 기준 이상으로 판별된 종 (여러 마리면 둘 이상, 최대 5)
  final Map<String, Prediction> forms; // 판별된 종의 ② 폼 판정 (그 종 안에서 다시 정규화한 확률)
}

/// 사진 판별 결과. [focus] 가 있으면 사용자가 탭한 영역 둘레만으로 본 결과 (없으면 null), [all] 은 사진 전체.
class PhotoResult {
  const PhotoResult({
    required this.preview,
    required this.width,
    required this.height,
    required this.tiles,
    required this.all,
    required this.focus,
    required this.ms,
    this.srcWidth = 0,
    this.srcHeight = 0,
  });
  final Uint8List preview;
  final int width, height;
  final List<Tile> tiles;
  final PhotoView all;
  final PhotoView? focus;
  final int srcWidth, srcHeight; // 카메라가 준 원본 사진 크기 (width/height 는 판별·확대에 쓰는 크기)
  final int ms;
}

/// 확대 배율 [zoom] 에서의 선택 영역 크기들 (짧은 변 대비): 기본 55·40·28% 를 배율로 나누되 너무 작아지지 않게 [minSide] 픽셀 아래로는 내려가지 않는다.
List<double> zoomRatios(int short, double zoom, {int minSide = 64}) {
  const base = [0.55, 0.4, 0.28];
  final z = zoom < 1 ? 1.0 : zoom;
  final floor = minSide / short;
  return [for (final r in base) math.max(r / z, floor).clamp(0.0, 1.0)];
}

