import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
// Styles load in one fixed order for both layouts (the cascade depends on it), so they stay out of the lazy chunks.
import "./palette.css";
import "./index.css";
import "./AuthNotice.css";
import "./WebLoginStep.css";
import "./GoogleButton.css";
import "./ReferencePreview.css";
import "./WorkspaceDesign.css";
import "./SpaceMenu.css";
import "./App.css";
import "./AwardPreview.css";
import "./AwardSystem.css";
import "./Space3DView.css";
import "./PredictionSummary.css";
import "./Website.css";
import "./TestMarkerPreview.css";
import "./AwardMobile.css";
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
import "./EditorPlan.css";
import "./MobileConsistency.css";
import "./ControlSystem.css";
import "./InspectorRhythm.css";
import "./LogoFont.css";
import { registerServiceWorker } from "./swUpdate.ts";

// Only the layout in use is downloaded: the web layout and the app layout are separate chunks.
const App = lazy(() => import("./App.tsx"));
const MobileApp = lazy(() => import("./MobileApp.tsx"));

const mode = new URLSearchParams(location.search).get("mode");
const mobile = mode === "app" || (mode !== "web" && matchMedia("(max-width: 760px)").matches);
createRoot(document.getElementById("root")!).render(
  <StrictMode><Suspense fallback={null}>{mobile ? <MobileApp /> : <App />}</Suspense></StrictMode>,
);

if (import.meta.env.PROD && "serviceWorker" in navigator) registerServiceWorker();
