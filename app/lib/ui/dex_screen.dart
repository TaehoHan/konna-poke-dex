import 'package:flutter/material.dart';

import '../app_model.dart';
import '../core/dex.dart';
import 'widgets.dart';

/// 도감 상세 화면을 연다. [formId] 는 ② 가 판정한 폼 라벨 (없으면 종만).
void openDetail(BuildContext context, AppModel model, String id, {String? formId}) {
  FocusManager.instance.primaryFocus?.unfocus(); // 키보드 닫기
  Navigator.of(context).push(MaterialPageRoute(builder: (_) => DexDetailPage(model: model, id: id, formId: formId)));
}

class DexScreen extends StatefulWidget {
  const DexScreen({super.key, required this.model});
  final AppModel model;

  @override
  State<DexScreen> createState() => _DexScreenState();
}

class _DexScreenState extends State<DexScreen> {
  String _query = '';
  int? _gen; // null = 전체
  bool _foundOnly = false;

  AppModel get model => widget.model;

  bool _match(DexEntry e, String q) =>
      e.name.contains(q) || e.id.contains(q) || e.nameEn.toLowerCase().contains(q) || '${e.no}' == q;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: model,
      builder: (context, _) {
        final dex = model.dex;
        if (dex == null) {
          return Scaffold(
            appBar: AppBar(title: const Text('도감')),
            body: Center(
              child: model.loadError != null
                  ? Padding(padding: const EdgeInsets.all(24), child: Text('도감 데이터를 불러오지 못했어요\n${model.loadError}'))
                  : const CircularProgressIndicator(),
            ),
          );
        }
        final all = dex.entries;
        final q = _query.trim().toLowerCase();
        final entries = [
          for (final e in all)
            if ((_gen == null || e.gen == _gen) && (!_foundOnly || model.discovered.contains(e.id)) && (q.isEmpty || _match(e, q))) e,
        ];
        return Scaffold(
          appBar: AppBar(
            title: Text('도감  ${model.discovered.length}/${all.length}'),
            bottom: PreferredSize(
              preferredSize: const Size.fromHeight(104),
              child: Column(children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(12, 0, 12, 4),
                  child: TextField(
                    onChanged: (v) => setState(() => _query = v),
                    textInputAction: TextInputAction.done, // 완료 키로도 닫힌다
                    decoration: InputDecoration(
                      hintText: '이름·번호로 검색',
                      prefixIcon: const Icon(Icons.search),
                      suffixIcon: _query.isEmpty
                          ? null
                          : IconButton(icon: const Icon(Icons.clear), onPressed: () => setState(() => _query = '')),
                      isDense: true,
                      filled: true,
                      border: OutlineInputBorder(borderRadius: BorderRadius.circular(24), borderSide: BorderSide.none),
                    ),
                  ),
                ),
                _chips(dex),
              ]),
            ),
          ),
          body: entries.isEmpty
              ? const Center(child: Text('검색 결과가 없어요'))
              : GridView.builder(
                  keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag, // 목록을 내리면 키보드가 내려가 하단 탭이 보인다
                  padding: const EdgeInsets.all(12),
                  gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
                    maxCrossAxisExtent: 130,
                    mainAxisSpacing: 10,
                    crossAxisSpacing: 10,
                    childAspectRatio: 0.78,
                  ),
                  itemCount: entries.length,
                  itemBuilder: (_, i) {
                    final e = entries[i];
                    final found = model.discovered.contains(e.id);
                    return InkWell(
                      borderRadius: BorderRadius.circular(12),
                      onTap: () => openDetail(context, model, e.id),
                      child: Column(children: [
                        Expanded(
                          child: Stack(fit: StackFit.expand, children: [
                            Positioned.fill(child: PokeThumb(dex: dex, entry: e)),
                            if (found)
                              const Positioned(top: 0, right: 0, child: Icon(Icons.check_circle, size: 22, color: Colors.red)),
                          ]),
                        ),
                        Text(e.noLabel, style: const TextStyle(fontSize: 10, color: Colors.black54)),
                        Text(e.name, style: const TextStyle(fontSize: 12), overflow: TextOverflow.ellipsis),
                      ]),
                    );
                  },
                ),
        );
      },
    );
  }

  Widget _chips(Dex dex) {
    Widget chip(String label, bool selected, VoidCallback onTap) => Padding(
      padding: const EdgeInsets.only(right: 8),
      child: ChoiceChip(label: Text(label), selected: selected, onSelected: (_) => setState(onTap)),
    );
    return SizedBox(
      height: 44,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 12),
        children: [
          Padding(
            padding: const EdgeInsets.only(right: 8),
            child: FilterChip(
              label: const Text('발견한 것만'),
              selected: _foundOnly,
              onSelected: (v) => setState(() => _foundOnly = v),
            ),
          ),
          chip('전체', _gen == null, () => _gen = null),
          for (final g in dex.generations) chip('$g세대', _gen == g, () => _gen = g),
        ],
      ),
    );
  }
}

class DexDetailPage extends StatefulWidget {
  const DexDetailPage({super.key, required this.model, required this.id, this.formId});
  final AppModel model;
  final String id;
  final String? formId;

  @override
  State<DexDetailPage> createState() => _DexDetailPageState();
}

class _DexDetailPageState extends State<DexDetailPage> {
  int? _flavorGen; // 도감 설명 세대 (null = 가장 최근 세대)
  late String? _variant = (widget.formId == widget.id || (widget.formId?.endsWith('__female') ?? false)) ? null : widget.formId; // 다른 모습 (null = 기본)
  late bool _female = widget.formId?.endsWith('__female') ?? false; // 암수 선택 (암수 생김새가 다른 종만)

  /// 큰 그림·폼 이름·도감 설명에 쓰는 키: 다른 모습을 고르면 그것, 아니면 암컷을 고른 경우 `<종>__female`.
  String? _shownKey(DexEntry e) => _variant ?? (_female && e.genderDiff ? e.femaleKey : null);

  AppModel get model => widget.model;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: model,
      builder: (context, _) {
        final dex = model.dex!;
        final e = dex.entry(widget.id);
        if (e == null) {
          return Scaffold(appBar: AppBar(), body: Center(child: Text('도감에 없는 포켓몬이에요 (${widget.id})')));
        }
        final variants = [for (final v in e.variants) if (dex.hasThumb(v.id) && !v.id.endsWith('__female')) v];
        final selected = variants.where((v) => v.id == _variant).firstOrNull;
        final form = selected?.name ?? (_variant == null ? null : dex.formName(_variant!));
        final shown = _shownKey(e);
        final canFemale = e.genderDiff && dex.hasThumb(e.femaleKey);
        final found = model.discovered.contains(e.id);
        final playing = model.playingId == e.id;
        final theme = Theme.of(context);
        return Scaffold(
          appBar: AppBar(title: Text('${e.noLabel} ${e.name}')),
          body: SafeArea(
            top: false, // Android 하단 내비게이션 바와 겹치지 않게 한다
            child: ListView(padding: const EdgeInsets.fromLTRB(20, 8, 20, 28), children: [
              Center(child: PokeThumb(dex: dex, entry: e, formId: shown, size: 160)),
              const SizedBox(height: 8),
              Center(child: Text(e.noLabel, style: theme.textTheme.bodySmall)),
              Center(child: Text(e.name, style: const TextStyle(fontSize: 26, fontWeight: FontWeight.bold))),
              if (form != null) Center(child: Chip(label: Text(form), visualDensity: VisualDensity.compact)),
              if (e.genus.isNotEmpty) Center(child: Text(e.genus, style: theme.textTheme.titleMedium)),
              if (found)
                const Center(child: Text('발견했어요', style: TextStyle(color: Colors.red))),
              if (canFemale) ...[
                const SizedBox(height: 10),
                Center(
                  child: SegmentedButton<bool>(
                    showSelectedIcon: false,
                    segments: const [
                      ButtonSegment(value: false, label: Text('♂ 수컷')),
                      ButtonSegment(value: true, label: Text('♀ 암컷')),
                    ],
                    selected: {_female && _variant == null},
                    onSelectionChanged: (v) => setState(() {
                      _female = v.first;
                      _variant = null; // 암수 그림은 기본 모습 기준이다
                    }),
                  ),
                ),
              ],
              if (variants.isNotEmpty) ...[
                const SizedBox(height: 10),
                _variantRow(dex, e, variants),
              ],
              const SizedBox(height: 10),
              Center(child: TypeChips(e)),
              const SizedBox(height: 14),
              Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                _Stat('키', e.heightM == null ? '-' : '${_num(e.heightM!)} m'),
                const SizedBox(width: 32),
                _Stat('몸무게', e.weightKg == null ? '-' : '${_num(e.weightKg!)} kg'),
              ]),
              if (e.genderLabel != null) ...[
                const SizedBox(height: 12),
                _GenderBar(e),
              ],
              if (flavorsFor(e.flavors, shown).isNotEmpty) ...[
                const SizedBox(height: 16),
                _flavorCard(e),
              ],
              if (model.canPlay(e.id)) ...[
                const SizedBox(height: 12),
                Center(
                  child: FilledButton.icon(
                    onPressed: () => model.playManually(e.id),
                    icon: Icon(playing ? Icons.stop : Icons.volume_up),
                    label: Text(playing ? '멈추기' : '설명 듣기'),
                  ),
                ),
              ],
              if (e.evolution.length > 1) ...[
                const SizedBox(height: 20),
                Text('진화 계열', style: theme.textTheme.titleMedium),
                const SizedBox(height: 8),
                Wrap(spacing: 8, runSpacing: 8, children: [
                  for (final id in e.evolution)
                    if (dex.entry(id) case final evo?) _evoTile(dex, evo, current: evo.id == e.id),
                ]),
              ],
            ]),
          ),
        );
      },
    );
  }

  /// 도감 설명: 세대 드롭다운으로 고르고, 그 세대의 버전별 설명을 모두 보여 준다. 기본은 가장 최근 세대.
  Widget _flavorCard(DexEntry e) {
    final byGen = flavorsByGen(flavorsFor(e.flavors, _shownKey(e)));
    if (byGen.isEmpty) return const SizedBox.shrink();
    final gens = byGen.keys.toList();
    final gen = byGen.containsKey(_flavorGen) ? _flavorGen! : gens.last;
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 12),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Text('도감 설명', style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
            const Spacer(),
            if (gens.length > 1)
              DropdownButton<int>(
                value: gen,
                underline: const SizedBox.shrink(),
                items: [for (final g in gens.reversed) DropdownMenuItem(value: g, child: Text(_genLabel(g)))],
                onChanged: (g) => setState(() => _flavorGen = g),
              )
            else
              Padding(padding: const EdgeInsets.symmetric(vertical: 12), child: Text(_genLabel(gen))),
          ]),
          for (final f in byGen[gen]!) ...[
            Text(f.versions, style: const TextStyle(fontSize: 12, color: Colors.black54)),
            Padding(
              padding: const EdgeInsets.only(top: 2, bottom: 10),
              child: Text(f.text, style: const TextStyle(fontSize: 15, height: 1.5)),
            ),
          ],
        ]),
      ),
    );
  }

  static String _genLabel(int g) => g == 0 ? '기타' : '$g세대';

  /// 기본 + 다른 모습(이로치·폼) 작은 그림. 누르면 위 큰 그림이 바뀐다.
  Widget _variantRow(Dex dex, DexEntry e, List<Variant> variants) {
    Widget tile(String? id, String label) {
      final sel = _variant == id;
      return Padding(
        padding: const EdgeInsets.only(right: 8),
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: () => setState(() => _variant = id),
          child: Container(
            width: 76,
            padding: const EdgeInsets.all(4),
            decoration: BoxDecoration(
              border: Border.all(color: sel ? Colors.red : Colors.black12, width: sel ? 2 : 1),
              borderRadius: BorderRadius.circular(12),
            ),
            child: Column(children: [
              PokeThumb(dex: dex, entry: e, formId: id, size: 48),
              Text(keepWords(label), style: const TextStyle(fontSize: 10), maxLines: 2, textAlign: TextAlign.center, overflow: TextOverflow.ellipsis),
            ]),
          ),
        ),
      );
    }

    return SizedBox(
      height: 92,
      child: ListView(scrollDirection: Axis.horizontal, children: [
        tile(null, '기본 모습'),
        for (final v in variants) tile(v.id, v.name),
      ]),
    );
  }

  Widget _evoTile(Dex dex, DexEntry evo, {required bool current}) {
    return InkWell(
      borderRadius: BorderRadius.circular(12),
      onTap: current
          ? null
          : () => Navigator.of(context).pushReplacement(
                MaterialPageRoute(builder: (_) => DexDetailPage(model: model, id: evo.id)),
              ),
      child: Container(
        width: 88,
        padding: const EdgeInsets.all(6),
        decoration: BoxDecoration(
          border: Border.all(color: current ? Colors.red : Colors.black12, width: current ? 2 : 1),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Column(children: [
          PokeThumb(dex: dex, entry: evo, size: 56),
          Text(evo.name, style: const TextStyle(fontSize: 12), overflow: TextOverflow.ellipsis),
        ]),
      ),
    );
  }

  static String _num(double v) => v == v.roundToDouble() ? v.toStringAsFixed(0) : '$v';
}

class _Stat extends StatelessWidget {
  const _Stat(this.label, this.value);
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Column(children: [
    Text(label, style: const TextStyle(fontSize: 12, color: Colors.black54)),
    Text(value, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
  ]);
}


/// 성비: 수컷(파랑)·암컷(빨강) 막대와 비율. 성별 없는 종은 글자만.
class _GenderBar extends StatelessWidget {
  const _GenderBar(this.e);
  final DexEntry e;

  @override
  Widget build(BuildContext context) {
    final g = e.genderRate!;
    return Column(children: [
      const Text('성비', style: TextStyle(fontSize: 12, color: Colors.black54)),
      const SizedBox(height: 4),
      if (g >= 0)
        SizedBox(
          width: 220,
          height: 10,
          child: ClipRRect(
            borderRadius: BorderRadius.circular(5),
            child: Row(children: [
              if (g < 8) Expanded(flex: 8 - g, child: Container(color: const Color(0xFF3B82F6))),
              if (g > 0) Expanded(flex: g, child: Container(color: const Color(0xFFEF4477))),
            ]),
          ),
        ),
      const SizedBox(height: 4),
      Text(e.genderLabel!, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
    ]);
  }
}

