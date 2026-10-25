import 'package:flutter/material.dart';

import '../app_model.dart';
import '../core/photo.dart';
import 'dex_screen.dart';
import 'widgets.dart';

/// 촬영한 사진을 전체 화면에서 확대·이동하며 보고, 포켓몬을 탭하면 그 둘레만 판별한다.
/// 확대 배율이 클수록 판별 영역이 작아진다 (zoomRatios). 닫으면 마지막으로 고른 영역의 결과를 돌려준다 (없으면 null).
class PhotoZoomPage extends StatefulWidget {
  const PhotoZoomPage({super.key, required this.model, required this.result, this.initial});
  final AppModel model;
  final PhotoResult result;
  final PhotoView? initial; // 결과 화면에서 이미 고른 영역이 있으면 그것부터 보여 준다

  @override
  State<PhotoZoomPage> createState() => _PhotoZoomPageState();
}

class _PhotoZoomPageState extends State<PhotoZoomPage> {
  final _ctl = TransformationController();
  late PhotoView _view = widget.initial ?? widget.result.all;
  PhotoView? _picked; // 이 화면에서 고른 영역의 결과 (닫을 때 돌려준다)
  bool _busy = false;

  static const _colors = [Colors.redAccent, Colors.lightBlueAccent, Colors.greenAccent, Colors.amberAccent, Colors.purpleAccent];

  @override
  void dispose() {
    _ctl.dispose();
    super.dispose();
  }

  Future<void> _pick(Offset p) async {
    if (_busy) return;
    final zoom = _ctl.value.getMaxScaleOnAxis();
    setState(() => _busy = true);
    try {
      final v = await widget.model.analyzePhotoRegion(widget.result, p, zoom: zoom);
      if (v != null && mounted) {
        setState(() {
          _view = v;
          _picked = v;
        });
      }
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('판별하지 못했어요: $e')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final dex = widget.model.dex!;
    final r = widget.result;
    final v = _view;
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) Navigator.of(context).pop(_picked);
      },
      child: Scaffold(
        backgroundColor: Colors.black,
        appBar: AppBar(
          backgroundColor: Colors.black,
          foregroundColor: Colors.white,
          title: const Text('확대해서 판별', style: TextStyle(fontSize: 16)),
          leading: IconButton(icon: const Icon(Icons.close), onPressed: () => Navigator.of(context).pop(_picked)),
          actions: [
            IconButton(
              tooltip: '원래 크기',
              icon: const Icon(Icons.zoom_out_map),
              onPressed: () => _ctl.value = Matrix4.identity(),
            ),
          ],
        ),
        body: Column(children: [
          Expanded(
            child: ClipRect(
              child: InteractiveViewer(
                transformationController: _ctl,
                minScale: 1,
                maxScale: 8,
                child: Center(
                  child: ListenableBuilder(
                    listenable: _ctl,
                    builder: (context, _) => PhotoBoxes(
                      preview: r.preview,
                      width: r.width,
                      height: r.height,
                      lineScale: _ctl.value.getMaxScaleOnAxis(),
                      onTap: _pick,
                      boxes: [
                        if (v.region != null) PhotoBox(v.region!, '선택한 영역', Colors.white),
                        for (var i = 0; i < v.judged.length; i++)
                          PhotoBox(v.tiles[v.judged[i].tile], dex.entry(v.judged[i].id)?.name ?? v.judged[i].id, _colors[i % _colors.length]),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
          Container(
            width: double.infinity,
            color: const Color(0xFF111111),
            padding: const EdgeInsets.fromLTRB(16, 10, 16, 8),
            child: SafeArea(
              top: false,
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
                Row(children: [
                  if (_busy) ...[
                    const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2)),
                    const SizedBox(width: 8),
                    const Text('판별하는 중…', style: TextStyle(color: Colors.white70, fontSize: 12)),
                  ] else
                    const Expanded(
                      child: Text('손가락으로 확대하고, 포켓몬을 눌러 보세요. 확대할수록 더 작은 영역만 판별해요.',
                          style: TextStyle(color: Colors.white54, fontSize: 12)),
                    ),
                ]),
                const SizedBox(height: 8),
                if (v.judged.isNotEmpty)
                  Wrap(spacing: 6, runSpacing: 4, children: [
                    for (var i = 0; i < v.judged.length; i++)
                      if (dex.entry(v.judged[i].id) case final e?)
                        ActionChip(
                          avatar: SizedBox(width: 20, height: 20, child: PokeThumb(dex: dex, entry: e, size: 20)),
                          label: Text('${e.name} ${(v.judged[i].prob * 100).toStringAsFixed(0)}%'),
                          side: BorderSide(color: _colors[i % _colors.length]),
                          onPressed: () => openDetail(context, widget.model, e.id),
                        ),
                  ])
                else
                  const Text('확실하게 판별하지 못했어요', style: TextStyle(color: Colors.white, fontSize: 13)),
                const SizedBox(height: 6),
                Text(
                  '가까운 후보  ${[
                    for (var i = 0; i < v.top3.length; i++)
                      '${i + 1}. ${dex.entry(v.top3[i].id)?.name ?? v.top3[i].id} ${(v.top3[i].prob * 100).toStringAsFixed(v.top3[i].prob < 0.1 ? 1 : 0)}%',
                  ].join('   ')}',
                  style: const TextStyle(color: Colors.white70, fontSize: 12),
                ),
              ]),
            ),
          ),
        ]),
      ),
    );
  }
}
