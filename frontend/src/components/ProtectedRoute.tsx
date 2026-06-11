import { useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuthStore } from "@/store/auth";

/**
 * Guards a route subtree. Behavior:
 *   - On first mount, calls /auth/me to refresh server-side state
 *     (handles the "auth disabled" case → open access).
 *   - If auth is required and no username is stored, redirects to /login
 *     while preserving the originally requested URL via location.state.
 */
export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const username = useAuthStore((s) => s.username);
  const authEnabled = useAuthStore((s) => s.authEnabled);
  const refresh = useAuthStore((s) => s.refresh);
  const attach = useAuthStore((s) => s.attachUnauthorizedListener);
  const [refreshed, setRefreshed] = useState(false);
  const location = useLocation();

  useEffect(() => {
    attach();
    void refresh().finally(() => setRefreshed(true));
  }, [attach, refresh]);

  // While we haven't talked to /auth/me yet we render a tiny placeholder —
  // otherwise we'd briefly redirect users to /login even when auth is off.
  if (!refreshed && authEnabled === null) {
    return (
      <div className="min-h-[40vh] flex items-center justify-center text-slate-400 text-sm">
        Initialisation de la session…
      </div>
    );
  }

  if (authEnabled === false) return <>{children}</>;
  if (!username) {
    return (
      <Navigate to="/login" replace state={{ from: location.pathname }} />
    );
  }
  return <>{children}</>;
}
