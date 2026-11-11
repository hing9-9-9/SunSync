// Photo checks for the corner capture (MAR3.2, FR-03.1). Marker detection itself belongs to the
// FR-03 module; until it is connected only brightness and blur are measured on the device.
export type MarkerState = "visible" | "partial" | "missing";
export type PhotoIssue = Exclude<MarkerState, "visible"> | "blur" | "dark" | "bright";
export type PhotoMetrics = { brightness: number; sharpness: number; marker: MarkerState };

export const corners = ["앞 왼쪽", "앞 오른쪽", "뒤 오른쪽", "뒤 왼쪽"] as const;

export const retakeMessages: Record<PhotoIssue, string> = {
  partial: "QR 마커 일부가 화면 밖이에요. 한 걸음 물러나 마커 네 모서리가 모두 보이게 다시 찍어 주세요.",
  missing: "QR 마커를 찾지 못했어요. 창문 필름의 마커를 화면 가운데에 두고 다시 찍어 주세요.",
  blur: "사진이 흔들렸어요. 휴대폰을 두 손으로 잡고 잠시 멈춘 뒤 다시 찍어 주세요.",
  dark: "사진이 너무 어두워요. 실내 조명을 켜고 다시 찍어 주세요.",
  bright: "빛 반사가 너무 강해요. 몸으로 햇빛을 가리거나 각도를 조금 바꿔 다시 찍어 주세요.",
};

const MIN_SHARPNESS = 60;
const MIN_BRIGHTNESS = 40;
const MAX_BRIGHTNESS = 230;
// Marker problems come first: retaking for blur is pointless if the marker is not in frame.
export function photoIssue(m: PhotoMetrics): PhotoIssue | null {
  if (m.marker !== "visible") return m.marker;
  if (m.sharpness < MIN_SHARPNESS) return "blur";
  if (m.brightness < MIN_BRIGHTNESS) return "dark";
  if (m.brightness > MAX_BRIGHTNESS) return "bright";
  return null;
}

// Mean luminance and variance of the 4-neighbour Laplacian on a greyscale image.
export function measurePixels(rgba: Uint8ClampedArray, width: number, height: number) {
  const grey = new Float32Array(width * height);
  let sum = 0;
  for (let i = 0; i < grey.length; i++) {
    grey[i] = 0.299 * rgba[i * 4] + 0.587 * rgba[i * 4 + 1] + 0.114 * rgba[i * 4 + 2];
    sum += grey[i];
  }
  let n = 0, mean = 0, square = 0;
  for (let y = 1; y < height - 1; y++)
    for (let x = 1; x < width - 1; x++) {
      const i = y * width + x;
      const v = grey[i - 1] + grey[i + 1] + grey[i - width] + grey[i + width] - 4 * grey[i];
      n++; mean += v; square += v * v;
    }
  mean = n ? mean / n : 0;
  return { brightness: grey.length ? sum / grey.length : 0, sharpness: n ? square / n - mean * mean : 0 };
}

// Reads a downscaled copy so the check stays fast on phones; the photo never leaves the device.
export async function analyseImage(file: File) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, 320 / Math.max(bitmap.width, bitmap.height));
  const width = Math.max(3, Math.round(bitmap.width * scale)), height = Math.max(3, Math.round(bitmap.height * scale));
  const canvas = document.createElement("canvas");
  canvas.width = width; canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("사진을 확인할 수 없습니다.");
  context.drawImage(bitmap, 0, 0, width, height);
  bitmap.close();
  return measurePixels(context.getImageData(0, 0, width, height).data, width, height);
}

