import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Navbar } from "./components/Navbar";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { ScrollToTop } from "./components/ScrollToTop";

// Route-level code splitting: each page is bundled separately and loaded
// on demand. Without this, every login session pulled the entire app
// bundle (1.3 MB) — including the heavy ResultsPage (~4 kloc), Recharts,
// html2canvas and jspdf — before the first paint. Now /history and /analytics
// users never download ResultsPage code, /home users never download
// admin/users code, etc.
const HomePage = lazy(() =>
  import("./pages/HomePage").then((m) => ({ default: m.HomePage }))
);
const UploadPage = lazy(() =>
  import("./pages/UploadPage").then((m) => ({ default: m.UploadPage }))
);
const HistoryPage = lazy(() =>
  import("./pages/HistoryPage").then((m) => ({ default: m.HistoryPage }))
);
const AnalyticsPage = lazy(() =>
  import("./pages/AnalyticsPage").then((m) => ({ default: m.AnalyticsPage }))
);
const LoginPage = lazy(() =>
  import("./pages/LoginPage").then((m) => ({ default: m.LoginPage }))
);
const OTPSetupPage = lazy(() =>
  import("./pages/OTPSetupPage").then((m) => ({ default: m.OTPSetupPage }))
);
const UsersPage = lazy(() =>
  import("./pages/UsersPage").then((m) => ({ default: m.UsersPage }))
);
const AuditLogPage = lazy(() =>
  import("./pages/AuditLogPage").then((m) => ({ default: m.AuditLogPage }))
);

// Shown while a route chunk is being fetched. Kept minimal so it doesn't
// trigger a layout shift when the page mounts.
function RouteFallback() {
  return (
    <div className="flex items-center justify-center py-20">
      <div className="h-8 w-8 rounded-full border-2 border-slate-200 border-t-ccm-red animate-spin" />
    </div>
  );
}

export default function App() {
  const location = useLocation();
  const isLoginPage = location.pathname === "/login";

  // The login page is rendered standalone (no navbar, no footer, no
  // protected wrapper) so the user can always reach it.
  if (isLoginPage) {
    return (
      <>
        <ScrollToTop />
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
          </Routes>
        </Suspense>
      </>
    );
  }

  return (
    <div className="min-h-screen flex flex-col bg-ccm-parchment">
      <ScrollToTop />
      <Navbar />
      <main className="flex-1">
        <div className="mx-auto w-full max-w-7xl px-4 md:px-8 py-8">
          <Suspense fallback={<RouteFallback />}>
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
              path="/results"
              element={
                <ProtectedRoute>
                  <UploadPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/upload"
              element={<Navigate to="/results" replace />}
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
            <Route
              path="/admin/users"
              element={
                <ProtectedRoute>
                  <UsersPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/admin/audit-log"
              element={
                <ProtectedRoute>
                  <AuditLogPage />
                </ProtectedRoute>
              }
            />
            <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
        </div>
      </main>
      <footer className="border-t border-slate-200 bg-white">
        <div className="mx-auto max-w-7xl px-4 md:px-8 py-4 text-xs text-slate-500 flex flex-wrap items-center justify-between gap-2">
          <p>
            <span className="font-semibold text-ccm-red">SIA</span> &middot;
            Plateforme d'analyse de scenarios &middot; Centre Cinematographique
            Marocain
          </p>
          <p className="text-slate-400">v1.0</p>
        </div>
      </footer>
    </div>
  );
}
