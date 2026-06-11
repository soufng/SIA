import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Navbar } from "./components/Navbar";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { HomePage } from "./pages/HomePage";
import { UploadPage } from "./pages/UploadPage";
import { HistoryPage } from "./pages/HistoryPage";
import { AnalyticsPage } from "./pages/AnalyticsPage";
import { LoginPage } from "./pages/LoginPage";
import { OTPSetupPage } from "./pages/OTPSetupPage";

export default function App() {
  const location = useLocation();
  const isLoginPage = location.pathname === "/login";

  // The login page is rendered standalone (no navbar, no footer, no
  // protected wrapper) so the user can always reach it.
  if (isLoginPage) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
      </Routes>
    );
  }

  return (
    <div className="min-h-screen flex flex-col bg-ccm-parchment">
      <Navbar />
      <main className="flex-1">
        <div className="mx-auto w-full max-w-7xl px-4 md:px-8 py-8">
          <Routes>
            <Route
              path="/"
              element={
                <ProtectedRoute>
                  <HomePage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/upload"
              element={
                <ProtectedRoute>
                  <UploadPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/results"
              element={<Navigate to="/upload" replace />}
            />
            <Route
              path="/history"
              element={
                <ProtectedRoute>
                  <HistoryPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/analytics"
              element={
                <ProtectedRoute>
                  <AnalyticsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/security/otp"
              element={
                <ProtectedRoute>
                  <OTPSetupPage />
                </ProtectedRoute>
              }
            />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>
      </main>
      <footer className="border-t border-slate-200 bg-white">
        <div className="mx-auto max-w-7xl px-4 md:px-8 py-4 text-xs text-slate-500 flex flex-wrap items-center justify-between gap-2">
          <p>
            <span className="font-semibold text-ccm-red">SPM</span> &middot;
            Plateforme d'analyse de scenarios &middot; Centre Cinematographique
            Marocain
          </p>
          <p className="text-slate-400">v1.0</p>
        </div>
      </footer>
    </div>
  );
}
