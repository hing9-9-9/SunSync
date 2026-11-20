import AVFoundation
import Foundation
import ImageIO
import UniformTypeIdentifiers
import CoreGraphics

/// AVFoundation adapter. Integrate in the native app's camera session queue (iOS 17+).
@available(iOS 17.0, *)
final class NativeMeasurementCapture {
    private var configuration: String?
    private var streamID = UUID().uuidString

    /// Use the actual camera device, not a preview/screenshot's dimensions.
    /// Call after autofocus settles. Keep the lock until the measurement/setup ends.
    func lockMeasurementConfiguration(device: AVCaptureDevice, connection: AVCaptureConnection) throws {
        guard device.isFocusModeSupported(.locked), connection.isVideoRotationAngleSupported(0) else {
            throw CaptureError.unsupportedConfiguration
        }
        try device.lockForConfiguration()
        defer { device.unlockForConfiguration() }
        device.focusMode = .locked
        device.videoZoomFactor = 1
        connection.videoRotationAngle = 0
        if connection.isVideoMirroringSupported {
            connection.automaticallyAdjustsVideoMirroring = false
            connection.isVideoMirrored = false
        }
        if connection.isVideoStabilizationSupported { connection.preferredVideoStabilizationMode = .off }
    }

    func settings(output: AVCapturePhotoOutput) -> AVCapturePhotoSettings {
        let settings = AVCapturePhotoSettings(format: [AVVideoCodecKey: AVVideoCodecType.jpeg])
        settings.isCameraCalibrationDataDeliveryEnabled = output.isCameraCalibrationDataDeliverySupported
        settings.photoQualityPrioritization = .speed
        return settings
    }

    /// Invoke in didFinishProcessingPhoto, paired to the ID/time saved in willCapturePhotoFor.
    /// Pixel bytes and calibration come from the same AVCapturePhoto. No EXIF auto-rotation.
    func payload(photo: AVCapturePhoto, device: AVCaptureDevice, connection: AVCaptureConnection,
                 captureID: String, pointID: String, shutterTime: Date, board: [String: Any]) throws -> [String: Any] {
        guard device.focusMode == .locked, connection.videoRotationAngle == 0,
              !connection.isVideoMirrored, connection.activeVideoStabilizationMode == .off,
              let original = photo.cgImageRepresentation() else { throw CaptureError.unsupportedConfiguration }
        // Full-frame uniform scaling bounds upload/memory use without changing the field of view.
        let scale = min(1, 1920.0 / Double(max(original.width, original.height)))
        let width = Int((Double(original.width) * scale).rounded())
        let height = Int((Double(original.height) * scale).rounded())
        guard let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
            bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { throw CaptureError.imageEncoding }
        context.interpolationQuality = .high
        context.draw(original, in: CGRect(x: 0, y: 0, width: CGFloat(width), height: CGFloat(height)))
        guard let cgImage = context.makeImage() else { throw CaptureError.imageEncoding }
        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(data as CFMutableData, UTType.jpeg.identifier as CFString, 1, nil) else {
            throw CaptureError.imageEncoding
        }
        CGImageDestinationAddImage(destination, cgImage, [kCGImagePropertyOrientation: 1] as CFDictionary)
        guard CGImageDestinationFinalize(destination) else { throw CaptureError.imageEncoding }
        let signature = "\(device.uniqueID)|\(width)x\(height)|\(device.lensPosition)|\(device.videoZoomFactor)|\(device.activeFormat)"
        if signature != configuration { configuration = signature; streamID = UUID().uuidString }
        var calibration: Any = NSNull()
        if let info = photo.cameraCalibrationData, let table = info.lensDistortionLookupTable {
            let k = info.intrinsicMatrix
            let size = info.intrinsicMatrixReferenceDimensions
            let values: [Float] = table.withUnsafeBytes { bytes in
                stride(from: 0, to: bytes.count - bytes.count % 4, by: 4).map {
                    bytes.loadUnaligned(fromByteOffset: $0, as: Float.self)
                }
            }
            if values.count >= 2 && abs(Double(width) / Double(size.width) - Double(height) / Double(size.height)) < 0.002 {
                calibration = ["kind": "ios_avfoundation",
                    "intrinsic": (0..<3).flatMap { row in (0..<3).map { col in Double(k[col][row]) } },
                    "reference_width": Int(size.width), "reference_height": Int(size.height),
                    "distortion_center": [info.lensDistortionCenter.x, info.lensDistortionCenter.y],
                    "distortion_lookup": values.map(Double.init), "full_reference_frame": true] as [String: Any]
            }
        }
        return ["capture_id": captureID, "point_id": pointID,
            "captured_at": ISO8601DateFormatter().string(from: shutterTime),
            "stream_id": streamID, "lens_id": device.uniqueID,
            "width": width, "height": height, "pixel_rotation_degrees": 0, "mirrored": false,
            "image_base64": (data as Data).base64EncodedString(), "board": board, "calibration": calibration]
    }

    func reset() { configuration = nil; streamID = UUID().uuidString }
    enum CaptureError: Error { case unsupportedConfiguration, unsupportedResolution, imageEncoding }
}
