/// 배경·포켓몬 아님 클래스의 라벨 이름. 인덱스는 라벨 파일에서 찾고 가정하지 않는다.
const noneLabel = 'none';

class Prediction {
  const Prediction(this.id, this.confidence);
  final String id; // class_id (폼 모델이면 폼 라벨), 배경/모름은 noneLabel
  final double confidence;
  bool get isNone => id == noneLabel;
}

enum RecState { searching, unknown, candidate, recognized }

class Recognition {
  const Recognition(this.state, [this.prediction, this.all = const []]);
  final RecState state;
  final Prediction? prediction; // 인식된 것 중 확신도가 가장 높은 종
  final List<Prediction> all; // 인식된 모든 종 (확신도 내림차순). 여러 마리면 둘 이상
}

/// 프레임(한 장을 여러 영역으로 나눠 분류한 결과)에서 "인식"된 종을 가려낸다. 인식 조건 (2026-10-25):
///  (a) 어떤 영역의 top-1 원시 확률 >= [threshold]  (b) top-1 이 none 이 아님  (c) 같은 종이 연속 [required] 프레임
///  (d) 그 연속이 [minDuration] 이상 이어짐 (2026-11-21: 폰이 빠르면 5프레임이 0.2초 남짓이라 카드가 너무 빨리 떴다).
/// 종마다 따로 세므로 여러 마리가 동시에 인식된다. 확신도는 그 연속 프레임들의 원시 확률(그 프레임 영역 중 최고) 평균.
class Recognizer {
  double threshold = 0.7; // AppModel.threshold 가 덮어쓴다 (임시값, 실제 촬영으로 다시 정한다)
  int required = 5; // 같은 종이 이만큼 연속으로 나와야 인식
  Duration minDuration = const Duration(milliseconds: 800); // 연속이 이만큼 이어져야 인식 (기기 속도와 무관하게)

  final Map<String, List<double>> _runs = {}; // 종 -> 최근 연속 프레임의 확률
  final Map<String, DateTime> _since = {}; // 종 -> 연속이 시작된 시각

  void reset() {
    _runs.clear();
    _since.clear();
  }

  /// [tiles]: 이번 프레임에서 영역마다의 상위 후보 (각 목록의 첫째가 top-1). [now] 는 테스트용.
  Recognition update(List<List<Prediction>> tiles, {DateTime? now}) {
    final t0 = now ?? DateTime.now();
    final best = <String, double>{};
    var any = false;
    for (final t in tiles) {
      if (t.isEmpty) continue;
      any = true;
      final p = t.first;
      if (!p.isNone && p.confidence >= threshold && p.confidence > (best[p.id] ?? 0)) best[p.id] = p.confidence;
    }
    _runs.removeWhere((id, _) => !best.containsKey(id)); // 한 프레임이라도 빠지면 처음부터
    _since.removeWhere((id, _) => !best.containsKey(id));
    for (final e in best.entries) {
      _since.putIfAbsent(e.key, () => t0);
      final r = _runs.putIfAbsent(e.key, () => []);
      r.add(e.value);
      while (r.length > required) {
        r.removeAt(0);
      }
    }
    final done = [
      for (final e in _runs.entries)
        if (e.value.length >= required && t0.difference(_since[e.key]!) >= minDuration)
          Prediction(e.key, e.value.fold(0.0, (a, b) => a + b) / e.value.length),
    ]..sort((a, b) => b.confidence.compareTo(a.confidence));
    if (done.isNotEmpty) return Recognition(RecState.recognized, done.first, done);
    if (best.isNotEmpty) {
      final top = best.entries.reduce((a, b) => b.value > a.value ? b : a);
      return Recognition(RecState.candidate, Prediction(top.key, top.value));
    }
    return Recognition(any ? RecState.unknown : RecState.searching);
  }
}

/// ② 폼 판정을 최근 프레임 다수결로 안정화한다. 종이 바뀌면 처음부터 센다.
class FormVoter {
  int window = 6;
  String? _species;
  final List<Prediction> _history = [];

  void reset() {
    _species = null;
    _history.clear();
  }

  void add(String species, Prediction form) {
    if (species != _species) reset();
    _species = species;
    _history.add(form);
    while (_history.length > window) {
      _history.removeAt(0);
    }
  }

  /// [species] 의 현재 폼 (가장 많이 나온 것과 평균 확신도). 기록이 없으면 null.
  Prediction? current(String species) {
    if (species != _species || _history.isEmpty) return null;
    final counts = <String, int>{};
    final sums = <String, double>{};
    for (final p in _history) {
      counts[p.id] = (counts[p.id] ?? 0) + 1;
      sums[p.id] = (sums[p.id] ?? 0) + p.confidence;
    }
    final best = counts.entries.reduce((a, b) => b.value > a.value ? b : a).key;
    return Prediction(best, sums[best]! / counts[best]!);
  }
}
