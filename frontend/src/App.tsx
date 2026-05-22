import { Navigate, Route, Routes } from "react-router-dom";

import { AppLayout } from "./components/layout/AppLayout";
import { DashboardPage } from "./pages/DashboardPage";
import { InstrumentPage } from "./pages/InstrumentPage";
import { MobileChartPage } from "./pages/MobileChartPage";
import { ScannerPage } from "./pages/ScannerPage";

// Detect Capacitor (Android/iOS) at module load time.
// When running in a Capacitor WebView, window.Capacitor is defined.
const isNative = "Capacitor" in window;

export default function App() {
  return (
    <Routes>
      {/* Standalone mobile chart — no desktop AppLayout wrapper */}
      <Route path="/mobile-chart" element={<MobileChartPage />} />

      {/* Desktop web routes — all wrapped in AppLayout */}
      <Route
        path="/"
        element={
          isNative ? (
            // On device, redirect root directly to the mobile page
            <Navigate to="/mobile-chart" replace />
          ) : (
            <AppLayout>
              <DashboardPage />
            </AppLayout>
          )
        }
      />
      <Route
        path="/chart"
        element={
          <AppLayout>
            <InstrumentPage />
          </AppLayout>
        }
      />
      <Route
        path="/instruments/:ticker"
        element={
          <AppLayout>
            <InstrumentPage />
          </AppLayout>
        }
      />
      <Route
        path="/scanner"
        element={
          <AppLayout>
            <ScannerPage />
          </AppLayout>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
