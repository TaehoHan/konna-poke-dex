import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show Uint8List;

import '../core/dex.dart';
import '../core/preprocess.dart' show Tile;

/// 타입 색 (영문 타입 키 기준, 게임에서 흔히 쓰는 색에 가깝게).
const typeColors = <String, Color>{
  'normal': Color(0xFF9FA19F),
  'fire': Color(0xFFE62829),
  'water': Color(0xFF2980EF),
  'grass': Color(0xFF3FA129),
  'electric': Color(0xFFFAC000),
  'ice': Color(0xFF3DCEF3),
  'fighting': Color(0xFFFF8000),
  'poison': Color(0xFF9141CB),
  'ground': Color(0xFF915121),
  'flying': Color(0xFF81B9EF),
  'psychic': Color(0xFFEF4179),
  'bug': Color(0xFF91A119),
  'rock': Color(0xFFAFA981),
  'ghost': Color(0xFF704170),
  'dragon': Color(0xFF5060E1),
  'dark': Color(0xFF624D4E),
  'steel': Color(0xFF60A1B8),
  'fairy': Color(0xFFEF70EF),
};

class TypeChips extends StatelessWidget {
  const TypeChips(this.entry, {super.key, this.small = false});
  final DexEntry entry;
  final bool small;

  @override
  Widget build(BuildContext context) {
    return Wrap(spacing: 6, runSpacing: 4, children: [
      for (var i = 0; i < entry.typesKo.length; i++)
        Container(
          padding: EdgeInsets.symmetric(horizontal: small ? 8 : 12, vertical: small ? 2 : 4),
          decoration: BoxDecoration(
            color: typeColors[i < entry.types.length ? entry.types[i] : ''] ?? Colors.grey,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Text(entry.typesKo[i],
              style: TextStyle(color: Colors.white, fontSize: small ? 11 : 13, fontWeight: FontWeight.bold)),
        ),
    ]);
  }
}

/// 썸네일. 없으면(아직 만들지 않음) 번호 자리표시를 보여 준다.
class PokeThumb extends StatelessWidget {
  const PokeThumb({super.key, required this.dex, required this.entry, this.formId, this.size});
  final Dex dex;
  final DexEntry entry;
  final String? formId; // 폼 썸네일이 있으면 그것을 쓴다
  final double? size;

  @override
  Widget build(BuildContext context) {
    final id = (formId != null && formId != entry.id && dex.hasThumb(formId!)) ? formId! : entry.id;
    final placeholder = _Placeholder(entry: entry, size: size);
    if (!dex.hasThumb(id)) return placeholder;
    return Image.asset('assets/thumbs/$id.png',
        width: size, height: size, fit: BoxFit.contain, errorBuilder: (_, _, _) => placeholder);
  }
}

class _Placeholder extends StatelessWidget {
  const _Placeholder({required this.entry, this.size});
  final DexEntry entry;
  final double? size;

  @override
  Widget build(BuildContext context) {
    final c = typeColors[entry.types.isEmpty ? '' : entry.types.first] ?? Colors.grey;
    return SizedBox(
      width: size,
      height: size,
      child: LayoutBuilder(builder: (context, box) {
        final s = box.biggest.shortestSide.isFinite ? box.biggest.shortestSide : 72.0;
        return Center(
          child: Container(
            width: s * 0.8,
            height: s * 0.8,
            alignment: Alignment.center,
            decoration: BoxDecoration(color: c.withValues(alpha: 0.18), shape: BoxShape.circle),
            child: Text('${entry.no}', style: TextStyle(fontSize: s * 0.22, fontWeight: FontWeight.bold, color: c)),
          ),
        );
      }),
    );
  }
}

/// 한글이 글자 단위로 줄바꿈되지 않게 단어 안 글자 사이에 word joiner(U+2060)를 넣는다 (공백에서만 줄이 바뀐다).
String keepWords(String s) => s.split(' ').map((w) => w.split('').join('\u2060')).join(' ');

/// 판별된 영역 상자 하나 (똑바로 세운 사진의 픽셀 좌표).
class PhotoBox {
  const PhotoBox(this.tile, this.label, this.color);
  final Tile tile;
  final String label;
  final Color color;
}

/// 사진 위에 판별된 영역 상자를 그린다. Positioned 는 반드시 Stack 의 직접 자식이어야 하므로
/// LayoutBuilder 가 Stack 바깥에 있다 (안에 두면 "Incorrect use of ParentDataWidget" 으로 릴리스 빌드에서 회색 상자가 된다).
class PhotoBoxes extends StatelessWidget {
  const PhotoBoxes({super.key, required this.preview, required this.width, required this.height, required this.boxes, this.onTap, this.lineScale = 1});
  final Uint8List preview;
  final int width, height;
  final List<PhotoBox> boxes;
  final ValueChanged<Offset>? onTap; // 사진을 탭한 곳 (사진 기준 0..1)
  final double lineScale; // 사진을 이만큼 확대해 보여 주는 중이면 상자 선·글자를 1/lineScale 로 줄여 화면에서 같은 굵기로 보이게 한다

  @override
  Widget build(BuildContext context) {
    return AspectRatio(
      aspectRatio: width / height,
      child: LayoutBuilder(builder: (context, c) {
        final sx = c.maxWidth / width, sy = c.maxHeight / height;
        final stack = Stack(fit: StackFit.expand, children: [
          ClipRRect(borderRadius: BorderRadius.circular(12), child: Image.memory(preview, fit: BoxFit.fill, gaplessPlayback: true)),
          for (final b in boxes)
            Positioned(
              left: b.tile.x * sx,
              top: b.tile.y * sy,
              width: b.tile.side * sx,
              height: b.tile.side * sy,
              child: IgnorePointer(
                child: Container(
                  decoration: BoxDecoration(border: Border.all(color: b.color, width: (3 / lineScale).clamp(0.3, 3.0)), borderRadius: BorderRadius.circular(8)),
                  alignment: Alignment.topLeft,
                  child: Container(
                    color: b.color,
                    padding: EdgeInsets.symmetric(horizontal: (6 / lineScale).clamp(1.0, 6.0)),
                    child: Text(b.label, style: TextStyle(fontSize: (12 / lineScale).clamp(2.0, 12.0), color: Colors.black)),
                  ),
                ),
              ),
            ),
        ]);
        if (onTap == null) return stack;
        return GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTapUp: (d) => onTap!(Offset(d.localPosition.dx / c.maxWidth, d.localPosition.dy / c.maxHeight)),
          child: stack,
        );
      }),
    );
  }
}
