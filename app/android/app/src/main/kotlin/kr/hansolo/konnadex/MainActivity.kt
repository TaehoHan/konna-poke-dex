package kr.hansolo.konnadex

import android.content.Context
import android.graphics.ImageFormat
import android.graphics.SurfaceTexture
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    /// 기기 하드웨어가 지원하는 카메라 크기를 알려 준다 (Flutter camera 패키지는 목록을 주지 않는다).
    /// 채널 kr.hansolo.konnadex/camera_info, 메서드 info, 인자 {name: CameraDescription.name = Camera2 카메라 id}.
    /// 돌려주는 값: {video: ["1920x1080", ...], photo: ["4000x3000", ...]} (video = 미리보기/영상 프레임, photo = JPEG 사진)
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "kr.hansolo.konnadex/camera_info")
            .setMethodCallHandler { call, result ->
                if (call.method != "info") {
                    result.notImplemented()
                    return@setMethodCallHandler
                }
                val name = call.argument<String>("name")
                if (name == null) {
                    result.error("bad_args", "name 이 없어요", null)
                    return@setMethodCallHandler
                }
                try {
                    val manager = getSystemService(Context.CAMERA_SERVICE) as CameraManager
                    val map = manager.getCameraCharacteristics(name).get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
                    if (map == null) {
                        result.error("no_map", "지원 크기 정보가 없어요: $name", null)
                        return@setMethodCallHandler
                    }
                    val video = map.getOutputSizes(SurfaceTexture::class.java)?.map { "${it.width}x${it.height}" }?.distinct() ?: emptyList()
                    val yuv = map.getOutputSizes(ImageFormat.YUV_420_888)?.map { "${it.width}x${it.height}" }?.distinct() ?: emptyList()
                    val photo = map.getOutputSizes(ImageFormat.JPEG)?.map { "${it.width}x${it.height}" }?.distinct() ?: emptyList()
                    result.success(mapOf("video" to (video + yuv).distinct(), "photo" to photo))
                } catch (e: Exception) {
                    result.error("camera_info", e.message, null)
                }
            }
    }
}
