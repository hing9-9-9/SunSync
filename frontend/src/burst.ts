/** Bounded one-button acquisition, independent of browser or native camera APIs. */
export async function acquireBurst<T>(
  capture: () => Promise<T>,
  options: { count?: number; intervalMs?: number; signal?: AbortSignal } = {},
): Promise<T[]> {
  const count = options.count ?? 10,
    interval = options.intervalMs ?? 180;
  if (
    !Number.isInteger(count) ||
    count < 3 ||
    count > 20 ||
    interval < 0 ||
    interval > 2000
  )
    throw new Error("Invalid burst settings");
  const frames: T[] = [];
  for (let i = 0; i < count; i++) {
    options.signal?.throwIfAborted();
    frames.push(await capture());
    options.signal?.throwIfAborted();
    if (i < count - 1)
      await new Promise<void>((resolve, reject) => {
        const cancel = () => {
          clearTimeout(timer);
          reject(options.signal?.reason ?? new Error("Cancelled"));
        };
        const timer = setTimeout(() => {
          options.signal?.removeEventListener("abort", cancel);
          resolve();
        }, interval);
        options.signal?.addEventListener("abort", cancel, { once: true });
      });
  }
  return frames;
}


/** Geometry relevant to calibration; frame rate and exposure are not camera geometry. */
export function captureGeometry(settings: {
  deviceId?: string; facingMode?: string; resizeMode?: string; zoom?: number; focusDistance?: number;
}, width: number, height: number): string {
  return JSON.stringify([settings.deviceId ?? null, settings.facingMode ?? null,
    settings.resizeMode ?? null, settings.zoom ?? null, settings.focusDistance ?? null, width, height]);
}

/** Bind only once pixels are available; a real change resets the baseline for the next setup. */
export class CaptureGeometryGuard {
  private baseline: string | null = null;
  reset() { this.baseline = null; }
  accept(signature: string): boolean {
    const changed = this.baseline !== null && this.baseline !== signature;
    this.baseline = signature;
    return !changed;
  }
}
