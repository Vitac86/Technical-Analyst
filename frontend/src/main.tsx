import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import "./styles/globals.css";

// Defence-in-depth against page zoom gestures on Android WebView / iOS Safari.
// The viewport meta in index.html disables user-scaling, but some platforms
// still honour gesture/Ctrl+wheel zoom. These listeners cancel those.
(function blockZoomGestures() {
  // iOS Safari only — prevents pinch-to-zoom inside WebView.
  document.addEventListener("gesturestart", (e) => e.preventDefault(), { passive: false });
  document.addEventListener("gesturechange", (e) => e.preventDefault(), { passive: false });
  document.addEventListener("gestureend", (e) => e.preventDefault(), { passive: false });

  // Block Ctrl/⌘ + wheel zoom on desktop WebView.
  window.addEventListener(
    "wheel",
    (e) => { if (e.ctrlKey) e.preventDefault(); },
    { passive: false },
  );

  // Block Ctrl/⌘ + (+/-/0) keyboard zoom.
  window.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    if (["=", "+", "-", "0"].includes(e.key)) e.preventDefault();
  });
})();

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
