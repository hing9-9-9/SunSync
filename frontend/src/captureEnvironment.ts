import { allowBrowserCapture } from "./capturePolicy";

// Same rule main.tsx uses to pick the mobile screen.
const mode = new URLSearchParams(location.search).get("mode");
const mobile = mode === "app" || (mode !== "web" && matchMedia("(max-width: 760px)").matches);

export const browserCaptureEnabled = allowBrowserCapture(
  import.meta.env.DEV,
  import.meta.env.VITE_ENABLE_CAPTURE_TESTS,
  mobile,
);
