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
