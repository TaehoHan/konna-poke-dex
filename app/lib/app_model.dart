import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;
import 'dart:ui' as ui;
import 'dart:io';

import 'package:audioplayers/audioplayers.dart';
import 'package:camera/camera.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'core/dex.dart';
import 'core/photo.dart';
import 'core/camera_hw.dart';
import 'core/capture.dart';
import 'core/classifier.dart';
import 'core/preprocess.dart';
import 'core/recognizer.dart';

/// 카메라 해상도 (영상 인식 프레임과 정지 사진이 같은 해상도다). 높을수록 사진 확대·작은 포켓몬에 유리하지만 영상 처리가 느려질 수 있다.
enum CamRes {
  hd('720p', '1280×720', ResolutionPreset.high),
  fhd('1080p', '1920×1080', ResolutionPreset.veryHigh),
  uhd('4K', '3840×2160', ResolutionPreset.ultraHigh),
  max('최대', '기기 최대', ResolutionPreset.max);

  const CamRes(this.label, this.size, this.preset);
  final String label;
  final String size;
  final ResolutionPreset preset;
}

/// 확인한 해상도 [sizes] (CamRes.name → "3840×2160") 에서 고를 수 있는 단계. 비어 있으면 아직 확인 전이라 전부,
/// 아니면 기기가 실제로 지원하고(확인 결과가 있고) 앞 단계와 크기가 같지 않은 것만 (예: 이 기기에서 4K 와 최대가 같으면 최대를 뺀다).
List<CamRes> availableCamRes(Map<String, String> sizes) {
  if (sizes.isEmpty) return CamRes.values;
  final seen = <String>{};
  return [
    for (final r in CamRes.values)
      if (sizes[r.name] case final sz? when seen.add(sz)) r,
  ];
}

/// 앱 전체 상태. 카메라 프레임 -> 전처리 -> ① 종 모델 -> 인식 안정화 -> (폼 있는 종이면 ② 폼 모델, 마스크) -> 카드/음성.
/// 모델은 선택이다: ① 이 없으면 도감만, ② 가 없으면 종만 판정한다 (DESIGN.md 2.1, 8절).
class AppModel extends ChangeNotifier {
  Dex? dex;
  Classifier? _species; // ① 종 모델 (1025종 + none)
  Classifier? _forms; // ② 폼 모델 (선택)
  FormTable? _formTable;
  String? loadError; // 도감 DB 를 못 읽음 (앱을 쓸 수 없음)
  String? modelError; // 모델을 못 읽음 (도감은 쓸 수 있음)
  bool loaded = false;

  bool get hasSpeciesModel => _species != null;
  bool get hasFormModel => _forms != null;
  int get speciesClasses => _species?.labels.length ?? 0;
  int get formClasses => _forms?.labels.length ?? 0;
  int get inputSize => _species?.inputSize ?? modelSize;

  // 화면 상태
  Recognition recognition = const Recognition(RecState.searching);
  List<Prediction> top = [];
  List<Prediction> recognized = []; // 지금 인식된 모든 종 (여러 마리면 둘 이상, 확신도 순)
  String? _selectedId; // 사용자가 칩을 눌러 고른 종 (인식 목록에서 사라지면 해제)
  CamRes camRes = CamRes.fhd; // 카메라 해상도. 바꾸면 카메라를 다시 만든다 (setCamRes)
  bool multi = true; // 여러 마리 찾기: 가운데 + 겹치는 타일 여러 곳을 분류한다 (끄면 가운데 한 곳만)
  int tileCount = 1; // 이번 프레임에서 분류한 영역 수 (진단)
  Offset? region; // 사용자가 탭한 영역의 가운데 (미리보기 기준 0..1). 있으면 그 둘레만 판별한다 (null = 화면 전체)
  DexEntry? shown;
  String? shownForm; // 폼 라벨 (`<종>__<폼>` 또는 기본형 종 이름). ② 가 없거나 폼 없는 종이면 null
  double shownConfidence = 0;
  List<Prediction> lastForms = []; // 진단용: 이번 프레임의 ② 상위 2개 (그 종 안에서 다시 정규화한 확률)
  double shownFormConfidence = 0;
  // ② 폼 판정 기준: 다시 정규화한 확률(최근 프레임 다수결 평균)이 이보다 낮으면 기본형으로 보여 준다. 임시값 (form_v1, 2026-10-25), 실제 폼 굿즈로 다시 정한다
  double formThreshold = 0.6;
  static const _formThresholdKey = 'form_threshold';
  double fps = 0;
  double msPerFrame = 0;
  Set<String> discovered = {};
  String? playingId;

  // 설정
  double threshold = 0.7; // 임시값. train_v1 test 기준 0.5 였지만 치약→갈모매 오탐 뒤 올림 (2026-10-25). 실제 촬영으로 다시 정한다 (DESIGN.md 6절)
  static const _thresholdKey = 'threshold_v2'; // 기본값을 바꿔서 옛 저장값(threshold)은 쓰지 않는다
  CropMode cropMode = CropMode.centerCrop;
  bool autoPlay = true;
  bool showDebug = false; // 진단: 모델에 실제로 들어가는 이미지·프레임 정보·상위 3개
  ui.Image? inputPreview;
  String frameInfo = '';

  // 카메라
  CameraController? camera;
  bool cameraDenied = false;
  String? cameraError;
  bool _busy = false;
  bool _streaming = false;
  double zoom = 1.0;
  // 초광각 렌즈는 최단 초점거리가 훨씬 짧다(근접 촬영). 2배 확대하면 일반 렌즈와 화각이 같아서,
  // 있으면 항상 초광각 + 2배로 쓴다 (기본 카메라 앱의 자동 근접 전환 대신).
  bool _useUltra = false;
  double minZoom = 1.0, maxZoom = 1.0;
  Offset? focusPoint; // 화면에 보여 줄 초점 표시 (미리보기 기준 0..1)
  int _sensorOrientation = 90;
  final List<int> _frameTimes = [];
  final Recognizer _recognizer = Recognizer();
  final FormVoter _formVoter = FormVoter();

  // 음성
  final AudioPlayer _player = AudioPlayer();
  final Map<String, DateTime> _lastPlayedAt = {};
  static const _replayCooldown = Duration(seconds: 8);
  SharedPreferences? _prefs;

  Future<void> init() async {
    try {
      _prefs = await SharedPreferences.getInstance();
      threshold = _prefs!.getDouble(_thresholdKey) ?? threshold;
      formThreshold = _prefs!.getDouble(_formThresholdKey) ?? formThreshold;
      multi = _prefs!.getBool('multi') ?? multi;
      try {
        final rs = jsonDecode(_prefs!.getString('resSizes') ?? 'null') as Map<String, dynamic>?;
        if (rs != null) {
          _resCam = rs['cam'] as String?;
          resSizes = Map<String, String>.from(rs['sizes'] as Map);
        }
      } catch (_) {}
      final cr = _prefs!.getString('camRes');
      if (cr != null) camRes = CamRes.values.firstWhere((c) => c.name == cr, orElse: () => camRes);
      final m = _prefs!.getString('cropMode');
      if (m != null) cropMode = CropMode.values.firstWhere((c) => c.name == m, orElse: () => cropMode);
      autoPlay = _prefs!.getBool('autoPlay') ?? autoPlay;
      showDebug = _prefs!.getBool('showDebug') ?? showDebug;
      discovered = (_prefs!.getStringList('discovered') ?? []).toSet();
      _recognizer.threshold = threshold;

      await AudioPlayer.global.setAudioContext(AudioContext(
        iOS: AudioContextIOS(category: AVAudioSessionCategory.playback, options: {AVAudioSessionOptions.mixWithOthers}),
      ));
      _player.onPlayerComplete.listen((_) {
        playingId = null;
        notifyListeners();
      });
      dex = await Dex.load();
      captureCount = await CaptureStore.count();
    } catch (e) {
      loadError = '$e';
    }
    try {
      final assets = (await AssetManifest.loadFromAssetBundle(rootBundle)).listAssets().toSet();
      _species = await Classifier.load(model: 'assets/models/species.onnx', labelsAsset: 'assets/species_labels.txt', assets: assets);
      if (_species != null) {
        _forms = await Classifier.load(model: 'assets/models/forms.onnx', labelsAsset: 'assets/form_labels.txt', assets: assets);
        if (_forms != null) _formTable = FormTable(_forms!.labels);
      }
    } catch (e) {
      modelError = '$e';
    }
    await loadHardwareResolutions();
    try {
      hasFront = (await availableCameras()).any((c) => c.lensDirection == CameraLensDirection.front);
    } catch (_) {}
    loaded = true;
    notifyListeners();
  }

  // ---- 카메라 ----
  /// 쓸 카메라. 후면(기본): 초광각(근접 촬영) → 광각 → 아무 후면 → 아무 카메라. 전면([front]): 전면 카메라.
  /// 근접용 줌 기본값·센서 방향도 여기서 정한다.
  Future<CameraDescription> _pickCamera({bool front = false}) async {
    final cams = await availableCameras();
    final fronts = cams.where((c) => c.lensDirection == CameraLensDirection.front).toList();
    if (front && fronts.isNotEmpty) {
      _useUltra = false;
      _sensorOrientation = fronts.first.sensorOrientation;
      return fronts.first;
    }
    final backs = cams.where((c) => c.lensDirection == CameraLensDirection.back).toList();
    final ultra = backs.where((c) => c.lensType == CameraLensType.ultraWide).toList();
    _useUltra = ultra.isNotEmpty;
    final wide = backs.where((c) => c.lensType == CameraLensType.wide).toList();
    final back = ultra.isNotEmpty ? ultra.first : (wide.isNotEmpty ? wide.first : (backs.isNotEmpty ? backs.first : cams.first));
    _sensorOrientation = back.sensorOrientation;
    return back;
  }

  bool front = false; // 전면 카메라를 쓰는 중 (앱을 켤 때는 항상 후면)
  bool hasFront = true; // 전면 카메라가 있다 (없으면 전환 버튼을 숨긴다)
  bool torch = false; // 손전등(어두운 곳에서 비춤). 전면은 없다

  /// 전면/후면 카메라를 바꾼다: 영상 인식을 멈추고 카메라를 닫았다가 다른 카메라로 다시 연다.
  Future<void> switchCamera() async {
    await stopCamera();
    final old = camera;
    camera = null;
    front = !front;
    torch = false;
    region = null; // 다른 카메라라 선택 영역은 의미가 없다
    notifyListeners();
    try {
      await old?.dispose();
    } catch (_) {}
    await startCamera();
  }

  /// 손전등을 켜고 끈다 (전면 카메라·손전등이 없는 기기에서는 무시).
  Future<void> setTorch(bool on) async {
    final c = camera;
    if (c == null || front) return;
    try {
      await c.setFlashMode(on ? FlashMode.torch : FlashMode.off);
      torch = on;
    } catch (_) {
      torch = false;
    }
    notifyListeners();
  }

  // ---- 지원 해상도 ----
  /// 기기에 직접 물어 확인한 해상도: [CamRes.name] → "3840×2160". 비어 있으면 아직 확인하지 않았다.
  Map<String, String> resSizes = {};
  String? _resCam; // resSizes 를 확인한 카메라 (이름/렌즈)
  bool probing = false;
  bool hwKnown = false; // 하드웨어 정보로 지원 해상도를 알아냈다 (네이티브 채널). 못 알아내면 probeResolutions 로 실제로 열어 본다
  String? hwMaxPhoto; // 이 카메라의 사진 최대 크기 (하드웨어 정보)

  /// 설정에서 고를 수 있는 해상도. 확인 전에는 전부(미확인), 확인 뒤에는 기기가 실제로 지원하고 크기가 겹치지 않는 것만.
  List<CamRes> get availableResolutions => availableCamRes(resSizes);

  /// 하드웨어(iOS AVCaptureDevice.formats, 안드로이드 Camera2)에서 쓰는 카메라의 지원 해상도를 읽는다. 카메라를 열지 않아 바로 끝난다.
  Future<void> loadHardwareResolutions() async {
    try {
      final desc = await _pickCamera();
      final hw = await queryHwSizes(desc.name);
      if (hw == null) return;
      final sizes = resolutionsFromHw(hw);
      if (sizes.isEmpty) return;
      resSizes = sizes;
      _resCam = '${desc.name}/${desc.lensType.name}';
      hwKnown = true;
      final m = hw.maxPhoto;
      hwMaxPhoto = m == null ? null : '${m.w}×${m.h}';
      _prefs?.setString('resSizes', jsonEncode({'cam': _resCam, 'sizes': sizes}));
      final ok = availableResolutions;
      if (ok.isNotEmpty && !ok.contains(camRes)) {
        camRes = ok.contains(CamRes.fhd) ? CamRes.fhd : ok.last;
        _prefs?.setString('camRes', camRes.name);
      }
      notifyListeners();
    } catch (_) {
      // 읽지 못하면 설정의 "열어서 확인" 으로 대신한다
    }
  }

  /// 해상도 단계마다 카메라를 실제로 열어 크기를 읽는다 (카메라 하나만 열 수 있어서 영상 인식은 멈춘다). 3~5초.
  Future<void> probeResolutions() async {
    if (probing || _species == null) return;
    probing = true;
    notifyListeners();
    final found = <String, String>{};
    try {
      await stopCamera();
      final old = camera;
      camera = null;
      notifyListeners();
      try {
        await old?.dispose();
      } catch (_) {}
      final desc = await _pickCamera();
      for (final r in CamRes.values) {
        CameraController? c;
        try {
          c = CameraController(desc, r.preset,
              enableAudio: false, imageFormatGroup: Platform.isIOS ? ImageFormatGroup.bgra8888 : ImageFormatGroup.yuv420);
          await c.initialize();
          final sz = c.value.previewSize;
          if (sz != null) {
            final a = math.max(sz.width, sz.height).toInt(), b = math.min(sz.width, sz.height).toInt();
            found[r.name] = '$a×$b';
          }
        } catch (_) {
          // 이 단계를 지원하지 않는다
        } finally {
          try {
            await c?.dispose();
          } catch (_) {}
        }
      }
      resSizes = found;
      _resCam = '${desc.name}/${desc.lensType.name}';
      _prefs?.setString('resSizes', jsonEncode({'cam': _resCam, 'sizes': found}));
      final ok = availableResolutions;
      if (ok.isNotEmpty && !ok.contains(camRes)) {
        camRes = ok.contains(CamRes.fhd) ? CamRes.fhd : ok.last; // 고른 단계를 지원하지 않으면 1080p(없으면 가장 높은 것)로
        _prefs?.setString('camRes', camRes.name);
      }
    } finally {
      probing = false;
      notifyListeners();
    }
  }

  Future<void> startCamera() async {
    if (_streaming || _species == null) return;
    try {
      if (camera == null) {
        final back = await _pickCamera(front: front);
        final c = CameraController(
          back,
          camRes.preset,
          enableAudio: false,
          imageFormatGroup: Platform.isIOS ? ImageFormatGroup.bgra8888 : ImageFormatGroup.yuv420,
        );
        await c.initialize();
        // 방향은 잠그지 않는다 (가로 모드 허용). iOS 플러그인은 프레임을 기기 방향에 맞게 돌려서 주고,
        // Android 는 센서 방향 그대로 주므로 _rotation() 에서 보정한다.
        // 전면 카메라처럼 초점·노출·줌 일부를 지원하지 않는 카메라는 건너뛴다 (실패해도 카메라는 계속 쓴다)
        try {
          await c.setFocusMode(FocusMode.auto); // 연속 자동 초점
        } catch (_) {}
        try {
          await c.setExposureMode(ExposureMode.auto);
        } catch (_) {}
        try {
          minZoom = await c.getMinZoomLevel();
          maxZoom = await c.getMaxZoomLevel();
        } catch (_) {
          minZoom = maxZoom = 1.0;
        }
        zoom = (_useUltra ? 2.0 : 1.0).clamp(minZoom, maxZoom);
        try {
          await c.setZoomLevel(zoom);
        } catch (_) {}
        camera = c;
      }
      _recognizer.reset();
      _formVoter.reset();
      _streaming = true;
      await camera!.startImageStream(_onFrame);
      cameraError = null;
    } on CameraException catch (e) {
      if (e.code.contains('Denied') || e.code.contains('Restricted')) {
        cameraDenied = true;
      } else {
        cameraError = '${e.code}: ${e.description}';
      }
      camera = null;
      _streaming = false;
    } catch (e) {
      cameraError = '$e';
      camera = null;
      _streaming = false;
    }
    notifyListeners();
  }

  /// 카메라 해상도를 바꾼다: 영상 인식을 멈추고 카메라를 닫는다 (카메라 탭에 돌아오면 새 해상도로 다시 연다).
  Future<void> setCamRes(CamRes r) async {
    if (r == camRes) return;
    camRes = r;
    _prefs?.setString('camRes', r.name);
    await stopCamera();
    final old = camera;
    camera = null; // 화면이 닫히는 컨트롤러를 쓰지 않도록 먼저 비운다
    notifyListeners();
    try {
      await old?.dispose();
    } catch (_) {}
    notifyListeners();
  }

  /// 지금 카메라가 실제로 쓰는 미리보기 크기 (설정 화면 표시용). 카메라가 없으면 null.
  String? get camActualSize {
    final v = camera?.value;
    final sz = v?.previewSize;
    if (v == null || !v.isInitialized || sz == null) return null;
    final a = sz.width >= sz.height ? sz.width : sz.height, b = sz.width >= sz.height ? sz.height : sz.width;
    return '${a.toInt()}×${b.toInt()}';
  }

  Future<void> stopCamera() async {
    if (!_streaming) return;
    _streaming = false;
    try {
      await camera?.stopImageStream();
    } catch (_) {}
    recognition = const Recognition(RecState.searching);
    top = [];
    notifyListeners();
  }

  /// 프레임을 똑바로 세우려고 돌려야 하는 각도.
  int _rotation() {
    if (Platform.isIOS) return 0;
    final deg = switch (camera?.value.deviceOrientation) {
      DeviceOrientation.landscapeLeft => 90,
      DeviceOrientation.portraitDown => 180,
      DeviceOrientation.landscapeRight => 270,
      _ => 0,
    };
    // 전면 카메라는 센서가 거울상이라 기기 방향을 더한다
    return front ? (_sensorOrientation + deg) % 360 : (_sensorOrientation - deg + 360) % 360;
  }

  Future<void> setZoom(double z) async {
    final c = camera;
    if (c == null) return;
    zoom = z.clamp(minZoom, maxZoom);
    notifyListeners();
    try {
      await c.setZoomLevel(zoom);
    } catch (_) {}
  }

  /// 화면을 눌러 그 지점에 초점·노출을 맞춘다. [p] 는 미리보기 기준 0..1.
  Future<void> focusAt(Offset p) async {
    final c = camera;
    if (c == null) return;
    focusPoint = p;
    notifyListeners();
    try {
      await c.setFocusPoint(p);
      await c.setExposurePoint(p);
    } catch (_) {}
    Future.delayed(const Duration(seconds: 1), () {
      if (focusPoint == p) {
        focusPoint = null;
        notifyListeners();
      }
    });
  }

  Future<ui.Image> _toImage(Float32List f, int size) {
    final n = size * size;
    final rgba = Uint8List(n * 4);
    for (var i = 0; i < n; i++) {
      rgba[i * 4] = (f[i] * 255).round();
      rgba[i * 4 + 1] = (f[n + i] * 255).round();
      rgba[i * 4 + 2] = (f[2 * n + i] * 255).round();
      rgba[i * 4 + 3] = 255;
    }
    final c = Completer<ui.Image>();
    ui.decodeImageFromPixels(rgba, size, size, ui.PixelFormat.rgba8888, c.complete);
    return c.future;
  }

  // ---- 사진 찍어 판별 (카메라가 흔들릴 때) ----
  /// 정지 사진을 찍어 여러 영역으로 판별한다. 영상 인식은 멈췄다가 [resumeAfterPhoto] 로 다시 켠다.
  /// 결과: 기준(threshold) 이상으로 판별된 종들 + "포켓몬 아님"을 뺀 가장 가까운 3종.
  Future<PhotoResult?> takePhoto() async {
    final c = camera, sp = _species;
    if (c == null || !c.value.isInitialized || sp == null) return null;
    final t0 = DateTime.now();
    await stopCamera(); // 영상 프레임 처리를 멈춘다
    for (var i = 0; _busy && i < 50; i++) {
      await Future.delayed(const Duration(milliseconds: 20)); // 처리 중인 프레임이 끝나기를 기다린다
    }
    try {
      await c.setFlashMode(torch ? FlashMode.torch : FlashMode.off); // 사진에서 플래시는 터뜨리지 않고, 손전등이 켜져 있으면 유지
    } catch (_) {}
    final file = await c.takePicture();
    final bytes = await file.readAsBytes();
    try {
      await File(file.path).delete();
    } catch (_) {}
    // 카메라 화면에서 탭해 둔 영역(region)은 사진에 따라붙지 않는다: 사진은 전체를 판별하고,
    // 영역은 결과 화면에서 사진을 탭해 따로 고른다 (analyzePhotoRegion).
    final prep = await compute(prepPhoto, PhotoJob(bytes, sp.inputSize));
    final probs = <List<double>>[for (final x in prep.inputs) await sp.probs(x)];
    final full = await _photoView(probs, prep.inputs, prep.tiles, null, null);
    final focus = prep.focusIdx.isEmpty ? null : await _photoView(probs, prep.inputs, prep.tiles, prep.focusIdx, prep.tiles[prep.focusIdx.first]);
    var found = false;
    for (final cd in (focus ?? full).judged) {
      found |= discovered.add(cd.id); // 처음 보여 주는 결과에서 판별된 종을 도감에 기록한다
    }
    if (found) _prefs?.setStringList('discovered', discovered.toList());
    notifyListeners();
    return PhotoResult(
      preview: prep.preview,
      width: prep.width,
      height: prep.height,
      tiles: prep.tiles,
      all: full,
      focus: focus,
      srcWidth: prep.srcWidth,
      srcHeight: prep.srcHeight,
      ms: DateTime.now().difference(t0).inMilliseconds,
    );
  }

  /// 영역별 확률([probs])에서 [only] 영역만 써서 판별 결과를 만든다 (null = 전부). 판별된 종은 ② 폼도 본다.
  Future<PhotoView> _photoView(List<List<double>> probs, List<Float32List> inputs, List<Tile> tiles, List<int>? only, Tile? region) async {
    final sp = _species!;
    final fm = _forms, table = _formTable;
    final all = bestPerSpecies(probs, sp.labels, only: only);
    final judged = [for (final cd in all.take(5)) if (cd.prob >= threshold) cd];
    final forms = <String, Prediction>{};
    if (fm != null && table != null && fm.inputSize == sp.inputSize) {
      for (final cd in judged) {
        if (!table.hasForms(cd.id)) continue;
        final top = table.top(cd.id, await fm.probs(inputs[cd.tile]), fm.labels);
        if (top.isNotEmpty) forms[cd.id] = top.first;
      }
    }
    return PhotoView(top3: all.take(3).toList(), judged: judged, forms: forms, tiles: tiles, region: region);
  }

  /// 사진 결과 화면에서 사진을 탭한 곳([p], 사진 기준 0..1) 둘레만 다시 판별한다.
  /// [zoom] 은 사진을 확대해서 보던 배율 (클수록 더 작은 영역을 본다).
  Future<PhotoView?> analyzePhotoRegion(PhotoResult r, Offset p, {double zoom = 1}) async {
    final sp = _species;
    if (sp == null) return null;
    final prep = await compute(prepFocusOnly, PhotoJob(r.preview, sp.inputSize, focusX: p.dx, focusY: p.dy, zoom: zoom));
    final probs = <List<double>>[for (final x in prep.inputs) await sp.probs(x)];
    final v = await _photoView(probs, prep.inputs, prep.tiles, null, prep.tiles.first);
    var found = false;
    for (final cd in v.judged) {
      found |= discovered.add(cd.id);
    }
    if (found) _prefs?.setStringList('discovered', discovered.toList());
    notifyListeners();
    return v;
  }

  /// 사진 결과를 닫으면 영상 인식을 다시 켠다.
  Future<void> resumeAfterPhoto() => startCamera();

  // ---- 진단: 실제 촬영 수집 ----
  Completer<Shot>? _grab;
  int captureCount = 0;

  /// 다음 카메라 프레임을 잡아 돌려준다 (모델 입력·원본 프레임·상위 3개).
  Future<Shot> grabFrame() {
    final c = _grab ?? Completer<Shot>();
    _grab = c;
    return c.future.timeout(const Duration(seconds: 3));
  }

  Future<void> saveShot(Shot shot, String label) async {
    await CaptureStore.save(shot, label);
    captureCount = await CaptureStore.count();
    notifyListeners();
  }

  void setShowDebug(bool v) {
    showDebug = v;
    _prefs?.setBool('showDebug', v);
    notifyListeners();
  }

  Future<void> _onFrame(CameraImage img) async {
    final sp = _species;
    if (_busy || sp == null) return;
    _busy = true;
    try {
      final t0 = DateTime.now();
      final rot = _rotation();
      final size = sp.inputSize;
      // 기본은 가운데 영역(기존 방식)이 첫째, 여러 마리 찾기를 켜면 화면 전체를 덮는 타일을 더한다.
      // 사용자가 영역을 탭해 두었으면 그 둘레의 크기가 다른 정사각형 3개만 분류한다 (더 빠르고 그 영역 위주).
      final swap = rot == 90 || rot == 270;
      final uw = swap ? img.height : img.width, uh = swap ? img.width : img.height;
      final r = region;
      final scans = <_Scan>[];
      if (r != null) {
        for (final t in focusTiles(uw, uh, r.dx, r.dy)) {
          final x = preprocess(img, rotation: rot, mode: cropMode, size: size, tile: t, samples: scans.isEmpty ? 3 : 2);
          scans.add(_Scan(t, x, topK(await sp.probs(x), sp.labels)));
        }
      } else {
        final center = preprocess(img, rotation: rot, mode: cropMode, size: size);
        scans.add(_Scan(null, center, topK(await sp.probs(center), sp.labels)));
        if (multi) {
          for (final t in frameTiles(uw, uh).skip(1)) {
            final x = preprocess(img, rotation: rot, mode: cropMode, size: size, tile: t, samples: 2);
            scans.add(_Scan(t, x, topK(await sp.probs(x), sp.labels)));
          }
        }
      }
      final center = scans.first.input; // 진단·수집에 쓰는 대표 입력 (가운데 또는 선택 영역의 가장 큰 타일)
      if (showDebug) {
        frameInfo = '${img.width}x${img.height} ${img.format.group.name} planes=${img.planes.length} rot=$rot ${cropMode.name} $size${r != null ? ' 선택영역' : ''}';
        if (_frameTimes.length % 3 == 0) inputPreview = await _toImage(center, size);
      }
      tileCount = scans.length;
      final preds = scans.first.preds;
      final rec = _recognizer.update([for (final sc in scans) sc.preds]);
      final g = _grab;
      if (g != null && !g.isCompleted) {
        _grab = null;
        g.complete(Shot.from(img, rot, center, size, preds, {
          'threshold': threshold,
          'crop_mode': cropMode.name,
          'model_classes': sp.labels.length,
          'zoom': zoom,
          'state': rec.state.name,
          'multi': multi,
          'tiles': scans.length,
          'recognized': [for (final p in rec.all) {'id': p.id, 'p': p.confidence}],
        }));
      }
      final sel = rec.state == RecState.recognized ? _chooseSelected(rec) : null;
      await _classifyForm(img, rot, size, scans, sel);
      final now = DateTime.now().millisecondsSinceEpoch;
      _frameTimes
        ..add(now)
        ..removeWhere((t) => now - t > 1000);
      fps = _frameTimes.length.toDouble();
      msPerFrame = DateTime.now().difference(t0).inMilliseconds.toDouble();
      _apply(rec, preds, sel);
    } catch (e) {
      cameraError = '$e';
    } finally {
      _busy = false;
    }
  }

  /// 인식된 종 중 카드로 보여 줄 하나: 사용자가 고른 것 → 지금 보이는 것(깜빡임 방지) → 확신도가 가장 높은 것.
  String _chooseSelected(Recognition rec) {
    bool has(String? id) => id != null && rec.all.any((p) => p.id == id);
    if (has(_selectedId)) return _selectedId!;
    _selectedId = null;
    if (has(shown?.id)) return shown!.id;
    return rec.all.first.id;
  }

  /// 화면을 탭한 곳 위주로 판별한다 ([p] 는 미리보기 기준 0..1). 다른 곳을 다시 탭하면 옮겨진다.
  void setRegion(Offset p) {
    // 안드로이드 전면 카메라는 미리보기만 거울상이고 분석 프레임은 아니라서 가로를 뒤집는다 (iOS 는 프레임도 거울상) — 미검증
    final x = (front && Platform.isAndroid) ? 1 - p.dx : p.dx;
    region = Offset(x.clamp(0.0, 1.0), p.dy.clamp(0.0, 1.0));
    _recognizer.reset(); // 영역이 바뀌면 연속 인식을 처음부터 센다
    _formVoter.reset();
    notifyListeners();
  }

  /// 미리보기 화면에 상자를 그릴 위치 (setRegion 에서 뒤집은 가로를 되돌린다).
  Offset? get regionOnScreen {
    final r = region;
    if (r == null) return null;
    return (front && Platform.isAndroid) ? Offset(1 - r.dx, r.dy) : r;
  }

  void clearRegion() {
    if (region == null) return;
    region = null;
    _recognizer.reset();
    _formVoter.reset();
    notifyListeners();
  }

  /// 인식된 종을 카드로 고른다 (칩을 눌렀을 때).
  void selectRecognized(String id) {
    if (!recognized.any((p) => p.id == id)) return;
    _selectedId = id;
    final e = dex?.entry(id);
    if (e != null && shown?.id != id) {
      shown = e;
      shownForm = null;
      shownConfidence = recognized.firstWhere((p) => p.id == id).confidence;
      _formVoter.reset();
      _playIfAllowed(id);
    }
    notifyListeners();
  }

  /// ② 폼 모델: 카드로 보여 줄 종에 폼이 있을 때만 돌린다. 그 종이 가장 잘 보인 영역(타일)을 넣고, 그 종의 폼만 남겨 고른다.
  Future<void> _classifyForm(CameraImage img, int rot, int size, List<_Scan> scans, String? sel) async {
    lastForms = [];
    final fm = _forms, table = _formTable;
    if (fm == null || table == null || sel == null || !table.hasForms(sel)) return;
    _Scan? best;
    for (final sc in scans) {
      if (sc.preds.isEmpty || sc.preds.first.id != sel) continue;
      if (best == null || sc.preds.first.confidence > best.preds.first.confidence) best = sc;
    }
    best ??= scans.first;
    final x = fm.inputSize == size
        ? best.input
        : preprocess(img, rotation: rot, mode: cropMode, size: fm.inputSize, tile: best.tile);
    final top = table.top(sel, await fm.probs(x), fm.labels);
    if (top.isEmpty) return;
    lastForms = top;
    _formVoter.add(sel, top.first);
  }

  void _apply(Recognition rec, List<Prediction> preds, String? sel) {
    recognition = rec;
    top = preds;
    if (rec.state == RecState.recognized && sel != null) {
      recognized = rec.all;
      var changed = false;
      for (final p in rec.all) {
        changed |= discovered.add(p.id); // 인식된 모든 종을 도감에 기록한다
      }
      if (changed) _prefs?.setStringList('discovered', discovered.toList());
      shownConfidence = rec.all.firstWhere((p) => p.id == sel).confidence;
      final f = _formVoter.current(sel);
      // 폼 라벨이고 기준 이상일 때만 폼으로 보여 준다 (아니면 기본형)
      shownForm = (f != null && f.id.contains('__') && f.confidence >= formThreshold) ? f.id : null;
      shownFormConfidence = f?.confidence ?? 0;
      if (shown?.id != sel) {
        shown = dex?.entry(sel);
        _playIfAllowed(sel);
      }
    } else if (shown != null || recognized.isNotEmpty) {
      // 인식 조건을 만족할 때만 카드를 보여 준다
      recognized = [];
      _selectedId = null;
      shown = null;
      shownForm = null;
    }
    notifyListeners();
  }

  // ---- 음성 ----
  bool canPlay(String id) => dex?.hasAudio(id) ?? false;

  /// 음성 파일이 없으면 조용히 건너뛴다 (아직 음성을 만들지 않은 종).
  Future<bool> _play(String id) async {
    if (!canPlay(id)) return false;
    try {
      await _player.stop();
      await _player.play(AssetSource(dex!.audioPath(id)));
      playingId = id;
      notifyListeners();
      return true;
    } catch (_) {
      playingId = null;
      return false;
    }
  }

  void _playIfAllowed(String id) {
    if (!autoPlay) return;
    final last = _lastPlayedAt[id];
    if (last != null && DateTime.now().difference(last) < _replayCooldown) return;
    _lastPlayedAt[id] = DateTime.now();
    _play(id);
  }

  Future<void> playManually(String id) async {
    if (playingId == id) {
      await _player.stop();
      playingId = null;
      notifyListeners();
    } else {
      await _play(id);
    }
  }

  // ---- 설정 ----
  void setThreshold(double v) {
    threshold = v;
    _recognizer.threshold = v;
    _prefs?.setDouble(_thresholdKey, v);
    notifyListeners();
  }

  void setFormThreshold(double v) {
    formThreshold = v;
    _prefs?.setDouble(_formThresholdKey, v);
    notifyListeners();
  }

  void setMulti(bool v) {
    multi = v;
    _recognizer.reset();
    _prefs?.setBool('multi', v);
    notifyListeners();
  }

  void setCropMode(CropMode m) {
    cropMode = m;
    _prefs?.setString('cropMode', m.name);
    notifyListeners();
  }

  void setAutoPlay(bool v) {
    autoPlay = v;
    _prefs?.setBool('autoPlay', v);
    notifyListeners();
  }

  void resetDex() {
    discovered = {};
    _prefs?.remove('discovered');
    notifyListeners();
  }

  @override
  void dispose() {
    camera?.dispose();
    _player.dispose();
    super.dispose();
  }
}

/// 한 프레임에서 한 영역을 분류한 결과. [tile] 이 null 이면 가운데(cropMode) 영역.
class _Scan {
  _Scan(this.tile, this.input, this.preds);
  final Tile? tile;
  final Float32List input;
  final List<Prediction> preds;
}

