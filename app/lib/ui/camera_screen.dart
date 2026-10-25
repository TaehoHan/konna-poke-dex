
import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../app_model.dart';
import '../core/photo.dart';
import '../core/recognizer.dart';
import 'dex_screen.dart';
import 'photo_zoom.dart';
import 'widgets.dart';

class CameraScreen extends StatelessWidget {
  const CameraScreen({super.key, required this.model});
  final AppModel model;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: model,
      builder: (context, _) {
        if (model.loadError != null) return _Message('도감 데이터를 불러오지 못했어요\n${model.loadError}');
        if (model.modelError != null) return _Message('인식 모델을 불러오지 못했어요\n${model.modelError}');
        if (!model.loaded) return const Center(child: CircularProgressIndicator());
        if (!model.hasSpeciesModel) {
          return const _Message('인식 모델이 아직 없어요.\n도감 탭은 그대로 쓸 수 있어요.');
        }
        if (model.cameraDenied) {
          return const _Message('카메라 권한이 필요해요.\n설정 > 포켓몬 도감에서 카메라를 허용해 주세요.');
        }
        final cam = model.camera;
        return AnnotatedRegion<SystemUiOverlayStyle>( // 검정 배경 위에서 상태 표시줄 아이콘(시계·알림)이 보이게 한다
          value: SystemUiOverlayStyle.light.copyWith(statusBarColor: Colors.transparent),
          child: Container(
          color: Colors.black,
          child: SafeArea(
            child: LayoutBuilder(builder: (context, box) {
              final portrait = box.maxHeight >= box.maxWidth;
              final ar = (cam != null && cam.value.isInitialized) ? cam.value.aspectRatio : 16 / 9;
              final ratio = portrait ? 1 / ar : ar; // 미리보기 가로/세로
              // 미리보기를 화면 안에 맞춘 실제 크기 (가이드는 이 중 짧은 변 기준)
              var pw = box.maxWidth, ph = pw / ratio;
              if (ph > box.maxHeight) {
                ph = box.maxHeight;
                pw = ph * ratio;
              }
              return Stack(children: [
                Center(
                  child: cam != null && cam.value.isInitialized
                      ? SizedBox(
                          width: pw,
                          height: ph,
                          child: _Gestures(model: model, child: CameraPreview(cam)),
                        )
                      : const CircularProgressIndicator(),
                ),
                if (!model.multi) Center(child: IgnorePointer(child: CustomPaint(size: Size(pw, ph), painter: _GuidePainter(pw < ph ? pw : ph)))),
                if (model.region != null) _RegionBox(model: model, pw: pw, ph: ph, offX: (box.maxWidth - pw) / 2, offY: (box.maxHeight - ph) / 2),
                if (model.focusPoint != null)
                  Positioned(
                    left: (box.maxWidth - pw) / 2 + model.focusPoint!.dx * pw - 32,
                    top: (box.maxHeight - ph) / 2 + model.focusPoint!.dy * ph - 32,
                    child: IgnorePointer(
                      child: Container(
                        width: 64,
                        height: 64,
                        decoration: BoxDecoration(
                            border: Border.all(color: Colors.yellowAccent, width: 2), borderRadius: BorderRadius.circular(8)),
                      ),
                    ),
                  ),
                if (model.showDebug) Positioned(left: 8, top: 8, child: _Debug(model: model)),
                if (model.showDebug) Positioned(right: 8, top: 60, child: _CaptureButtons(model: model)),
                // 네이티브 카메라처럼: 아래에서 위로 인식 카드 → 줌 → 도구 줄(왼쪽 도구, 가운데 셔터, 오른쪽 전환)
                Positioned(
                  left: 0,
                  right: 0,
                  bottom: 0,
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 12),
                      child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 560), child: _InfoCard(model: model)),
                    ),
                    const SizedBox(height: 8),
                    _ZoomBar(model: model),
                    const SizedBox(height: 8),
                    _ControlBar(model: model),
                  ]),
                ),
              ]);
            }),
          ),
          ),
        );
      },
    );
  }
}

/// 탭: 그 지점에 초점·노출, 두 손가락: 확대/축소.
class _Gestures extends StatefulWidget {
  const _Gestures({required this.model, required this.child});
  final AppModel model;
  final Widget child;
  @override
  State<_Gestures> createState() => _GesturesState();
}

class _GesturesState extends State<_Gestures> {
  double _base = 1;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, box) {
      return GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTapUp: (d) {
          final p = Offset(d.localPosition.dx / box.maxWidth, d.localPosition.dy / box.maxHeight);
          widget.model.focusAt(p); // 카메라 초점·노출
          widget.model.setRegion(p); // 그 둘레 위주로 판별
        },
        onScaleStart: (_) => _base = widget.model.zoom,
        onScaleUpdate: (d) {
          if (d.pointerCount >= 2) widget.model.setZoom(_base * d.scale);
        },
        child: widget.child,
      );
    });
  }
}

class _ZoomBar extends StatelessWidget {
  const _ZoomBar({required this.model});
  final AppModel model;
  @override
  Widget build(BuildContext context) {
    final levels = [1.0, 2.0, 3.0, 5.0].where((z) => z >= model.minZoom - 0.01 && z <= model.maxZoom + 0.01).toList();
    return Container(
      decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(20)),
      padding: const EdgeInsets.symmetric(horizontal: 4),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        for (final z in levels)
          TextButton(
            style: TextButton.styleFrom(minimumSize: const Size(44, 36), padding: EdgeInsets.zero),
            onPressed: () => model.setZoom(z),
            child: Text(
              '${z.toStringAsFixed(0)}×',
              style: TextStyle(
                  color: (model.zoom - z).abs() < 0.15 ? Colors.yellowAccent : Colors.white,
                  fontWeight: (model.zoom - z).abs() < 0.15 ? FontWeight.bold : FontWeight.normal),
            ),
          ),
        Padding(
          padding: const EdgeInsets.only(right: 8),
          child: Text('${model.zoom.toStringAsFixed(1)}×', style: const TextStyle(color: Colors.white70, fontSize: 12)),
        ),
      ]),
    );
  }
}

/// 분류에 쓰는 가운데 정사각형(가운데 자르기)의 안쪽 80% 를 보여 주는 가이드.
class _GuidePainter extends CustomPainter {
  _GuidePainter(this.side);
  final double side;
  @override
  void paint(Canvas canvas, Size size) {
    final s = side * 0.8;
    final r = RRect.fromRectAndRadius(
        Rect.fromCenter(center: size.center(Offset.zero), width: s, height: s), const Radius.circular(24));
    canvas.drawRRect(
        r,
        Paint()
          ..color = Colors.white70
          ..style = PaintingStyle.stroke
          ..strokeWidth = 3);
  }

  @override
  bool shouldRepaint(_GuidePainter o) => o.side != side;
}

class _InfoCard extends StatelessWidget {
  const _InfoCard({required this.model});
  final AppModel model;

  @override
  Widget build(BuildContext context) {
    final e = model.shown;
    if (e == null) {
      final msg = model.cameraError != null ? '카메라 오류: ${model.cameraError}' : switch (model.recognition.state) {
        RecState.searching => model.region != null ? '네모 안의 포켓몬을 찾는 중이에요' : (model.multi ? '포켓몬을 비춰 보세요 (여러 마리도 찾아요). 화면을 누르면 그 둘레만 판별해요' : '포켓몬을 네모 안에 비춰 보세요'),
        RecState.unknown => '포켓몬을 찾지 못했어요. 조금 더 가까이 비춰 보세요',
        _ => '찾는 중…',
      };
      return Container(
        padding: const EdgeInsets.symmetric(vertical: 14, horizontal: 16),
        decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(16)),
        child: Text(msg, textAlign: TextAlign.center, style: const TextStyle(color: Colors.white, fontSize: 15)),
      );
    }
    final dex = model.dex!;
    final playing = model.playingId == e.id;
    final others = model.recognized;
    final form = model.shownForm == null ? null : dex.formName(model.shownForm!);
    final variant = model.shownForm == null ? null : dex.variantFor(model.shownForm!); // 폼 그림·설명 키
    final card = Material(
      color: Colors.white,
      borderRadius: BorderRadius.circular(20),
      child: InkWell(
        borderRadius: BorderRadius.circular(20),
        onTap: () => openDetail(context, model, e.id, formId: variant),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Row(children: [
            PokeThumb(dex: dex, entry: e, formId: variant, size: 72),
            const SizedBox(width: 12),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
                Text('${e.noLabel}  ${(model.shownConfidence * 100).toStringAsFixed(0)}%',
                    style: const TextStyle(fontSize: 12, color: Colors.black54)),
                Text(e.name, style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
                if (form != null) Text(form, style: const TextStyle(fontSize: 13, color: Colors.black87)),
                if (e.genus.isNotEmpty) Text(e.genus, style: const TextStyle(fontSize: 13)),
                const SizedBox(height: 4),
                TypeChips(e, small: true),
              ]),
            ),
            if (model.canPlay(e.id))
              IconButton.filledTonal(
                onPressed: () => model.playManually(e.id),
                icon: Icon(playing ? Icons.stop : Icons.volume_up),
              ),
          ]),
        ),
      ),
    );
    if (others.length < 2) return card;
    return Column(mainAxisSize: MainAxisSize.min, children: [
      SizedBox(
        height: 44,
        child: ListView(scrollDirection: Axis.horizontal, shrinkWrap: true, children: [
          for (final p in others)
            if (dex.entry(p.id) case final o?)
              Padding(
                padding: const EdgeInsets.only(right: 6),
                child: ChoiceChip(
                  avatar: SizedBox(width: 24, height: 24, child: PokeThumb(dex: dex, entry: o, size: 24)),
                  label: Text('${o.name} ${(p.confidence * 100).toStringAsFixed(0)}%'),
                  selected: o.id == e.id,
                  onSelected: (_) => model.selectRecognized(o.id),
                ),
              ),
        ]),
      ),
      const SizedBox(height: 6),
      card,
    ]);
  }
}

class _Message extends StatelessWidget {
  const _Message(this.text);
  final String text;
  @override
  Widget build(BuildContext context) => Center(
      child: Padding(padding: const EdgeInsets.all(24), child: Text(text, textAlign: TextAlign.center)));
}


/// 진단: 모델에 실제로 들어가는 입력 이미지(똑바로 서 있어야 한다)와 상위 3개 후보, ② 폼 판정.
class _Debug extends StatelessWidget {
  const _Debug({required this.model});
  final AppModel model;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(6),
      decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(8)),
      child: DefaultTextStyle(
        style: const TextStyle(color: Colors.white, fontSize: 11),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (model.inputPreview != null) SizedBox(width: 96, height: 96, child: RawImage(image: model.inputPreview, fit: BoxFit.cover)),
          Text(model.frameInfo),
          Text('${model.fps.toStringAsFixed(0)}fps ${model.msPerFrame.toStringAsFixed(0)}ms (영역 ${model.tileCount}곳)'),
          if (model.recognized.length > 1) Text('인식 ${model.recognized.map((p) => p.id).join(', ')}'),
          for (final p in model.top) Text('${p.id} ${(p.confidence * 100).toStringAsFixed(0)}%'),
          for (final f in model.lastForms) Text('② ${f.id} ${(f.confidence * 100).toStringAsFixed(0)}%'),
        ]),
      ),
    );
  }
}


/// 진단: 실제 촬영 수집 버튼. "포켓몬 아님" → `captures/none`, "정답 지정" → `captures/<class_id>`.
/// 맥으로 꺼내기: scripts/eval/import-captures.py (`workspace/eval_real/<라벨>/`).
class _CaptureButtons extends StatelessWidget {
  const _CaptureButtons({required this.model});
  final AppModel model;

  Future<void> _capture(BuildContext context, {required bool none}) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      final shot = await model.grabFrame(); // 누른 순간의 다음 프레임
      String? label = noneLabel;
      if (!none) {
        if (!context.mounted) return;
        label = await showModalBottomSheet<String>(
          context: context,
          isScrollControlled: true,
          builder: (_) => _SpeciesPicker(model: model, suggestions: [for (final p in shot.top) if (!p.isNone) p.id]),
        );
        if (label == null) return;
      }
      await model.saveShot(shot, label);
      final name = label == noneLabel ? '포켓몬 아님' : (model.dex?.entry(label)?.name ?? label);
      messenger.showSnackBar(SnackBar(content: Text('저장했어요: $name (모두 ${model.captureCount}장)'), duration: const Duration(seconds: 2)));
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text('저장하지 못했어요: $e')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final style = FilledButton.styleFrom(visualDensity: VisualDensity.compact, padding: const EdgeInsets.symmetric(horizontal: 10));
    return Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
      FilledButton.tonal(style: style, onPressed: () => _capture(context, none: true), child: const Text('포켓몬 아님')),
      const SizedBox(height: 6),
      FilledButton.tonal(style: style, onPressed: () => _capture(context, none: false), child: const Text('정답 지정')),
      Text('${model.captureCount}장', style: const TextStyle(color: Colors.white, fontSize: 11)),
    ]);
  }
}

/// 정답 종 고르기: 지금 상위 후보 + 이름·번호 검색.
class _SpeciesPicker extends StatefulWidget {
  const _SpeciesPicker({required this.model, required this.suggestions});
  final AppModel model;
  final List<String> suggestions;
  @override
  State<_SpeciesPicker> createState() => _SpeciesPickerState();
}

class _SpeciesPickerState extends State<_SpeciesPicker> {
  String _q = '';

  @override
  Widget build(BuildContext context) {
    final dex = widget.model.dex!;
    final q = _q.trim().toLowerCase();
    final list = q.isEmpty
        ? [for (final id in widget.suggestions) ?dex.entry(id)]
        : [for (final e in dex.entries) if (e.name.contains(q) || e.id.contains(q) || '${e.no}' == q) e];
    return SafeArea(
      child: SizedBox(
        height: MediaQuery.of(context).size.height * 0.7,
        child: Column(children: [
          Padding(
            padding: const EdgeInsets.all(12),
            child: TextField(
              autofocus: true,
              decoration: const InputDecoration(hintText: '맞는 포켓몬 이름·번호', prefixIcon: Icon(Icons.search)),
              onChanged: (v) => setState(() => _q = v),
            ),
          ),
          if (q.isEmpty) const Text('지금 후보', style: TextStyle(fontSize: 12, color: Colors.black54)),
          Expanded(
            child: ListView(children: [
              for (final e in list.take(50))
                ListTile(
                  leading: SizedBox(width: 40, child: PokeThumb(dex: dex, entry: e, size: 40)),
                  title: Text(e.name),
                  subtitle: Text(e.noLabel),
                  onTap: () => Navigator.pop(context, e.id),
                ),
            ]),
          ),
        ]),
      ),
    );
  }
}


/// 사진 찍어 판별: 흔들리는 영상 대신 정지 사진 한 장을 여러 영역으로 판별한다.
class _Shutter extends StatelessWidget {
  const _Shutter({required this.model});
  final AppModel model;

  Future<void> _shoot(BuildContext context) async {
    final nav = Navigator.of(context);
    final messenger = ScaffoldMessenger.of(context);
    showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (_) => const Center(child: Card(child: Padding(padding: EdgeInsets.all(24), child: Column(mainAxisSize: MainAxisSize.min, children: [
        CircularProgressIndicator(),
        SizedBox(height: 12),
        Text('판별하는 중…'),
      ])))),
    );
    PhotoResult? r;
    String? err;
    try {
      r = await model.takePhoto();
    } catch (e) {
      err = '$e';
    } finally {
      nav.pop();
    }
    if (r == null) {
      messenger.showSnackBar(SnackBar(content: Text(err == null ? '카메라가 아직 준비되지 않았어요' : '사진을 판별하지 못했어요: $err')));
      await model.resumeAfterPhoto();
      return;
    }
    if (!context.mounted) return;
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true, // 상단 노치·상태 표시줄 아래에서 시작한다 (노치에 걸려 못 내리던 문제)
      showDragHandle: false,
      builder: (_) => _PhotoSheet(model: model, result: r!),
    );
    await model.resumeAfterPhoto();
  }

  @override
  Widget build(BuildContext context) => GestureDetector(
        onTap: () => _shoot(context),
        child: Container(
          width: 64,
          height: 64,
          padding: const EdgeInsets.all(4),
          decoration: BoxDecoration(shape: BoxShape.circle, border: Border.all(color: Colors.white, width: 3), color: Colors.black26),
          child: Container(decoration: const BoxDecoration(shape: BoxShape.circle, color: Colors.white)),
        ),
      );
}

class _PhotoSheet extends StatefulWidget {
  const _PhotoSheet({required this.model, required this.result});
  final AppModel model;
  final PhotoResult result;

  @override
  State<_PhotoSheet> createState() => _PhotoSheetState();
}

class _PhotoSheetState extends State<_PhotoSheet> {
  PhotoView? _focus; // 이 화면에서 사진을 탭해 고른 영역 기준 결과 (처음에는 없다: 사진 전체를 먼저 보여 준다)
  bool _useFocus = false;
  bool _busy = false; // 사진을 탭해 다시 판별하는 중

  /// 전체 화면으로 사진을 확대해 보며 영역을 골라 판별한다. 거기서 고른 마지막 영역의 결과를 이 화면에 반영한다.
  Future<void> _openZoom(BuildContext context) async {
    final v = await Navigator.of(context).push<PhotoView?>(MaterialPageRoute(
      fullscreenDialog: true,
      builder: (_) => PhotoZoomPage(model: widget.model, result: widget.result, initial: _useFocus ? _focus : null),
    ));
    if (v != null && mounted) {
      setState(() {
        _focus = v;
        _useFocus = true;
      });
    }
  }

  /// 사진을 탭한 곳 둘레만 다시 판별한다.
  Future<void> _reselect(Offset p) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final v = await widget.model.analyzePhotoRegion(widget.result, p);
      if (v != null && mounted) {
        setState(() {
          _focus = v;
          _useFocus = true;
        });
      }
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('다시 판별하지 못했어요: $e')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final model = widget.model;
    final dex = model.dex!;
    final res = widget.result;
    final v = (_useFocus && _focus != null) ? _focus! : res.all; // 지금 보여 주는 기준의 결과
    final r = (
      preview: res.preview,
      width: res.width,
      height: res.height,
      tiles: v.tiles,
      judged: v.judged,
      top3: v.top3,
      forms: v.forms,
      ms: res.ms,
    );
    final judgedIds = {for (final c in r.judged) c.id};
    final colors = [Colors.redAccent, Colors.lightBlueAccent, Colors.greenAccent, Colors.amberAccent, Colors.purpleAccent];
    Widget row(Candidate c, {required bool judged, int? rank}) {
      final e = dex.entry(c.id);
      if (e == null) return const SizedBox.shrink();
      final f = r.forms[c.id];
      final formKey = (f != null && f.id.contains('__') && f.confidence >= model.formThreshold) ? f.id : null;
      final form = formKey == null ? null : dex.formName(formKey);
      return ListTile(
        dense: true,
        leading: SizedBox(width: 48, child: PokeThumb(dex: dex, entry: e, formId: formKey == null ? null : dex.variantFor(formKey), size: 48)),
        title: Text('${rank != null ? '$rank. ' : ''}${e.name}${form != null ? ' · $form' : ''}'),
        subtitle: LinearProgressIndicator(value: c.prob.clamp(0.0, 1.0), minHeight: 6),
        trailing: Text('${(c.prob * 100).toStringAsFixed(c.prob < 0.1 ? 1 : 0)}%', style: const TextStyle(fontWeight: FontWeight.bold)),
        onTap: () => openDetail(context, model, e.id, formId: formKey == null ? null : dex.variantFor(formKey)),
      );
    }

    final top = r.top3.isEmpty ? null : r.top3.first;
    final screenH = MediaQuery.of(context).size.height;
    return SafeArea(
      child: SizedBox(
        height: screenH * 0.8,
        child: ListView(padding: const EdgeInsets.only(bottom: 24), children: [
          // 위쪽 줄: 제목 + 닫기 (시트를 끌어내리기 어려운 화면에서도 닫을 수 있게)
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 4, 4, 0),
            child: Row(children: [
              const Expanded(child: Text('사진 판별', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold))),
              IconButton(icon: const Icon(Icons.close), tooltip: '닫기', onPressed: () => Navigator.of(context).pop()),
            ]),
          ),
          if (_focus != null)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: SegmentedButton<bool>(
                showSelectedIcon: false,
                segments: const [
                  ButtonSegment(value: true, label: Text('선택한 영역')),
                  ButtonSegment(value: false, label: Text('화면 전체')),
                ],
                selected: {_useFocus},
                onSelectionChanged: (x) => setState(() => _useFocus = x.first),
              ),
            ),
          // 사진 + 판별된 영역 상자 (높이는 화면의 30% 까지만, Top 3 가 바로 보이도록)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Center( // ListView 의 꽉 찬 너비 제약을 풀어 줘야 AspectRatio 가 높이 제한에 맞춰 줄어든다
              child: ConstrainedBox(
              constraints: BoxConstraints(maxHeight: screenH * 0.30),
              child: AspectRatio(
              aspectRatio: r.width / r.height,
              child: PhotoBoxes(
                preview: r.preview,
                width: r.width,
                height: r.height,
                onTap: _reselect,
                boxes: [
                  if (v.region != null) PhotoBox(v.region!, '선택한 영역', Colors.white),
                  for (var i = 0; i < r.judged.length; i++)
                    PhotoBox(r.tiles[r.judged[i].tile], dex.entry(r.judged[i].id)?.name ?? r.judged[i].id, colors[i % colors.length]),
                ],
              ),
              ),
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 4, 12, 0),
            child: Row(children: [
              Expanded(
                child: _busy
                    ? const Row(children: [SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2)), SizedBox(width: 8), Text('다시 판별하는 중…', style: TextStyle(fontSize: 12))])
                    : const Text('사진을 누르면 그 둘레만 다시 판별해요', style: TextStyle(fontSize: 12, color: Colors.black54)),
              ),
              FilledButton.tonalIcon(
                style: FilledButton.styleFrom(visualDensity: VisualDensity.compact),
                onPressed: () => _openZoom(context),
                icon: const Icon(Icons.zoom_in, size: 18),
                label: const Text('확대해서 판별'),
              ),
            ]),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 12, 20, 0),
            child: Text(
              r.judged.isEmpty ? '확실하게 판별하지 못했어요' : (r.judged.length == 1 ? '판별 결과' : '판별 결과 · ${r.judged.length}마리'),
              style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
            ),
          ),
          if (r.judged.isEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 4, 20, 0),
              child: Text('인식 기준 ${(model.threshold * 100).toStringAsFixed(0)}% 이상인 포켓몬이 없어요. 아래 가까운 후보를 참고하세요.',
                  style: const TextStyle(fontSize: 12, color: Colors.black54)),
            )
          else
            for (final c in r.judged) row(c, judged: true),
          const Divider(height: 24),
          const Padding(
            padding: EdgeInsets.symmetric(horizontal: 20),
            child: Text('가장 가까운 포켓몬 Top 3', style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
          ),
          const Padding(
            padding: EdgeInsets.fromLTRB(20, 2, 20, 0),
            child: Text('"포켓몬 아님"은 빼고 보여 줘요. 퍼센트는 모델이 낸 값 그대로예요.', style: TextStyle(fontSize: 12, color: Colors.black54)),
          ),
          for (var i = 0; i < r.top3.length; i++) row(r.top3[i], judged: judgedIds.contains(r.top3[i].id), rank: i + 1),
          if (top != null && top.noneProb >= 0.5)
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 4, 20, 0),
              child: Text('1위가 나온 영역에서 모델은 "포켓몬 아님"을 ${(top.noneProb * 100).toStringAsFixed(0)}% 로 봤어요. 포켓몬이 아닐 수 있어요.',
                  style: const TextStyle(fontSize: 12, color: Colors.deepOrange)),
            ),
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 8, 20, 0),
            child: Text('${res.srcWidth > 0 ? '원본 ${res.srcWidth}×${res.srcHeight}${res.srcWidth != res.width ? ' → 판별 ${res.width}×${res.height}' : ''} · ' : ''}${res.all.tiles.length}개 영역 판별 · ${(r.ms / 1000).toStringAsFixed(1)}초', style: const TextStyle(fontSize: 11, color: Colors.black45)),
          ),
        ]),
      ),
    );
  }
}


/// 탭한 영역 표시: 가장 큰 판별 영역(짧은 변의 55%)을 조준 틀(네 모서리)로 그리고, 아래(자리가 없으면 위)의 작은 알약으로 해제한다.
/// 알약이 틀 바깥으로 나가면 Flutter 가 탭을 전달하지 않으므로(부모 영역 밖), 알약까지 포함한 영역을 Positioned 로 잡는다.
class _RegionBox extends StatelessWidget {
  const _RegionBox({required this.model, required this.pw, required this.ph, required this.offX, required this.offY});
  final AppModel model;
  final double pw, ph, offX, offY;

  @override
  Widget build(BuildContext context) {
    final r = model.regionOnScreen!;
    final side = (pw < ph ? pw : ph) * 0.55;
    final x = (r.dx * pw - side / 2).clamp(0.0, pw - side);
    final y = (r.dy * ph - side / 2).clamp(0.0, ph - side);
    final extra = RegionFrame.pillSpace;
    final pillBelow = y + side + extra <= ph || y < extra; // 아래에 자리가 있거나, 위에도 자리가 없으면 아래
    return Positioned(
      left: offX + x,
      top: offY + (pillBelow ? y : y - extra),
      width: side,
      height: side + extra,
      child: RegionFrame(key: ValueKey('${x.round()}_${y.round()}'), side: side, pillBelow: pillBelow, onClear: model.clearRegion),
    );
  }
}

/// 조준 틀 + 해제 알약 (세로로 틀과 알약을 이어 붙인 한 덩어리, 높이 = side + [pillSpace]).
/// 새 영역이 잡힐 때 살짝 커지며 나타난다 (위치가 바뀌면 key 가 달라져 다시 나타난다).
class RegionFrame extends StatelessWidget {
  const RegionFrame({super.key, required this.side, required this.pillBelow, required this.onClear, this.animate = true});
  final double side;
  final bool pillBelow;
  final VoidCallback onClear;
  final bool animate;

  static const pillHeight = 28.0;
  static const pillSpace = pillHeight + 8; // 알약 + 틀과의 간격

  @override
  Widget build(BuildContext context) {
    final bracket = SizedBox(width: side, height: side, child: IgnorePointer(child: CustomPaint(painter: _BracketPainter())));
    final pill = SizedBox(
      height: pillHeight,
      child: Center(
        child: Material(
          color: Colors.black.withValues(alpha: 0.55),
          shape: const StadiumBorder(side: BorderSide(color: Colors.white38)),
          child: InkWell(
            customBorder: const StadiumBorder(),
            onTap: onClear,
            child: const Padding(
              padding: EdgeInsets.only(left: 12, right: 8),
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                Text('선택 영역', style: TextStyle(fontSize: 12, color: Colors.white, fontWeight: FontWeight.w600)),
                SizedBox(width: 6),
                Icon(Icons.close_rounded, size: 16, color: Colors.white70),
              ]),
            ),
          ),
        ),
      ),
    );
    const gap = SizedBox(height: pillSpace - pillHeight);
    final frame = SizedBox(
      width: side,
      height: side + pillSpace,
      child: Column(children: pillBelow ? [bracket, gap, pill] : [pill, gap, bracket]),
    );
    if (!animate) return frame;
    return TweenAnimationBuilder<double>(
      tween: Tween(begin: 1.12, end: 1.0),
      duration: const Duration(milliseconds: 160),
      curve: Curves.easeOut,
      builder: (context, t, child) => Opacity(opacity: (1.6 - t).clamp(0.0, 1.0), child: Transform.scale(scale: t, child: child)),
      child: frame,
    );
  }
}

/// 조준 틀: 네 모서리에 둥근 ㄱ자 선. 어두운 배경 위에서도 보이게 얇은 그림자를 깐다.
class _BracketPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final len = size.shortestSide * 0.2;
    final r = size.shortestSide * 0.06; // 모서리 둥글기
    Path corner(double sx, double sy) {
      // (sx, sy) = 모서리 방향 (+1 은 왼쪽/위, -1 은 오른쪽/아래)
      final ox = sx > 0 ? 0.0 : size.width, oy = sy > 0 ? 0.0 : size.height;
      return Path()
        ..moveTo(ox + sx * len, oy)
        ..lineTo(ox + sx * r, oy)
        ..quadraticBezierTo(ox, oy, ox, oy + sy * r)
        ..lineTo(ox, oy + sy * len);
    }

    final paths = [corner(1, 1), corner(-1, 1), corner(1, -1), corner(-1, -1)];
    final shadow = Paint()
      ..color = Colors.black45
      ..style = PaintingStyle.stroke
      ..strokeWidth = 6
      ..strokeCap = StrokeCap.round
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 2);
    final line = Paint()
      ..color = Colors.white
      ..style = PaintingStyle.stroke
      ..strokeWidth = 3.5
      ..strokeCap = StrokeCap.round;
    for (final p in paths) {
      canvas.drawPath(p, shadow);
    }
    for (final p in paths) {
      canvas.drawPath(p, line);
    }
  }

  @override
  bool shouldRepaint(_BracketPainter old) => false;
}


/// 아래 도구 줄: 왼쪽에 도구(여러 마리 찾기, 손전등), 가운데 셔터, 오른쪽에 전면/후면 전환. 반투명 검정 바탕.
class _ControlBar extends StatelessWidget {
  const _ControlBar({required this.model});
  final AppModel model;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      color: Colors.black.withValues(alpha: 0.45),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      child: Row(children: [
        Expanded(
          child: Row(mainAxisAlignment: MainAxisAlignment.start, children: [
            _ToolButton(
              icon: Icons.grid_view_rounded,
              label: '여러 마리',
              on: model.multi,
              onTap: () => model.setMulti(!model.multi),
            ),
            if (!model.front) ...[
              const SizedBox(width: 14),
              _ToolButton(
                icon: model.torch ? Icons.flashlight_on_rounded : Icons.flashlight_off_rounded,
                label: '손전등',
                on: model.torch,
                onTap: () => model.setTorch(!model.torch),
              ),
            ],
          ]),
        ),
        _Shutter(model: model),
        Expanded(
          child: Row(mainAxisAlignment: MainAxisAlignment.end, children: [
            if (model.hasFront)
              _ToolButton(
                icon: Icons.cameraswitch_rounded,
                label: model.front ? '후면' : '전면',
                on: false,
                onTap: model.switchCamera,
              ),
          ]),
        ),
      ]),
    );
  }
}

/// 도구 줄의 동그란 아이콘 버튼 + 작은 이름. 켜져 있으면 노랗게.
class _ToolButton extends StatelessWidget {
  const _ToolButton({required this.icon, required this.label, required this.on, required this.onTap});
  final IconData icon;
  final String label;
  final bool on;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = on ? Colors.yellowAccent : Colors.white;
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: SizedBox(
        width: 52,
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(shape: BoxShape.circle, color: Colors.white.withValues(alpha: on ? 0.28 : 0.16)),
            child: Icon(icon, color: color, size: 24),
          ),
          const SizedBox(height: 2),
          Text(label, style: TextStyle(fontSize: 10, color: color), maxLines: 1, overflow: TextOverflow.ellipsis),
        ]),
      ),
    );
  }
}

