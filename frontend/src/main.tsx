import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.tsx";
import MobileApp from "./MobileApp.tsx";
import "./ProductPolish.css";

const mode = new URLSearchParams(location.search).get("mode");
const mobile = mode === "app" || (mode !== "web" && matchMedia("(max-width: 760px)").matches);
createRoot(document.getElementById("root")!).render(
  <StrictMode>{mobile ? <MobileApp /> : <App />}</StrictMode>,
);

if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // The online UI remains usable when offline installation is unavailable.
    });
  });
}
