import { Navigate, Route, Routes } from "react-router-dom";

import { AppLayout } from "./components/layout/AppLayout";
import { DashboardPage } from "./pages/DashboardPage";
import { InstrumentPage } from "./pages/InstrumentPage";
import { ScannerPage } from "./pages/ScannerPage";

export default function App() {
  return (
    <AppLayout>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/chart" element={<InstrumentPage />} />
        <Route path="/instruments/:ticker" element={<InstrumentPage />} />
        <Route path="/scanner" element={<ScannerPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppLayout>
  );
}
