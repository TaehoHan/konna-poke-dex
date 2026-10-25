import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

/// 도감 설명 한 줄. [versions] 는 표시용 게임 이름 ("X", "오메가루비 · 알파사파이어").
/// [translated] 는 한국어 설명이 없어 영어 설명을 번역한 것 (dex.json 의 `tr`). 화면에는 따로 표시하지 않는다 (사용자 결정).
class Flavor {
  const Flavor(this.versions, this.text, {this.translated = false, this.gen, this.form});
  final String versions;
  final String text;
  final bool translated;
  final int? gen; // 그 게임의 세대 (dex.json 의 `g`). 모르면 null
  final String? form; // 폼 설명이면 다른 모습 키 `<종>__<폼>` (dex.json 의 `f`). 기본 모습 설명은 null
}

/// 다른 모습 (폼·이로치). [id] 는 썸네일 키 (`<종>__<폼>`, 이로치는 `<종>__shiny`).
class Variant {
  const Variant(this.id, this.name);
  final String id;
  final String name;
}

/// 도감 설명을 세대별로 묶는다 (세대 오름차순, 세대를 모르면 0). 같은 세대 안은 원래 순서.
Map<int, List<Flavor>> flavorsByGen(List<Flavor> flavors) {
  final m = <int, List<Flavor>>{};
  for (final f in flavors) {
    (m[f.gen ?? 0] ??= []).add(f);
  }
  return {for (final k in m.keys.toList()..sort()) k: m[k]!};
}

/// 고른 모습([variant], null = 기본)의 도감 설명. 그 폼 설명이 있으면 그것만, 없으면 기본 모습 설명.
List<Flavor> flavorsFor(List<Flavor> flavors, String? variant) {
  if (variant != null) {
    final own = [for (final f in flavors) if (f.form == variant) f];
    if (own.isNotEmpty) return own;
  }
  return [for (final f in flavors) if (f.form == null) f];
}

/// 종 하나의 도감 항목. 형식은 scripts/deploy/build-app-assets.py 가 만드는 assets/dex.json 과 같다.
class DexEntry {
  const DexEntry({
    required this.id,
    required this.no,
    required this.name,
    this.gen,
    this.nameEn = '',
    this.genus = '',
    this.types = const [],
    this.typesKo = const [],
    this.heightM,
    this.weightKg,
    this.flavors = const [],
    this.evolution = const [],
    this.variants = const [],
    this.genderDiff = false,
    this.genderRate,
  });

  final String id; // class_id (PokeAPI 종 영문 이름 소문자, - → _)
  final int no; // 전국도감 번호
  final int? gen;
  final String name;
  final String nameEn;
  final String genus; // "씨앗포켓몬"
  final List<String> types; // 영문 키 (색 고르기용)
  final List<String> typesKo;
  final double? heightM;
  final double? weightKg;
  final List<Flavor> flavors; // 비어 있으면(9세대 등) 설명 영역을 숨긴다
  final List<String> evolution; // 진화 계열 class_id (자기 자신 포함)
  final List<Variant> variants; // 다른 모습. 앱은 썸네일이 있는 것만 보여 준다
  final bool genderDiff; // 암수 생김새가 다르다 (암컷 그림 = `<종>__female`, 상세 화면의 암수 선택으로 본다)
  final int? genderRate; // 암컷 비율 8분의 n (0~8), -1 = 성별 없음, null = 모름

  /// 성비 표시 ("♂ 87.5% · ♀ 12.5%", "성별 없음"). 모르면 null.
  String? get genderLabel {
    final g = genderRate;
    if (g == null) return null;
    if (g < 0) return '성별 없음';
    String pct(double v) => v == v.roundToDouble() ? '${v.toInt()}%' : '$v%';
    if (g == 0) return '♂ 100%';
    if (g == 8) return '♀ 100%';
    return '♂ ${pct((8 - g) * 12.5)} · ♀ ${pct(g * 12.5)}';
  }

  String get femaleKey => '${id}__female';

  String get noLabel => 'No.${no.toString().padLeft(4, '0')}';

  factory DexEntry.fromJson(Map<String, dynamic> m) => DexEntry(
    id: m['id'] as String,
    no: m['no'] as int,
    gen: m['gen'] as int?,
    name: m['name'] as String,
    nameEn: (m['name_en'] as String?) ?? '',
    genus: (m['genus'] as String?) ?? '',
    types: [for (final t in (m['types'] as List? ?? const [])) t as String],
    typesKo: [for (final t in (m['types_ko'] as List? ?? const [])) t as String],
    heightM: (m['h'] as num?)?.toDouble(),
    weightKg: (m['w'] as num?)?.toDouble(),
    flavors: [
      for (final f in (m['flavor'] as List? ?? const []))
        Flavor((f['v'] as String?) ?? '', f['t'] as String, translated: f['tr'] == true, gen: f['g'] as int?, form: f['f'] as String?),
    ],
    evolution: [for (final e in (m['evo'] as List? ?? const [])) e as String],
    genderDiff: m['gender'] == true,
    genderRate: m['gr'] as int?,
    variants: [
      for (final v in (m['variants'] as List? ?? const []))
        Variant(v['id'] as String, (v['name'] as String?) ?? (v['id'] as String)),
    ],
  );
}

class Dex {
  Dex(this.entries, {this.formNames = const {}, this.formVariant = const {}, this.audioIds, this.thumbIds, this.audioExt = const {}})
    : _byId = {for (final e in entries) e.id: e};

  final List<DexEntry> entries; // 전국도감 번호순
  final Map<String, DexEntry> _byId;
  final Map<String, String> formNames; // `<종>__<폼>` → 한국어 폼 이름
  final Map<String, String> formVariant; // ② 폼 모델 라벨(PokeAPI 이름) → 다른 모습 키(세레비넷 태그, 그림·설명)

  /// ② 폼 라벨에 해당하는 다른 모습 키. 기본형(종 이름)이면 null.
  String? variantFor(String formLabel) {
    if (!formLabel.contains('__')) return null;
    return formVariant[formLabel] ?? formLabel;
  }
  final Set<String>? audioIds; // null 이면 모르는 것으로 보고 재생을 시도한다
  final Map<String, String> audioExt; // 음성 파일 확장자 (m4a 우선, 없으면 wav)

  /// AssetSource 에 넘길 음성 경로 (assets/ 다음부터).
  String audioPath(String id) => 'audio/$id.${audioExt[id] ?? 'm4a'}';
  final Set<String>? thumbIds; // null 이면 모르는 것으로 보고 이미지를 시도한다

  DexEntry? entry(String id) => _byId[id];

  /// 폼 라벨의 표시 이름. 기본형(종 이름 그대로)이면 null.
  String? formName(String formId) {
    if (!formId.contains('__')) return null;
    return formNames[formId] ?? formId.split('__').last;
  }

  bool hasAudio(String id) => audioIds == null || audioIds!.contains(id);
  bool hasThumb(String id) => thumbIds == null || thumbIds!.contains(id);

  List<int> get generations => {for (final e in entries) ?e.gen}.toList()..sort();

  static Dex parse(String json, {Set<String>? audioIds, Set<String>? thumbIds}) {
    final m = jsonDecode(json) as Map<String, dynamic>;
    final list = [for (final e in m['species'] as List) DexEntry.fromJson(e as Map<String, dynamic>)]
      ..sort((a, b) => a.no.compareTo(b.no));
    return Dex(
      list,
      formNames: Map<String, String>.from(m['forms'] as Map? ?? const {}),
      formVariant: Map<String, String>.from(m['form_variant'] as Map? ?? const {}),
      audioIds: audioIds,
      thumbIds: thumbIds,
    );
  }

  static Future<Dex> load() async {
    final raw = await rootBundle.loadString('assets/dex.json');
    Set<String>? audio, thumbs;
    final ext = <String, String>{};
    try {
      final assets = (await AssetManifest.loadFromAssetBundle(rootBundle)).listAssets();
      final wav = _ids(assets, 'assets/audio/', '.wav'), m4a = _ids(assets, 'assets/audio/', '.m4a');
      audio = {...wav, ...m4a};
      for (final id in wav) {
        ext[id] = 'wav';
      }
      for (final id in m4a) {
        ext[id] = 'm4a';
      }
      thumbs = _ids(assets, 'assets/thumbs/', '.png');
    } catch (_) {}
    // 1MB 가까운 JSON 이라 UI 스레드를 막지 않게 따로 파싱한다
    final dex = await compute(Dex.parse, raw);
    return Dex(dex.entries, formNames: dex.formNames, formVariant: dex.formVariant, audioIds: audio, thumbIds: thumbs, audioExt: ext);
  }

  static Set<String> _ids(Iterable<String> assets, String dir, String ext) => {
    for (final a in assets)
      if (a.startsWith(dir) && a.endsWith(ext)) a.substring(dir.length, a.length - ext.length),
  };
}
