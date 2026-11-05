import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import "./palette.css";
import "./index.css";
import App from "./App.tsx";
import MobileApp from "./MobileApp.tsx";
import "./ProductPolish.css";
import "./Moodboard.css";
import "./tokens.css";
import "./HomeDesign.css";
import "./WebInteractions.css";
import "./GuideDesign.css";
import "./AppTabs.css";
import "./SpaceDesign.css";
import "./AccountDesign.css";
import "./PageTypography.css";
import "./CaptureDesign.css";

const params = new URLSearchParams(location.search);
const mode = params.get("mode");
const mobile = mode === "app" || (mode !== "web" && matchMedia("(max-width: 760px)").matches);
// three.js loads only for the 3D sample (?sample=3d), so the main app bundle stays the same size.
const Space3DSample = lazy(() => import("./Space3DSample.tsx"));
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {params.get("sample") === "3d" ? <Suspense><Space3DSample /></Suspense> : mobile ? <MobileApp /> : <App />}
  </StrictMode>,
);

if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // The online UI remains usable when offline installation is unavailable.
    });
  });
}
