import { StrictMode } from "react";
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
import "./EditorPlan.css";
import "./MobileConsistency.css";
import "./ControlSystem.css";
import { registerServiceWorker } from "./swUpdate.ts";

const mode = new URLSearchParams(location.search).get("mode");
const mobile = mode === "app" || (mode !== "web" && matchMedia("(max-width: 760px)").matches);
createRoot(document.getElementById("root")!).render(
  <StrictMode>{mobile ? <MobileApp /> : <App />}</StrictMode>,
);

if (import.meta.env.PROD && "serviceWorker" in navigator) registerServiceWorker();
