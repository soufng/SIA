import { useEffect, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import QRCode from "react-qr-code";
import {
  ArrowLeft,
  ChevronDown,
  ClipboardCheck,
  ClipboardCopy,
  Loader2,
  LogIn,
  ShieldCheck,
  Smartphone,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Alert } from "@/components/ui/alert";
import { LogoLockup } from "@/components/Logo";
import { useAuthStore } from "@/store/auth";
import { cn } from "@/lib/utils";

type Step = "credentials" | "otp";

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const username = useAuthStore((s) => s.username);
  const authEnabled = useAuthStore((s) => s.authEnabled);
  const loginAction = useAuthStore((s) => s.login);
  const loading = useAuthStore((s) => s.loading);
  const storeError = useAuthStore((s) => s.error);
  const otpRequired = useAuthStore((s) => s.otpRequired);
  const enrollment = useAuthStore((s) => s.otpEnrollment);
  const clearOtp = useAuthStore((s) => s.clearOtpRequirement);
  const refresh = useAuthStore((s) => s.refresh);

  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [otpCode, setOtpCode] = useState("");
  const [step, setStep] = useState<Step>("credentials");
  const [localError, setLocalError] = useState<string | null>(null);
  const [showEnrollment, setShowEnrollment] = useState(false);
  const [secretCopied, setSecretCopied] = useState(false);

  const redirectTo = (location.state as { from?: string } | null)?.from ?? "/";

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Keep step in sync with the store's otpRequired flag.
  useEffect(() => {
    if (otpRequired) setStep("otp");
  }, [otpRequired]);

  if (authEnabled === false) return <Navigate to={redirectTo} replace />;
  if (username) return <Navigate to={redirectTo} replace />;

  const submitCredentials = async (e: React.FormEvent) => {
    e.preventDefault();
    setLocalError(null);
    const outcome = await loginAction(user.trim(), password);
    if (outcome.kind === "ok") {
      navigate(redirectTo, { replace: true });
    } else if (outcome.kind === "otp_required") {
      setStep("otp");
      setOtpCode("");
    } else {
      setLocalError(outcome.message);
    }
  };

  const submitOTP = async (e: React.FormEvent) => {
    e.preventDefault();
    setLocalError(null);
    const outcome = await loginAction(user.trim(), password, otpCode.trim());
    if (outcome.kind === "ok") {
      navigate(redirectTo, { replace: true });
    } else if (outcome.kind === "error") {
      setLocalError(outcome.message);
      setOtpCode("");
    }
  };

  const backToCredentials = () => {
    clearOtp();
    setStep("credentials");
    setOtpCode("");
    setLocalError(null);
    setShowEnrollment(false);
  };

  const copySecret = async () => {
    if (!enrollment?.secret) return;
    try {
      await navigator.clipboard.writeText(enrollment.secret);
      setSecretCopied(true);
      setTimeout(() => setSecretCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };

  const error = localError ?? storeError;

  return (
    <div className="min-h-screen flex items-center justify-center bg-ccm-parchment p-4">
      <div className="w-full max-w-md space-y-6">
        <div className="flex flex-col items-center gap-3">
          <LogoLockup width={140} />
          <h1 className="text-2xl font-bold text-ccm-ink">
            Connexion à la plateforme
          </h1>
          <p className="text-sm text-slate-500 text-center">
            Système de pré-modération de scénarios — Centre Cinématographique
            Marocain
          </p>
        </div>

        {step === "credentials" && (
          <form
            onSubmit={submitCredentials}
            className="rounded-xl border border-slate-200 bg-white p-6 shadow-ccm-soft space-y-4"
          >
            <div className="flex items-center gap-2 text-sm font-medium text-ccm-ink">
              <ShieldCheck className="h-4 w-4 text-ccm-red" />
              Étape 1 sur 2 — Identifiants
            </div>

            {error && <Alert variant="error">{error}</Alert>}

            <div className="space-y-1">
              <label
                htmlFor="username"
                className="text-xs font-medium text-slate-600"
              >
                Nom d'utilisateur
              </label>
              <Input
                id="username"
                value={user}
                onChange={(e) => setUser(e.target.value)}
                placeholder="admin"
                autoComplete="username"
                required
                disabled={loading}
                autoFocus
              />
            </div>

            <div className="space-y-1">
              <label
                htmlFor="password"
                className="text-xs font-medium text-slate-600"
              >
                Mot de passe
              </label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                autoComplete="current-password"
                required
                disabled={loading}
              />
            </div>

            <Button
              type="submit"
              className="w-full"
              disabled={loading || !user || !password}
            >
              {loading ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Connexion en cours…
                </>
              ) : (
                <>
                  <LogIn className="h-4 w-4" />
                  Se connecter
                </>
              )}
            </Button>

            <p className="text-[11px] text-slate-400 text-center pt-1">
              Identifiants par défaut (dev) :{" "}
              <code className="font-mono">admin</code> /{" "}
              <code className="font-mono">admin</code>. À changer en
              production via <code>SPM_ADMIN_PASSWORD_HASH</code>.
            </p>
          </form>
        )}

        {step === "otp" && (
          <form
            onSubmit={submitOTP}
            className="rounded-xl border border-slate-200 bg-white p-6 shadow-ccm-soft space-y-4"
          >
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-sm font-medium text-ccm-ink">
                <Smartphone className="h-4 w-4 text-ccm-red" />
                Étape 2 sur 2 — Vérification 2FA
              </div>
              <button
                type="button"
                onClick={backToCredentials}
                className="text-xs text-slate-500 hover:text-ccm-red inline-flex items-center gap-1"
              >
                <ArrowLeft className="h-3 w-3" />
                Retour
              </button>
            </div>

            <p className="text-sm text-slate-600">
              Ouvre ton application d'authentification (Google Authenticator,
              Microsoft Authenticator, Authy, …) et saisis le code à 6
              chiffres affiché pour <strong>{user}</strong>.
            </p>

            {error && <Alert variant="error">{error}</Alert>}

            <div className="space-y-1">
              <label
                htmlFor="otp"
                className="text-xs font-medium text-slate-600"
              >
                Code de vérification
              </label>
              <Input
                id="otp"
                type="text"
                inputMode="numeric"
                pattern="[0-9 -]*"
                value={otpCode}
                onChange={(e) => setOtpCode(e.target.value)}
                placeholder="123 456"
                maxLength={12}
                autoComplete="one-time-code"
                required
                disabled={loading}
                autoFocus
                className="text-center text-2xl tracking-[0.4em] font-mono"
              />
              <p className="text-[11px] text-slate-400 text-center pt-1">
                Le code change toutes les 30 secondes.
              </p>
            </div>

            <Button
              type="submit"
              className="w-full"
              disabled={loading || otpCode.replace(/\D/g, "").length < 6}
            >
              {loading ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Vérification…
                </>
              ) : (
                <>
                  <ShieldCheck className="h-4 w-4" />
                  Valider le code
                </>
              )}
            </Button>

            {/* First-time enrolment: show the QR + manual fallback so the
                user can configure Google Authenticator / Authy without
                having to ask an admin. */}
            {enrollment && (
              <div className="pt-2 border-t border-slate-100">
                <button
                  type="button"
                  onClick={() => setShowEnrollment((v) => !v)}
                  className="w-full flex items-center justify-between gap-2 text-xs text-slate-600 hover:text-ccm-red"
                >
                  <span className="inline-flex items-center gap-1.5">
                    <Smartphone className="h-3.5 w-3.5" />
                    Première utilisation ? Configurer mon application
                  </span>
                  <ChevronDown
                    className={cn(
                      "h-3.5 w-3.5 transition-transform",
                      showEnrollment && "rotate-180"
                    )}
                  />
                </button>

                {showEnrollment && (
                  <div className="mt-3 space-y-3">
                    <p className="text-xs text-slate-600 leading-relaxed">
                      Ouvre Google Authenticator (ou Microsoft
                      Authenticator, Authy, 1Password…), touche{" "}
                      <strong>« + »</strong> puis{" "}
                      <strong>« Scanner un QR code »</strong> et vise
                      l'image ci-dessous. Le code à 6 chiffres apparaîtra
                      immédiatement.
                    </p>

                    <div className="flex justify-center bg-white border border-slate-200 rounded-md p-3">
                      <QRCode
                        value={enrollment.provisioningUri}
                        size={180}
                        level="M"
                        fgColor="#1A1A1A"
                        bgColor="#FFFFFF"
                      />
                    </div>

                    <div className="rounded-md bg-amber-50 border border-amber-200 p-3 text-[11px] text-amber-900 space-y-1">
                      <p className="font-semibold">
                        Tu ne peux pas scanner ?
                      </p>
                      <p>
                        Ajoute manuellement avec :
                        <br />
                        Compte :{" "}
                        <span className="font-mono">
                          {enrollment.account}
                        </span>
                        {" · "}Émetteur :{" "}
                        <span className="font-mono">{enrollment.issuer}</span>
                        {" · "}SHA1 · 6 chiffres · 30 s
                      </p>
                      {enrollment.secret && (
                        <div className="flex items-stretch gap-2 mt-2">
                          <code className="flex-1 font-mono text-[11px] bg-white border border-amber-200 rounded px-2 py-1 break-all">
                            {enrollment.secret}
                          </code>
                          <button
                            type="button"
                            onClick={copySecret}
                            className="inline-flex items-center gap-1 px-2 rounded border border-amber-300 hover:bg-amber-100 text-amber-900"
                          >
                            {secretCopied ? (
                              <>
                                <ClipboardCheck className="h-3.5 w-3.5 text-emerald-700" />
                                Copié
                              </>
                            ) : (
                              <>
                                <ClipboardCopy className="h-3.5 w-3.5" />
                                Copier
                              </>
                            )}
                          </button>
                        </div>
                      )}
                    </div>

                    <p className="text-[11px] text-slate-400 text-center">
                      Une fois l'application configurée, saisis le code
                      affiché ci-dessus pour finaliser la connexion.
                    </p>
                  </div>
                )}
              </div>
            )}
          </form>
        )}
      </div>
    </div>
  );
}
