import { allowBrowserCapture } from "./capturePolicy";

export const browserCaptureEnabled = import.meta.env.DEV && allowBrowserCapture(
  import.meta.env.DEV,
  import.meta.env.VITE_ENABLE_CAPTURE_TESTS,
);
