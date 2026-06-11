import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import QRCode from "react-qr-code";
import {
  ClipboardCheck,
  ClipboardCopy,
  Loader2,
  ShieldCheck,
  Smartphone,
} from "lucide-react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { fetchOTPSetup, type OTPSetupResponse } from "@/lib/api";

export function OTPSetupPage() {
  const navigate = useNavigate();
  const [data, setData] = useState<OTPSetupResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<"secret" | "uri" | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchOTPSetup()
      .then((r) => {
        if (!cancelled) setData(r);
      })
      .catch((e) => {
        if (!cancelled) setError((e as Error).message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const copy = async (value: string, kind: "secret" | "uri") => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(kind);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-bold text-ccm-ink flex items-center gap-3">
          <ShieldCheck className="h-7 w-7 text-ccm-red" />
          Configuration de la 2FA (TOTP)
        </h1>
        <p className="text-slate-500 mt-1 text-sm">
          Scanne le QR code avec ton application d'authentification (Google
          Authenticator, Microsoft Authenticator, Authy, 1Password…). À la
          prochaine connexion, on te demandera un code à 6 chiffres.
        </p>
      </header>

      {loading && (
        <Card>
          <CardContent className="py-12 text-center text-slate-500">
            <Loader2 className="h-5 w-5 animate-spin mx-auto mb-2 text-ccm-red" />
            Chargement de la configuration OTP…
          </CardContent>
        </Card>
      )}

      {error && (
        <Alert variant="error">
          <p className="font-semibold mb-1">
            Impossible de récupérer la configuration OTP.
          </p>
          <p className="text-xs font-mono">{error}</p>
          <p className="text-xs mt-2 text-slate-600">
            Vérifie que <code>SPM_OTP_SECRET</code> est défini côté serveur
            (génère-le avec{" "}
            <code>python -m backend.core.totp generate</code>).
          </p>
        </Alert>
      )}

      {data && (
        <>
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <CardTitle className="flex items-center gap-2">
                  <Smartphone className="h-5 w-5 text-ccm-red" />
                  QR code à scanner
                </CardTitle>
                <Badge
                  className={
                    data.enabled
                      ? "bg-emerald-100 text-emerald-700 border-emerald-200"
                      : "bg-amber-100 text-amber-800 border-amber-200"
                  }
                >
                  {data.enabled
                    ? "2FA active (login obligatoire avec code)"
                    : "2FA inactive (le serveur n'exige pas encore le code)"}
                </Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex flex-col md:flex-row items-start gap-6">
                <div className="bg-white p-3 border border-slate-200 rounded-md">
                  <QRCode
                    value={data.provisioning_uri}
                    size={200}
                    level="M"
                    fgColor="#1A1A1A"
                    bgColor="#FFFFFF"
                  />
                </div>
                <div className="flex-1 space-y-3 text-sm">
                  <ol className="list-decimal list-inside space-y-1.5 text-slate-700">
                    <li>Ouvre ton application d'authentification.</li>
                    <li>
                      Touche « + » puis « Scanner un QR code » et vise
                      l'image à gauche.
                    </li>
                    <li>
                      Une entrée «{" "}
                      <span className="font-mono">
                        {data.issuer}:{data.account}
                      </span>{" "}
                      » apparaît avec un code à 6 chiffres qui se rafraîchit
                      toutes les 30 s.
                    </li>
                    <li>
                      À la prochaine connexion, après ton mot de passe, on te
                      demandera ce code.
                    </li>
                  </ol>
                </div>
              </div>

              <Alert variant="info">
                <p className="text-xs">
                  <strong>Si tu ne peux pas scanner</strong>, ajoute l'entrée
                  manuellement avec ces paramètres :
                </p>
                <ul className="text-xs mt-1 list-disc list-inside font-mono">
                  <li>
                    Compte : <strong>{data.account}</strong>
                  </li>
                  <li>
                    Émetteur : <strong>{data.issuer}</strong>
                  </li>
                  <li>Type : code temporel (TOTP)</li>
                  <li>Algorithme : SHA1 — 6 chiffres — 30 s</li>
                </ul>
              </Alert>

              <div className="space-y-1.5">
                <label className="text-xs font-medium text-slate-600 uppercase tracking-wide">
                  Clé secrète (base32)
                </label>
                <div className="flex items-stretch gap-2">
                  <code className="flex-1 font-mono text-sm bg-slate-50 border border-slate-200 rounded-md px-3 py-2 break-all">
                    {data.secret}
                  </code>
                  <Button
                    variant="outline"
                    onClick={() => copy(data.secret, "secret")}
                  >
                    {copied === "secret" ? (
                      <ClipboardCheck className="h-4 w-4 text-emerald-600" />
                    ) : (
                      <ClipboardCopy className="h-4 w-4" />
                    )}
                  </Button>
                </div>
                <p className="text-[11px] text-slate-500">
                  Ne partage cette clé avec personne. Quiconque la possède
                  peut générer tes codes 2FA.
                </p>
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-medium text-slate-600 uppercase tracking-wide">
                  URI de provisionnement
                </label>
                <div className="flex items-stretch gap-2">
                  <code className="flex-1 font-mono text-xs bg-slate-50 border border-slate-200 rounded-md px-3 py-2 break-all">
                    {data.provisioning_uri}
                  </code>
                  <Button
                    variant="outline"
                    onClick={() => copy(data.provisioning_uri, "uri")}
                  >
                    {copied === "uri" ? (
                      <ClipboardCheck className="h-4 w-4 text-emerald-600" />
                    ) : (
                      <ClipboardCopy className="h-4 w-4" />
                    )}
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>

          <div className="flex justify-end">
            <Button variant="outline" onClick={() => navigate("/")}>
              Retour à l'accueil
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
