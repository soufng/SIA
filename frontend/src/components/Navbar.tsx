import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import { useNavigate } from "react-router-dom";
import {
  BarChart3,
  Check,
  FileSearch,
  History,
  Home,
  LogOut,
  Menu,
  PlugZap,
  ScrollText,
  Settings,
  ShieldCheck,
  Trash2,
  Users,
  X,
} from "lucide-react";
import { Logo } from "./Logo";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { checkHealth, getBaseUrl, setBaseUrl } from "@/lib/api";
import { useAnalysisStore } from "@/store/analysis";
import { useAuthStore } from "@/store/auth";
import { cn } from "@/lib/utils";

const baseItems = [
  { to: "/", label: "Accueil", icon: Home },
  { to: "/results", label: "Résultats", icon: FileSearch },
  { to: "/history", label: "Historique", icon: History },
  { to: "/analytics", label: "Statistiques", icon: BarChart3 },
];

const adminItems = [
  { to: "/admin/users", label: "Utilisateurs", icon: Users },
  { to: "/admin/audit-log", label: "Journal d'audit", icon: ScrollText },
];

export function Navbar() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const role = useAuthStore((s) => s.role);
  const items = role === "admin" ? [...baseItems, ...adminItems] : baseItems;

  return (
    <header className="sticky top-0 z-40 border-b border-white/5 backdrop-blur-xl">
      {/* Layered background — same DNA as the hero so the whole page
          reads as a coherent CCM-branded identity. */}
      <div className="absolute inset-0 bg-[linear-gradient(135deg,#1A1A1A_0%,#8E1B22_100%)]" />
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top_left,_rgba(193,39,45,0.55),_transparent_60%)]" />
      <div className="pointer-events-none absolute -top-10 left-1/4 h-24 w-24 rounded-full bg-ccm-gold/15 blur-2xl" />
      {/* Bottom accent line — same gradient as the hero footer. */}
      <div className="absolute inset-x-0 bottom-0 h-px bg-gradient-to-r from-transparent via-ccm-gold to-transparent" />

      <div className="relative mx-auto flex h-16 max-w-7xl items-center gap-4 px-4 md:px-8">
        <NavLink
          to="/"
          className="group flex items-center gap-3 rounded-lg transition-transform hover:scale-[1.02]"
        >
          <span className="relative inline-flex h-10 w-10 items-center justify-center rounded-lg bg-white/10 ring-1 ring-white/15 backdrop-blur transition-colors group-hover:bg-white/15">
            <Logo size={28} withText={false} />
          </span>
          <span className="hidden flex-col leading-tight sm:flex">
            <span className="bg-gradient-to-r from-white to-ccm-gold bg-clip-text text-base font-bold tracking-tight text-transparent">
              SIA <span className="text-white/40">/ CCM</span>
            </span>
            <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-white/55">
              Centre Cinématographique
            </span>
          </span>
        </NavLink>

        <nav className="ml-6 hidden items-center gap-0.5 md:flex">
          {items.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                cn(
                  "group relative flex items-center gap-2 whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium transition-all",
                  isActive
                    ? "bg-white/10 text-white shadow-inner shadow-ccm-red-dark/30 ring-1 ring-white/10"
                    : "text-white/70 hover:bg-white/5 hover:text-white",
                )
              }
            >
              {({ isActive }) => (
                <>
                  <Icon
                    className={cn(
                      "h-4 w-4 transition-colors",
                      isActive ? "text-ccm-gold" : "text-white/60 group-hover:text-white",
                    )}
                  />
                  {label}
                  {isActive && (
                    <span className="absolute inset-x-3 -bottom-px h-0.5 rounded-full bg-gradient-to-r from-transparent via-ccm-gold to-transparent" />
                  )}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-1.5">
          <UserMenu />
          <BackendSettings open={settingsOpen} setOpen={setSettingsOpen} />
          <button
            type="button"
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-white/85 ring-1 ring-white/10 backdrop-blur transition-colors hover:bg-white/10 hover:text-white md:hidden"
            onClick={() => setMobileOpen((o) => !o)}
            aria-label="Menu"
          >
            {mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
        </div>
      </div>

      {mobileOpen && (
        <nav className="relative space-y-1 border-t border-white/5 bg-ccm-ink/95 px-4 py-3 backdrop-blur-xl md:hidden">
          {items.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              onClick={() => setMobileOpen(false)}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-white/10 text-white ring-1 ring-white/10"
                    : "text-white/70 hover:bg-white/5 hover:text-white",
                )
              }
            >
              {({ isActive }) => (
                <>
                  <Icon
                    className={cn(
                      "h-4 w-4",
                      isActive ? "text-ccm-gold" : "text-white/60",
                    )}
                  />
                  {label}
                </>
              )}
            </NavLink>
          ))}
        </nav>
      )}
    </header>
  );
}

function BackendSettings({
  open,
  setOpen,
}: {
  open: boolean;
  setOpen: (v: boolean) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [baseUrl, setUrl] = useState(getBaseUrl());
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<
    { kind: "ok" | "err"; message: string } | null
  >(null);
  const reset = useAnalysisStore((s) => s.reset);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open, setOpen]);

  const saveAndTest = async () => {
    setBaseUrl(baseUrl);
    setStatus(null);
    setLoading(true);
    try {
      const h = await checkHealth();
      setStatus({
        kind: "ok",
        message: h.message ?? "Backend operationnel.",
      });
    } catch (e) {
      setStatus({ kind: "err", message: (e as Error).message });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        className={cn(
          "inline-flex h-9 items-center gap-2 rounded-lg px-3 text-sm font-medium text-white/80 ring-1 ring-white/10 backdrop-blur transition-colors hover:bg-white/10 hover:text-white",
          open && "bg-white/10 text-white",
        )}
        onClick={() => setOpen(!open)}
      >
        <Settings className="h-4 w-4" />
        <span className="hidden sm:inline">Backend</span>
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-80 rounded-lg border border-slate-200 bg-white p-4 shadow-xl text-ccm-ink">
          <p className="text-xs font-semibold uppercase tracking-wide text-ccm-red">
            Configuration backend
          </p>
          <label className="mt-3 block text-xs font-medium text-slate-600">
            URL FastAPI
          </label>
          <Input
            value={baseUrl}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="http://127.0.0.1:8000"
            className="mt-1"
          />
          <Button
            variant="outline"
            className="mt-3 w-full"
            onClick={saveAndTest}
            disabled={loading}
          >
            <PlugZap className="h-4 w-4" />
            {loading ? "Test en cours..." : "Tester la connexion"}
          </Button>
          {status && (
            <p
              className={cn(
                "mt-2 flex items-start gap-1 text-xs",
                status.kind === "ok" ? "text-emerald-700" : "text-red-700"
              )}
            >
              <Check className="h-3.5 w-3.5 mt-0.5 shrink-0" />
              {status.message}
            </p>
          )}
          <hr className="my-3 border-slate-200" />
          <Button
            variant="ghost"
            className="w-full justify-start text-slate-600"
            onClick={() => {
              reset();
              setOpen(false);
            }}
          >
            <Trash2 className="h-4 w-4" />
            Effacer la session
          </Button>
        </div>
      )}
    </div>
  );
}

function UserMenu() {
  const navigate = useNavigate();
  const ref = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const username = useAuthStore((s) => s.username);
  const authEnabled = useAuthStore((s) => s.authEnabled);
  const expiresAt = useAuthStore((s) => s.expiresAt);
  const logout = useAuthStore((s) => s.logout);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  // If auth is disabled server-side, we don't render the menu at all.
  if (authEnabled === false) return null;
  if (!username) return null;

  const remainingMin = expiresAt
    ? Math.max(0, Math.round((expiresAt - Date.now()) / 60000))
    : null;

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className={cn(
          "inline-flex h-9 items-center gap-2 rounded-lg px-2.5 text-sm font-medium text-white/90 ring-1 ring-white/10 backdrop-blur transition-colors hover:bg-white/10 hover:text-white",
          open && "bg-white/10 text-white",
        )}
      >
        <span className="inline-flex h-6 w-6 items-center justify-center rounded-md bg-gradient-to-br from-ccm-red-light to-ccm-red-dark text-[10px] font-bold text-white shadow-sm ring-1 ring-ccm-gold/40">
          {username.slice(0, 1).toUpperCase()}
        </span>
        <span className="hidden sm:inline">{username}</span>
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-72 rounded-lg border border-slate-200 bg-white p-4 shadow-xl text-ccm-ink z-10">
          <p className="text-xs uppercase tracking-wide text-ccm-red font-semibold">
            Session active
          </p>
          <p className="mt-2 text-sm">
            Connecté en tant que{" "}
            <span className="font-semibold">{username}</span>
          </p>
          {remainingMin != null && (
            <p className="text-xs text-slate-500 mt-1">
              Session valide encore {remainingMin} min
              {remainingMin <= 10 && (
                <span className="ml-1 text-amber-600">
                  (pense à te reconnecter bientôt)
                </span>
              )}
            </p>
          )}
          <hr className="my-3 border-slate-200" />
          <Button
            variant="ghost"
            className="w-full justify-start text-slate-700"
            onClick={() => {
              setOpen(false);
              navigate("/security/otp");
            }}
          >
            <ShieldCheck className="h-4 w-4" />
            Configurer la 2FA (TOTP)
          </Button>
          <Button
            variant="outline"
            className="w-full justify-center mt-2"
            onClick={() => {
              logout();
              setOpen(false);
              navigate("/login", { replace: true });
            }}
          >
            <LogOut className="h-4 w-4" />
            Se déconnecter
          </Button>
        </div>
      )}
    </div>
  );
}
