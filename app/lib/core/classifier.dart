import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter/services.dart';
import 'package:flutter_onnxruntime/flutter_onnxruntime.dart';
import 'package:path_provider/path_provider.dart';

import 'preprocess.dart';
import 'recognizer.dart';

/// ONNX Runtime 분류기 하나 (① 종 모델, ② 폼 모델 공용).
/// 입력 [1,3,S,S] (0..1 RGB, 정규화는 모델 안에 없다). S 는 모델 메타데이터 imgsz 에서 읽고 없으면 224.
/// 라벨 순서는 라벨 파일 (모델 메타데이터 names 순서와 같아야 한다. 다르면 불러오지 않는다).
class Classifier {
  Classifier._(this._session, this.labels, this.inputSize, this._inputName);
  final OrtSession _session;
  final List<String> labels;
  final int inputSize;
  final String _inputName;

  /// [model] 이 앱에 들어 있지 않으면 null (모델은 선택. 없으면 그 단계 없이 동작한다).
  static Future<Classifier?> load({required String model, required String labelsAsset, required Set<String> assets}) async {
    if (!assets.contains(model)) return null;
    if (!assets.contains(labelsAsset)) throw StateError('$model 은 있는데 $labelsAsset 가 없어요');
    final labels = (await rootBundle.loadString(labelsAsset)).split('\n').map((s) => s.trim()).where((s) => s.isNotEmpty).toList();
    final session = await OnnxRuntime().createSession(await _modelFile(model));
    var size = modelSize;
    try {
      final meta = (await session.getMetadata()).customMetadataMap;
      size = parseImgsz(meta['imgsz']) ?? size;
      final names = parseNames(meta['names']);
      if (names != null && !_sameList(names, labels)) {
        await session.close();
        throw StateError('$labelsAsset 와 모델의 라벨이 달라요 (라벨 ${labels.length}개, 모델 ${names.length}개). '
            'build-app-assets.py 로 에셋을 다시 만드세요');
      }
    } on StateError {
      rethrow;
    } catch (_) {
      // 메타데이터를 못 읽는 플랫폼이면 기본값으로 둔다
    }
    final input = session.inputNames.isNotEmpty ? session.inputNames.first : 'images';
    return Classifier._(session, labels, size, input);
  }

  /// 번들된 모델을 임시 폴더에 꺼내 경로를 돌려준다. 파일 이름에 모델 내용의 해시를 넣는다.
  /// (플러그인의 createSessionFromAsset 은 고정 이름으로 한 번 꺼낸 파일을 앱을 업데이트해도 계속 재사용해서,
  ///  모델을 바꿔 설치해도 옛 모델이 돌았다. 그 때문에 라벨과 출력이 어긋나 엉뚱한 답이 나왔다. 티니핑 앱에서 겪음)
  static Future<String> _modelFile(String asset) async {
    final data = await rootBundle.load(asset);
    final bytes = data.buffer.asUint8List(data.offsetInBytes, data.lengthInBytes);
    var h = 0xcbf29ce484222325; // FNV-1a 64
    for (var i = 0; i < bytes.length; i += 7) {
      h = ((h ^ bytes[i]) * 0x100000001b3) & 0x7fffffffffffffff;
    }
    final stem = asset.split('/').last.replaceAll('.onnx', ''); // species / forms
    final prefix = 'konnadex_${stem}_';
    final dir = await getTemporaryDirectory();
    final file = File('${dir.path}${Platform.pathSeparator}$prefix${bytes.length}_${h.toRadixString(16)}.onnx');
    if (!await file.exists()) {
      for (final f in dir.listSync()) {
        final base = f.path.split(Platform.pathSeparator).last;
        if (f is File && base.startsWith(prefix)) {
          try {
            f.deleteSync();
          } catch (_) {}
        }
      }
      final tmp = File('${file.path}.part');
      await tmp.writeAsBytes(bytes, flush: true);
      await tmp.rename(file.path);
    }
    return file.path;
  }

  /// 확률 벡터 (라벨 순서). 출력이 이미 softmax 가 아니면 softmax 를 씌운다.
  Future<List<double>> probs(Float32List input) async {
    final x = await OrtValue.fromList(input, [1, 3, inputSize, inputSize]);
    final outputs = await _session.run({_inputName: x});
    final raw = await outputs.values.first.asFlattenedList();
    await x.dispose();
    for (final v in outputs.values) {
      await v.dispose();
    }
    final out = [for (final v in raw) (v as num).toDouble()];
    if (out.length != labels.length) {
      throw StateError('모델 출력 ${out.length}개와 라벨 ${labels.length}개가 달라요 (옛 모델/라벨?)');
    }
    return toProbs(out);
  }

  Future<void> close() => _session.close();
}

/// 이미 확률(0..1, 합 1)이면 그대로, 아니면 softmax.
List<double> toProbs(List<double> v) {
  if (v.isEmpty) return v;
  final sum = v.fold(0.0, (a, b) => a + b);
  if (v.every((x) => x >= 0 && x <= 1) && (sum - 1).abs() < 1e-3) return v;
  final m = v.reduce(math.max);
  final e = [for (final x in v) math.exp(x - m)];
  final s = e.fold(0.0, (a, b) => a + b);
  return [for (final x in e) x / s];
}

/// 확률 벡터 → 상위 k 개.
List<Prediction> topK(List<double> probs, List<String> labels, {int k = 3}) {
  final idx = List<int>.generate(probs.length, (i) => i)..sort((a, b) => probs[b].compareTo(probs[a]));
  return [for (final i in idx.take(k)) Prediction(labels[i], probs[i])];
}

/// ② 폼 모델 출력을 ①이 고른 종의 폼만 남겨(마스크) 다시 정규화해 고른다.
/// 폼 라벨: `<종>__<폼>` 과 기본형(종 이름 그대로).
class FormTable {
  FormTable(List<String> labels) {
    for (var i = 0; i < labels.length; i++) {
      final sp = labels[i].split('__').first;
      (_bySpecies[sp] ??= []).add(i);
    }
  }
  final Map<String, List<int>> _bySpecies = {};

  /// 폼 모델을 돌릴 가치가 있는 종인가 (폼 후보가 둘 이상).
  bool hasForms(String species) => (_bySpecies[species]?.length ?? 0) > 1;

  List<int> indices(String species) => _bySpecies[species] ?? const [];

  /// 그 종의 클래스만 남겨 다시 정규화한 상위 [k] 개 (확률 내림차순). 그 종이 없으면 빈 목록.
  List<Prediction> top(String species, List<double> probs, List<String> labels, {int k = 2}) {
    final idx = [...indices(species)];
    if (idx.isEmpty) return const [];
    final s = idx.fold(0.0, (a, i) => a + probs[i]);
    idx.sort((a, b) => probs[b].compareTo(probs[a]));
    return [for (final i in idx.take(k)) Prediction(labels[i], s > 0 ? probs[i] / s : 1.0 / idx.length)];
  }

  /// [probs] 는 폼 모델 전체 출력의 확률. 그 종의 폼이 없으면 null.
  Prediction? pick(String species, List<double> probs, List<String> labels) {
    final idx = indices(species);
    if (idx.isEmpty) return null;
    final s = idx.fold(0.0, (a, i) => a + probs[i]);
    var best = idx.first;
    for (final i in idx) {
      if (probs[i] > probs[best]) best = i;
    }
    return Prediction(labels[best], s > 0 ? probs[best] / s : 1.0 / idx.length);
  }
}

/// Ultralytics 메타데이터 imgsz ("[224, 224]" 또는 "224") → 한 변 크기.
int? parseImgsz(String? s) {
  if (s == null) return null;
  final m = RegExp(r'\d+').firstMatch(s);
  return m == null ? null : int.tryParse(m.group(0)!);
}

/// Ultralytics 메타데이터 names ("{0: 'a', 1: 'b'}") → 인덱스 순서 목록. 못 읽으면 null.
List<String>? parseNames(String? s) {
  if (s == null) return null;
  final ms = RegExp(r"""(\d+)\s*:\s*(?:'([^']*)'|"([^"]*)")""").allMatches(s).toList();
  if (ms.isEmpty) return null;
  final out = List<String?>.filled(ms.length, null);
  for (final m in ms) {
    final i = int.parse(m.group(1)!);
    if (i >= out.length) return null;
    out[i] = m.group(2) ?? m.group(3);
  }
  return out.contains(null) ? null : out.cast<String>();
}

bool _sameList(List<String> a, List<String> b) {
  if (a.length != b.length) return false;
  for (var i = 0; i < a.length; i++) {
    if (a[i] != b[i]) return false;
  }
  return true;
}
