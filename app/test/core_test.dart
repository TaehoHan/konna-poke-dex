import 'dart:typed_data';

import 'package:camera/camera.dart';
import 'package:camera_platform_interface/camera_platform_interface.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:konnadex/app_model.dart' show CamRes, availableCamRes;
import 'package:konnadex/core/camera_hw.dart';
import 'package:konnadex/core/classifier.dart';
import 'package:image/image.dart' as img;
import 'package:konnadex/core/dex.dart';
import 'package:konnadex/core/photo.dart';
import 'package:konnadex/ui/camera_screen.dart' show RegionFrame;
import 'package:flutter/material.dart';
import 'package:konnadex/core/preprocess.dart';
import 'package:konnadex/core/recognizer.dart';
import 'package:konnadex/ui/widgets.dart';

/// 똑바로 선 장면: 위쪽 빨강 성분, 왼쪽 초록 성분이 커지는 그라데이션 + 오른쪽 아래 파란 네모.
List<int> scene(double ux, double uy, double size) {
  final r = (uy / size * 255).round();
  final g = (ux / size * 255).round();
  final b = (ux > size * .6 && uy > size * .6) ? 255 : 0;
  return [r, g, b];
}

CameraImage rawBgra(int w, int h, int rotation, double sceneSize, double offX, double offY) {
  final bytes = Uint8List(w * h * 4);
  for (var sy = 0; sy < h; sy++) {
    for (var sx = 0; sx < w; sx++) {
      // 장면 좌표 -> 원본 버퍼 좌표 (preprocess 의 역변환). 장면은 가운데 정사각형 영역에만 있다.
      final lx = sx - offX, ly = sy - offY; // 영역 안 좌표
      double ux, uy;
      switch (rotation) {
        case 90:
          ux = sceneSize - ly;
          uy = lx;
        case 180:
          ux = sceneSize - lx;
          uy = sceneSize - ly;
        case 270:
          ux = ly;
          uy = sceneSize - lx;
        default:
          ux = lx;
          uy = ly;
      }
      final c = scene(ux, uy, sceneSize);
      final o = (sy * w + sx) * 4;
      bytes[o] = c[2];
      bytes[o + 1] = c[1];
      bytes[o + 2] = c[0];
      bytes[o + 3] = 255;
    }
  }
  return CameraImage.fromPlatformInterface(
    CameraImageData(
      format: const CameraImageFormat(ImageFormatGroup.bgra8888, raw: 0),
      planes: [CameraImagePlane(bytes: bytes, bytesPerRow: w * 4, bytesPerPixel: 4)],
      width: w,
      height: h,
    ),
  );
}

void main() {
  catalogTests();
  for (final rot in [0, 90, 180, 270]) {
    test('preprocess 가운데 자르기 rotation=$rot 은 똑바로 선 장면과 일치', () {
      const w = 640, h = 360, s = 360.0;
      final img = rawBgra(w, h, rot, s, (w - s) / 2, 0);
      final out = preprocess(img, rotation: rot, mode: CropMode.centerCrop);
      expect(out.length, 3 * 224 * 224);
      var err = 0.0;
      for (var oy = 0; oy < 224; oy++) {
        for (var ox = 0; ox < 224; ox++) {
          final e = scene((ox + .5) / 224 * s, (oy + .5) / 224 * s, s);
          final p = oy * 224 + ox;
          err +=
              (out[p] * 255 - e[0]).abs() +
              (out[224 * 224 + p] * 255 - e[1]).abs() +
              (out[2 * 224 * 224 + p] * 255 - e[2]).abs();
        }
      }
      final mae = err / (224 * 224 * 3);
      expect(mae, lessThan(4.0), reason: 'MAE=$mae (경계의 파란 네모 때문에 조금 남는다)');
    });
  }

  // 여러 마리 찾기: 똑바로 선 프레임 전체가 그라데이션일 때, 어떤 회전에서도 타일 결과가 그 영역의 그라데이션과 같아야 한다.
  for (final rot in [0, 90, 180, 270]) {
    test('preprocess 타일 rotation=$rot 은 똑바로 선 프레임의 같은 영역과 일치', () {
      const rw = 640, rh = 360; // 원본 버퍼 (센서 방향)
      final swap = rot == 90 || rot == 270;
      final uw = swap ? rh : rw, uh = swap ? rw : rh; // 똑바로 세운 프레임 크기
      double sceneR(double ux, double uy) => ux / uw * 255;
      double sceneG(double ux, double uy) => uy / uh * 255;
      final bytes = Uint8List(rw * rh * 4);
      for (var sy = 0; sy < rh; sy++) {
        for (var sx = 0; sx < rw; sx++) {
          // 원본 좌표 -> 똑바로 선 좌표 (preprocess 의 역변환, 전체 프레임 기준)
          double ux, uy;
          switch (rot) {
            case 90:
              ux = rh - sy.toDouble();
              uy = sx.toDouble();
            case 180:
              ux = rw - sx.toDouble();
              uy = rh - sy.toDouble();
            case 270:
              ux = sy.toDouble();
              uy = rw - sx.toDouble();
            default:
              ux = sx.toDouble();
              uy = sy.toDouble();
          }
          final o = (sy * rw + sx) * 4;
          bytes[o] = 0;
          bytes[o + 1] = sceneG(ux, uy).clamp(0, 255).round();
          bytes[o + 2] = sceneR(ux, uy).clamp(0, 255).round();
          bytes[o + 3] = 255;
        }
      }
      final img = CameraImage.fromPlatformInterface(CameraImageData(
        format: const CameraImageFormat(ImageFormatGroup.bgra8888, raw: 0),
        planes: [CameraImagePlane(bytes: bytes, bytesPerRow: rw * 4, bytesPerPixel: 4)],
        width: rw,
        height: rh,
      ));
      final tiles = frameTiles(uw, uh);
      expect(tiles.length, greaterThan(2));
      for (final t in [tiles.first, tiles[1], tiles.last]) {
        final out = preprocess(img, rotation: rot, mode: CropMode.centerCrop, tile: t);
        var err = 0.0;
        for (var oy = 0; oy < 224; oy++) {
          for (var ox = 0; ox < 224; ox++) {
            final ux = t.x + (ox + .5) / 224 * t.side, uy = t.y + (oy + .5) / 224 * t.side;
            final p = oy * 224 + ox;
            err += (out[p] * 255 - sceneR(ux, uy)).abs() + (out[224 * 224 + p] * 255 - sceneG(ux, uy)).abs();
          }
        }
        expect(err / (224 * 224 * 2), lessThan(3.0), reason: 'rot=$rot tile=(${t.x},${t.y},${t.side}) MAE=${err / (224 * 224 * 2)}');
      }
    });
  }

  test('frameTiles: 가운데가 첫째, 모두 프레임 안, 가장자리를 덮는다', () {
    for (final (uw, uh) in [(720, 1280), (1280, 720), (500, 500)]) {
      final ts = frameTiles(uw, uh);
      expect(ts.first.side, uw < uh ? uw : uh);
      for (final t in ts) {
        expect(t.x, greaterThanOrEqualTo(0));
        expect(t.y, greaterThanOrEqualTo(0));
        expect(t.x + t.side, lessThanOrEqualTo(uw));
        expect(t.y + t.side, lessThanOrEqualTo(uh));
      }
      expect(ts.skip(1).map((t) => t.y + t.side).reduce((a, b) => a > b ? a : b), uh);
      expect(ts.skip(1).map((t) => t.x + t.side).reduce((a, b) => a > b ? a : b), uw);
    }
  });

  test('bestPerSpecies: none 을 빼고 종별 최고 확률 내림차순, none 인덱스는 라벨에서 찾는다', () {
    const labels = ['abra', 'none', 'zubat', 'pikachu'];
    final c = bestPerSpecies([
      [0.1, 0.8, 0.05, 0.05], // 영역 0: 대부분 none
      [0.02, 0.1, 0.08, 0.80], // 영역 1: pikachu
      [0.30, 0.2, 0.40, 0.10], // 영역 2
    ], labels);
    expect(c.map((x) => x.id), ['pikachu', 'zubat', 'abra']); // none 없음
    expect(c.first.prob, 0.80);
    expect(c.first.tile, 1);
    expect(c.first.noneProb, 0.1); // 1위가 나온 영역의 none 확률
    expect(c[1].tile, 2);
  });

  test('prepPhoto: JPEG → 가운데 + 타일 입력들 (크기·개수), 큰 사진은 줄인다', () {
    final im = img.Image(width: 1600, height: 1200); // 가로 사진
    for (var y = 0; y < im.height; y++) {
      for (var x = 0; x < im.width; x++) {
        im.setPixelRgb(x, y, x * 255 ~/ im.width, y * 255 ~/ im.height, 0);
      }
    }
    final prep = prepPhoto(PhotoJob(Uint8List.fromList(img.encodeJpg(im, quality: 95)), 224, maxShort: 600));
    expect(prep.height, 600);
    expect(prep.width, 800);
    expect((prep.srcWidth, prep.srcHeight), (1600, 1200)); // 줄이기 전 원본 크기
    expect(prep.tiles.length, prep.inputs.length);
    expect(prep.tiles.length, greaterThan(4));
    expect(prep.tiles.first.side, 600); // 가운데 정사각형
    for (final t in prep.inputs) {
      expect(t.length, 3 * 224 * 224);
    }
    // 가운데 입력의 왼쪽 위 R 은 작고(그라데이션 시작), 오른쪽 위 R 은 크다
    final c = prep.inputs.first;
    expect(c[2], lessThan(c[221]));
  });

  testWidgets('PhotoBoxes: 사진 위에 영역 상자를 그려도 위젯 오류가 없다 (릴리스에서 회색 상자가 되던 문제)', (tester) async {
    final png = Uint8List.fromList(img.encodePng(img.Image(width: 9, height: 16)));
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: Center(
          child: SizedBox(
            width: 300,
            height: 400,
            child: PhotoBoxes(
              preview: png,
              width: 720,
              height: 1280,
              boxes: const [PhotoBox(Tile(0, 100, 500), '프리져', Colors.red), PhotoBox(Tile(200, 700, 360), '팽도리', Colors.blue)],
            ),
          ),
        ),
      ),
    ));
    expect(tester.takeException(), isNull);
    expect(find.text('프리져'), findsOneWidget);
    expect(find.text('팽도리'), findsOneWidget);
  });

  test('focusTiles: 탭한 지점이 가운데, 크기 55/40/28%, 가장자리에서는 프레임 안으로 밀린다', () {
    final ts = focusTiles(720, 1280, 0.5, 0.5);
    expect(ts.map((t) => t.side), [396, 288, 202]);
    for (final t in ts) {
      expect(t.x + t.side / 2, closeTo(360, 1));
      expect(t.y + t.side / 2, closeTo(640, 1));
    }
    final corner = focusTiles(720, 1280, 0.0, 0.0);
    expect([for (final t in corner) (t.x, t.y)], [(0, 0), (0, 0), (0, 0)]);
    final br = focusTiles(720, 1280, 1.0, 1.0);
    for (final t in br) {
      expect(t.x + t.side, 720);
      expect(t.y + t.side, 1280);
    }
  });

  test('bestPerSpecies only: 일부 영역만 쓰고 tile 은 원래 인덱스', () {
    const labels = ['abra', 'none', 'zubat'];
    final c = bestPerSpecies([
      [0.9, 0.05, 0.05], // 영역 0 (제외)
      [0.1, 0.1, 0.8], // 영역 1
      [0.3, 0.2, 0.5], // 영역 2
    ], labels, only: [1, 2]);
    expect(c.map((x) => x.id), ['zubat', 'abra']);
    expect(c.first.tile, 1);
    expect(c[1].prob, 0.3);
  });

  testWidgets('RegionFrame: 알약(✕)을 눌러야 해제되고, 뒤의 화면 탭으로는 새지 않는다', (tester) async {
    var cleared = 0, behind = 0;
    for (final below in [true, false]) {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: GestureDetector(
            behavior: HitTestBehavior.opaque, // 카메라 미리보기 (탭하면 영역이 옮겨지는 곳)
            onTap: () => behind++,
            child: Stack(children: [
              Positioned(
                left: 50,
                top: 100,
                width: 200,
                height: 200 + RegionFrame.pillSpace,
                child: RegionFrame(side: 200, pillBelow: below, onClear: () => cleared++, animate: false),
              ),
            ]),
          ),
        ),
      ));
      await tester.tap(find.byIcon(Icons.close_rounded));
      await tester.pump();
    }
    expect(cleared, 2);
    expect(behind, 0);
  });

  test('zoomRatios: 확대할수록 판별 영역이 작아지고 최소 64px 아래로는 안 내려간다', () {
    expect(zoomRatios(720, 1), [0.55, 0.4, 0.28]);
    final z4 = zoomRatios(720, 4);
    expect(z4[0], closeTo(0.1375, 1e-9));
    expect(z4[2], closeTo(64 / 720, 1e-9)); // 0.07 → 64px 바닥
    for (final r in zoomRatios(720, 100)) {
      expect(r * 720, greaterThanOrEqualTo(63.9));
    }
    expect(zoomRatios(720, 0.5), [0.55, 0.4, 0.28]); // 축소는 1배로 본다
  });

  test('prepFocusOnly: 확대 배율에 따라 선택 영역 타일이 작아진다', () {
    final im = img.Image(width: 720, height: 1280);
    final jpg = Uint8List.fromList(img.encodeJpg(im));
    final a = prepFocusOnly(PhotoJob(jpg, 224, focusX: 0.5, focusY: 0.5));
    final b = prepFocusOnly(PhotoJob(jpg, 224, focusX: 0.5, focusY: 0.5, zoom: 4));
    expect(a.tiles.first.side, 396);
    expect(b.tiles.first.side, lessThan(a.tiles.first.side));
    expect(b.inputs.first.length, 3 * 224 * 224);
  });

  test('availableCamRes: 확인 전에는 전부, 확인 뒤에는 지원하고 크기가 겹치지 않는 단계만', () {
    expect(availableCamRes({}), CamRes.values);
    expect(
      availableCamRes({'hd': '1280×720', 'fhd': '1920×1080', 'uhd': '3840×2160', 'max': '4032×3024'}),
      [CamRes.hd, CamRes.fhd, CamRes.uhd, CamRes.max],
    );
    // 4K 를 지원하지 않아 high/veryHigh 와 같은 크기로 떨어지는 기기 → 겹치는 단계는 뺀다
    expect(availableCamRes({'hd': '1280×720', 'fhd': '1920×1080', 'uhd': '1920×1080', 'max': '4032×3024'}), [CamRes.hd, CamRes.fhd, CamRes.max]);
    // 확인에 실패한 단계(키 없음)는 뺀다
    expect(availableCamRes({'hd': '1280×720', 'fhd': '1920×1080'}), [CamRes.hd, CamRes.fhd]);
  });

  test('resolutionsFromHw: 하드웨어가 알려 준 크기로 해상도 단계를 정한다', () {
    HwSizes hw(List<String> v, List<String> p) => HwSizes(HwSizes.parse(v), HwSizes.parse(p));
    // 4K 영상과 12MP 사진을 지원하는 폰 (세로로 적혀 있어도 큰 쪽을 가로로 본다)
    expect(
      resolutionsFromHw(hw(['1280x720', '1920x1080', '2160x3840', '3840x2160', '4032x3024'], ['3024x4032', '4032x3024'])),
      {'hd': '1280×720', 'fhd': '1920×1080', 'uhd': '3840×2160', 'max': '4032×3024'},
    );
    // 4K 영상이 없는 폰 → uhd 는 빠진다
    expect(resolutionsFromHw(hw(['640x480', '1280x720', '1920x1080'], ['4000x3000'])), {'hd': '1280×720', 'fhd': '1920×1080', 'max': '4000×3000'});
    // 사진 크기를 못 받으면 영상 최대로 max 를 정한다
    expect(resolutionsFromHw(hw(['1280x720', '1920x1080'], [])), {'hd': '1280×720', 'fhd': '1920×1080', 'max': '1920×1080'});
    expect(resolutionsFromHw(hw([], [])), isEmpty);
    expect(HwSizes.parse(['abc', '100x50']).length, 1);
  });

  test('keepWords: 공백에서만 줄바꿈', () {
    expect(keepWords('색이 다른 모습'), '색\u2060이 다\u2060른 모\u2060습');
  });

  test('Recognizer: 임계값 이상·none 아님·같은 종 연속 5프레임일 때만 인식, 확신도는 원시 평균', () {
    final r = Recognizer()..threshold = 0.7;
    List<List<Prediction>> one(Prediction p) => [[p]];
    for (var i = 0; i < 4; i++) {
      expect(r.update(one(const Prediction('a', 0.8))).state, RecState.candidate);
    }
    final rec = r.update(one(const Prediction('a', 0.9)));
    expect(rec.state, RecState.recognized);
    expect(rec.prediction!.confidence, closeTo((0.8 * 4 + 0.9) / 5, 1e-9));
    expect(r.update(one(const Prediction('a', 0.5))).state, RecState.unknown); // 임계값 미만이면 바로 풀린다
    for (var i = 0; i < 4; i++) {
      r.update(one(const Prediction('a', 0.9)));
    }
    expect(r.update(one(const Prediction('b', 0.9))).state, RecState.candidate); // 다른 종이 끼면 처음부터
    expect(r.update(one(const Prediction('none', 0.99))).state, RecState.unknown);
  });

  test('Recognizer: 영역이 여러 개면 종마다 따로 세어 여러 마리를 동시에 인식', () {
    final r = Recognizer()..threshold = 0.7;
    Recognition last = const Recognition(RecState.searching);
    for (var i = 0; i < 5; i++) {
      last = r.update([
        [const Prediction('a', 0.9)], // 가운데 영역
        [const Prediction('b', 0.8), const Prediction('c', 0.1)], // 타일
        [const Prediction('a', 0.95)], // 같은 종이 겹친 타일에도 있다
        [const Prediction('none', 0.99)],
        [const Prediction('d', 0.4)], // 임계값 미만
      ]);
    }
    expect(last.state, RecState.recognized);
    expect(last.all.map((p) => p.id), ['a', 'b']); // 확신도 순 (a: 최고 0.95/0.9 평균, b: 0.8)
    expect(last.all.first.confidence, closeTo(0.95, 1e-9)); // 한 프레임에서는 영역 중 최고 확률을 쓴다
    // b 가 한 프레임 빠지면 b 만 처음부터, a 는 계속
    last = r.update([[const Prediction('a', 0.9)]]);
    expect(last.all.map((p) => p.id), ['a']);
  });
}

void catalogTests() {
  group('Dex', () {
    const json = '''{"version":1,"species":[
      {"id":"ivysaur","no":2,"gen":1,"name":"이상해풀","types":["grass","poison"],"types_ko":["풀","독"],"h":1.0,"w":13.0,
       "flavor":[{"v":"X","t":"a"}],"evo":["bulbasaur","ivysaur"]},
      {"id":"bulbasaur","no":1,"gen":1,"name":"이상해씨","genus":"씨앗포켓몬","types":["grass"],"types_ko":["풀"],"flavor":[],"evo":[]},
      {"id":"sprigatito","no":906,"gen":9,"name":"나오하","flavor":[{"v":"스칼렛","t":"b","tr":true}],"evo":[]},
      {"id":"zz","no":2000,"name":"빈","flavor":[],"evo":[]}],
      "forms":{"vulpix__alola":"알로라의 모습"}}''';
    final d = Dex.parse(json);
    final dex = Dex(d.entries, formNames: d.formNames, audioIds: {'bulbasaur'}, thumbIds: const {});
    test('번호순 정렬, 조회, 세대', () {
      expect(dex.entries.map((e) => e.id), ['bulbasaur', 'ivysaur', 'sprigatito', 'zz']);
      expect(dex.entry('zzz'), isNull);
      expect(dex.entry('ivysaur')!.noLabel, 'No.0002');
      expect(dex.generations, [1, 9]);
      expect(dex.entry('zz')!.flavors, isEmpty);
      expect(dex.entry('sprigatito')!.flavors.single.translated, isTrue);
      expect(dex.entry('sprigatito')!.flavors.single.versions, '스칼렛'); // 번역이어도 화면 표시는 버전 이름 그대로
      expect(dex.entry('ivysaur')!.flavors.single.translated, isFalse);
    });
    test('성비 표시', () {
      String? lab(int? g) => DexEntry(id: 'x', no: 1, name: 'x', genderRate: g).genderLabel;
      expect(lab(1), '♂ 87.5% · ♀ 12.5%');
      expect(lab(4), '♂ 50% · ♀ 50%');
      expect(lab(0), '♂ 100%');
      expect(lab(8), '♀ 100%');
      expect(lab(-1), '성별 없음');
      expect(lab(null), isNull);
    });
    test('폼 설명: 고른 모습의 설명만, 없으면 기본', () {
      const fs = [Flavor('X', 'a', gen: 6), Flavor('Z-A', 'b', gen: 9), Flavor('Z-A', 'm', gen: 9, form: 'venusaur__mega')];
      expect(flavorsFor(fs, null).map((f) => f.text), ['a', 'b']);
      expect(flavorsFor(fs, 'venusaur__mega').map((f) => f.text), ['m']);
      expect(flavorsFor(fs, 'venusaur__shiny').map((f) => f.text), ['a', 'b']);
    });
    test('도감 설명 세대별 묶음', () {
      const fs = [Flavor('소드', 'a', gen: 8), Flavor('X', 'b', gen: 6), Flavor('?', 'c'), Flavor('Y', 'd', gen: 6)];
      final m = flavorsByGen(fs);
      expect(m.keys, [0, 6, 8]);
      expect(m[6]!.map((f) => f.versions), ['X', 'Y']);
      expect(dex.entry('ivysaur')!.heightM, 1.0);
    });
    test('폼 이름, 음성 유무', () {
      expect(dex.formName('vulpix'), isNull);
      expect(dex.formName('vulpix__alola'), '알로라의 모습');
      expect(dex.formName('raichu__alola'), 'alola');
      expect(dex.hasAudio('bulbasaur'), isTrue);
      expect(dex.hasAudio('ivysaur'), isFalse);
      expect(dex.hasThumb('bulbasaur'), isFalse);
    });
  });

  group('분류 보조', () {
    test('메타데이터 imgsz, names', () {
      expect(parseImgsz('[256, 256]'), 256);
      expect(parseImgsz(null), isNull);
      expect(parseNames("{0: 'abra', 1: 'none', 2: \"mr_mime\"}"), ['abra', 'none', 'mr_mime']);
      expect(parseNames('없음'), isNull);
    });
    test('toProbs: 확률은 그대로, 로짓은 softmax', () {
      expect(toProbs([0.2, 0.8]), [0.2, 0.8]);
      final p = toProbs([1.0, 1.0, -3.0]);
      expect(p.fold(0.0, (a, b) => a + b), closeTo(1, 1e-9));
      expect(p[0], closeTo(p[1], 1e-12));
    });
    test('none 인덱스를 가정하지 않는다', () {
      final preds = topK([0.1, 0.7, 0.2], ['abra', 'none', 'zubat']);
      expect(preds.first.isNone, isTrue);
    });
    test('폼 마스크: 고른 종의 폼만 남겨 다시 정규화', () {
      const labels = ['charizard', 'charizard__mega_x', 'charizard__mega_y', 'vulpix', 'vulpix__alola'];
      final t = FormTable(labels);
      expect(t.hasForms('vulpix'), isTrue);
      expect(t.hasForms('pikachu'), isFalse);
      // 전체 argmax 는 charizard__mega_x 지만 vulpix 의 폼만 본다
      final p = t.pick('vulpix', [0.05, 0.6, 0.05, 0.1, 0.2], labels)!;
      expect(p.id, 'vulpix__alola');
      expect(p.confidence, closeTo(0.2 / 0.3, 1e-9));
      expect(t.pick('pikachu', [0.2, 0.2, 0.2, 0.2, 0.2], labels), isNull);
      final top = t.top('charizard', [0.3, 0.1, 0.1, 0.25, 0.25], labels);
      expect(top.map((p) => p.id), ['charizard', 'charizard__mega_x']);
      expect(top.first.confidence, closeTo(0.6, 1e-9));
    });
    test('FormVoter: 다수결, 종이 바뀌면 초기화', () {
      final v = FormVoter();
      v.add('vulpix', const Prediction('vulpix__alola', 0.9));
      v.add('vulpix', const Prediction('vulpix', 0.6));
      v.add('vulpix', const Prediction('vulpix__alola', 0.7));
      expect(v.current('vulpix')!.id, 'vulpix__alola');
      expect(v.current('vulpix')!.confidence, closeTo(0.8, 1e-9));
      v.add('raichu', const Prediction('raichu', 0.9));
      expect(v.current('vulpix'), isNull);
    });
  });
}
