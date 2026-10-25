import AVFoundation
import Flutter
import UIKit

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    CameraInfo.register(messenger: engineBridge.applicationRegistrar.messenger())
  }
}

/// 기기 하드웨어가 지원하는 카메라 크기를 알려 준다 (Flutter camera 패키지는 목록을 주지 않는다).
/// 채널 `kr.hansolo.konnadex/camera_info`, 메서드 `info`, 인자 {name: camera 패키지의 CameraDescription.name = 기기 uniqueID}.
/// 돌려주는 값: {video: ["1920x1080", ...], photo: ["4032x3024", ...]}
///  - video: 이 카메라가 낼 수 있는 영상 프레임 크기 (AVCaptureDevice.formats)
///  - photo: 정지 사진 최대 크기들 (format.supportedMaxPhotoDimensions, iOS 16+)
enum CameraInfo {
  static func register(messenger: FlutterBinaryMessenger) {
    let channel = FlutterMethodChannel(name: "kr.hansolo.konnadex/camera_info", binaryMessenger: messenger)
    channel.setMethodCallHandler { call, result in
      guard call.method == "info", let args = call.arguments as? [String: Any], let name = args["name"] as? String else {
        result(FlutterMethodNotImplemented)
        return
      }
      guard let device = AVCaptureDevice(uniqueID: name) else {
        result(FlutterError(code: "no_device", message: "카메라를 찾지 못했어요: \(name)", details: nil))
        return
      }
      var video = Set<String>()
      var photo = Set<String>()
      for f in device.formats {
        let d = CMVideoFormatDescriptionGetDimensions(f.formatDescription)
        video.insert("\(d.width)x\(d.height)")
        for p in f.supportedMaxPhotoDimensions {
          photo.insert("\(p.width)x\(p.height)")
        }
      }
      result(["video": Array(video), "photo": Array(photo)])
    }
  }
}
