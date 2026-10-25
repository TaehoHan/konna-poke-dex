import 'dart:math' as math;

import 'package:flutter/services.dart';

/// 기기 하드웨어가 알려 주는 한 카메라의 지원 크기 (네이티브 채널 `kr.hansolo.konnadex/camera_info`).
/// iOS: AVCaptureDevice.formats / supportedMaxPhotoDimensions, 안드로이드: Camera2 StreamConfigurationMap.
class HwSizes {
  const HwSizes(this.video, this.photo);
  final List<({int w, int h})> video; // 영상(미리보기) 프레임 크기들
  final List<({int w, int h})> photo; // 정지 사진 크기들 (iOS 는 최대 크기들)

  /// "4032x3024" 목록 → 크기 목록 (가로·세로 큰 쪽을 w 로 맞춘다).
  static List<({int w, int h})> parse(Iterable<Object?> list) => [
        for (final s in list)
          if (RegExp(r'^(\d+)x(\d+)$').firstMatch('$s') case final m?)
            (w: math.max(int.parse(m.group(1)!), int.parse(m.group(2)!)), h: math.min(int.parse(m.group(1)!), int.parse(m.group(2)!))),
      ];

  bool hasVideoAtLeast(int w, int h) => video.any((s) => s.w >= w && s.h >= h);

  ({int w, int h})? get maxPhoto => _largest(photo.isNotEmpty ? photo : video);

  static ({int w, int h})? _largest(List<({int w, int h})> l) {
    if (l.isEmpty) return null;
    return l.reduce((a, b) => a.w * a.h >= b.w * b.h ? a : b);
  }
}

const _channel = MethodChannel('kr.hansolo.konnadex/camera_info');

/// [cameraName] (camera 패키지 CameraDescription.name) 카메라의 하드웨어 지원 크기. 채널이 없거나 실패하면 null.
Future<HwSizes?> queryHwSizes(String cameraName) async {
  try {
    final r = await _channel.invokeMapMethod<String, Object?>('info', {'name': cameraName});
    if (r == null) return null;
    return HwSizes(HwSizes.parse((r['video'] as List?) ?? const []), HwSizes.parse((r['photo'] as List?) ?? const []));
  } catch (_) {
    return null;
  }
}

/// 해상도 단계 이름(CamRes.name) → 이 카메라에서 나오는 크기. 하드웨어가 못 내는 단계는 빠진다.
/// hd/fhd/uhd 는 해당 크기 이상의 영상 형식이 있을 때만, max 는 이 카메라의 사진 최대 크기.
Map<String, String> resolutionsFromHw(HwSizes hw) {
  String f(int w, int h) => '$w×$h';
  final out = <String, String>{};
  if (hw.hasVideoAtLeast(1280, 720)) out['hd'] = f(1280, 720);
  if (hw.hasVideoAtLeast(1920, 1080)) out['fhd'] = f(1920, 1080);
  if (hw.hasVideoAtLeast(3840, 2160)) out['uhd'] = f(3840, 2160);
  final m = hw.maxPhoto;
  if (m != null) out['max'] = f(m.w, m.h);
  return out;
}
