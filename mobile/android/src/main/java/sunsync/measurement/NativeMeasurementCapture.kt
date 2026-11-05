package sunsync.measurement

import android.graphics.BitmapFactory
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.CaptureResult
import android.hardware.camera2.TotalCaptureResult
import android.media.Image
import android.os.Build
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.util.UUID

/** Camera2 adapter, API 28+. Invoke from the host app's camera thread, never from a WebView. */
class NativeMeasurementCapture {
    private var configuration: String? = null
    private var streamId = UUID.randomUUID().toString()

    /** Use this size for the ImageReader surface; do not resize/crop a preview screenshot. */
    fun recommendedJpegSize(characteristics: CameraCharacteristics): android.util.Size {
        val sizes = characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
            ?.getOutputSizes(android.graphics.ImageFormat.JPEG) ?: error("No JPEG capture stream")
        return sizes.filter { it.width <= 1920 && it.height <= 1920 }
            .maxByOrNull { it.width.toLong() * it.height } ?: error("No supported measurement resolution")
    }

    /** Apply to the actual still-capture request after preview autofocus has settled. */
    fun configure(builder: CaptureRequest.Builder, focusedPreview: CaptureResult) {
        builder.set(CaptureRequest.JPEG_ORIENTATION, 0)
        builder.set(CaptureRequest.DISTORTION_CORRECTION_MODE, CaptureRequest.DISTORTION_CORRECTION_MODE_OFF)
        builder.set(CaptureRequest.CONTROL_VIDEO_STABILIZATION_MODE, CaptureRequest.CONTROL_VIDEO_STABILIZATION_MODE_OFF)
        builder.set(CaptureRequest.LENS_OPTICAL_STABILIZATION_MODE, CaptureRequest.LENS_OPTICAL_STABILIZATION_MODE_OFF)
        if (Build.VERSION.SDK_INT >= 30) builder.set(CaptureRequest.CONTROL_ZOOM_RATIO, 1f)
        focusedPreview.get(CaptureResult.LENS_FOCUS_DISTANCE)?.let {
            builder.set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_OFF)
            builder.set(CaptureRequest.LENS_FOCUS_DISTANCE, it)
        }
    }

    /** Pair ImageReader JPEG and TotalCaptureResult by SENSOR_TIMESTAMP before calling.
     * shutterTime is recorded by the host in onCaptureStarted, not when upload finishes.
     * The host closes image in finally. Preview screenshots and gallery images are not accepted.
     */
    @Synchronized
    fun payload(
        image: Image, result: TotalCaptureResult, characteristics: CameraCharacteristics,
        cameraId: String, captureId: String, pointId: String, shutterTime: Instant, board: JSONObject
    ): JSONObject {
        require(Build.VERSION.SDK_INT >= 28) { "This adapter requires Android 9 or newer" }
        require(image.format == android.graphics.ImageFormat.JPEG) { "Capture a JPEG image" }
        require(result.get(CaptureResult.SENSOR_TIMESTAMP) == image.timestamp) { "Image/result timestamp mismatch" }
        require(result.get(CaptureResult.JPEG_ORIENTATION) == 0) { "Use unrotated capture pixels" }
        require(result.get(CaptureResult.CONTROL_AF_MODE) == CaptureResult.CONTROL_AF_MODE_OFF) { "Lock focus before measurement" }
        require((result.get(CaptureResult.CONTROL_VIDEO_STABILIZATION_MODE) ?: 0) == 0) { "Disable video stabilization" }
        require((result.get(CaptureResult.LENS_OPTICAL_STABILIZATION_MODE) ?: 0) == 0) { "Disable optical stabilization" }
        if (Build.VERSION.SDK_INT >= 30) require((result.get(CaptureResult.CONTROL_ZOOM_RATIO) ?: 1f) == 1f)
        if (Build.VERSION.SDK_INT >= 31) require((result.get(CaptureResult.SENSOR_PIXEL_MODE) ?: 0) == 0)
        val buffer = image.planes[0].buffer.duplicate()
        val bytes = ByteArray(buffer.remaining()).also { buffer.get(it) }
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        require(bounds.outWidth > 0 && bounds.outHeight > 0)
        require(bounds.outWidth <= 4096 && bounds.outHeight <= 4096 && bounds.outWidth.toLong() * bounds.outHeight <= 8_000_000)
        val activeId = result.get(CaptureResult.LOGICAL_MULTI_CAMERA_ACTIVE_PHYSICAL_ID) ?: cameraId
        val crop = result.get(CaptureResult.SCALER_CROP_REGION)
        val pre = characteristics.get(CameraCharacteristics.SENSOR_INFO_PRE_CORRECTION_ACTIVE_ARRAY_SIZE)
        // Never mix a logical camera's static calibration with another physical lens.
        val sameLens = activeId == cameraId
        val intrinsic = result.get(CaptureResult.LENS_INTRINSIC_CALIBRATION)
        val distortion = result.get(CaptureResult.LENS_DISTORTION)
        val correctionOff = result.get(CaptureResult.DISTORTION_CORRECTION_MODE) == 0
        val signature = listOf(activeId, bounds.outWidth, bounds.outHeight, crop,
            result.get(CaptureResult.LENS_FOCUS_DISTANCE), correctionOff).joinToString("|")
        if (signature != configuration) {
            configuration = signature
            streamId = UUID.randomUUID().toString()
        }
        val metadata = if (sameLens && correctionOff && intrinsic != null && distortion != null && pre != null && crop != null && kotlin.math.abs(intrinsic[4]) < 1e-6) {
            JSONObject().put("kind", "android_camera2")
                .put("intrinsic", JSONArray(intrinsic.map { it.toDouble() }))
                .put("distortion", JSONArray(distortion.map { it.toDouble() }))
                .put("reference_width", pre.width()).put("reference_height", pre.height())
                .put("crop", JSONArray(listOf(crop.left - pre.left, crop.top - pre.top, crop.width(), crop.height())))
                .put("distortion_correction", "off").put("stabilization", "off")
        } else JSONObject.NULL
        return JSONObject().put("capture_id", captureId).put("point_id", pointId)
            .put("captured_at", shutterTime.toString()).put("stream_id", streamId).put("lens_id", activeId)
            .put("width", bounds.outWidth).put("height", bounds.outHeight)
            .put("pixel_rotation_degrees", 0).put("mirrored", false)
            .put("image_base64", Base64.encodeToString(bytes, Base64.NO_WRAP))
            .put("board", board).put("calibration", metadata)
    }

    /** Call when closing/reopening the camera, even if the same settings are selected. */
    @Synchronized fun reset() { configuration = null; streamId = UUID.randomUUID().toString() }
}
