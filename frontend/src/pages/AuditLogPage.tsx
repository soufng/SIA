import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  FileText,
  KeyRound,
  Loader2,
  RefreshCcw,
  ScrollText,
  ShieldAlert,
  Trash2,
  UserPlus,
  XCircle,
} from "lucide-react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TBody, THead, Td, Th, Tr } from "@/components/ui/table";
import { fetchAuditLog } from "@/lib/api";
import type { AuditLogEvent } from "@/lib/types";
import { useAuthStore } from "@/store/auth";

const EVENT_TYPES: { value: string; label: string }[] = [
  { value: "", label: "Tous les événements" },
  { value: "login_success", label: "Connexion réussie" },
  { value: "login_failure", label: "Connexion échouée" },
  { value: "scenario_upload", label: "Upload de scénario" },
  { value: "scenario_view", label: "Consultation de scénario" },
  { value: "user_created", label: "Utilisateur créé" },
  { value: "user_deleted", label: "Utilisateur supprimé" },
  { value: "user_role_changed", label: "Rôle modifié" },
  { value: "user_password_changed", label: "Mot de passe changé" },
];

function eventMeta(eventType: string): {
  label: string;
  icon: typeof FileText;
  tone: "success" | "warning" | "danger" | "info";
} {
  switch (eventType) {
    case "login_success":
      return { label: "Connexion réussie", icon: CheckCircle2, tone: "success" };
    case "login_failure":
      return { label: "Connexion échouée", icon: XCircle, tone: "danger" };
    case "scenario_upload":
      return { label: "Upload scénario", icon: FileText, tone: "info" };
    case "scenario_view":
      return { label: "Consultation scénario", icon: FileText, tone: "info" };
    case "user_created":
      return { label: "Utilisateur créé", icon: UserPlus, tone: "info" };
    case "user_deleted":
      return { label: "Utilisateur supprimé", icon: Trash2, tone: "warning" };
    case "user_role_changed":
      return { label: "Rôle modifié", icon: ShieldAlert, tone: "warning" };
    case "user_password_changed":
      return { label: "Mot de passe changé", icon: KeyRound, tone: "warning" };
    default:
      return { label: eventType, icon: AlertTriangle, tone: "info" };
  }
}

function toneBadgeClass(tone: "success" | "warning" | "danger" | "info"): string {
  switch (tone) {
    case "success":
      return "bg-emerald-100 text-emerald-700 border-emerald-200";
    case "danger":
      return "bg-red-100 text-red-700 border-red-200";
    case "warning":
      return "bg-amber-100 text-amber-700 border-amber-200";
    case "info":
    default:
      return "bg-slate-100 text-slate-700 border-slate-200";
  }
}

export function AuditLogPage() {
  const role = useAuthStore((s) => s.role);

  const [eventType, setEventType] = useState<string>("");
  const [usernameFilter, setUsernameFilter] = useState<string>("");
  const [limit, setLimit] = useState<number>(100);

  const params = useMemo(
    () => ({
      limit,
      event_type: eventType || undefined,
      username: usernameFilter.trim() || undefined,
    }),
    [eventType, usernameFilter, limit],
  );

  const query = useQuery({
    queryKey: ["admin", "audit-log", params],
    queryFn: () => fetchAuditLog(params),
    staleTime: 5_000,
  });

  if (role && role !== "admin") {
    return (
      <div className="space-y-4">
        <h1 className="text-3xl font-bold text-ccm-ink">Journal d'audit</h1>
        <Alert variant="error">
          Cette page est réservée aux administrateurs.
        </Alert>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold text-ccm-ink flex items-center gap-2">
            <ScrollText className="h-7 w-7 text-ccm-red" />
            Journal d'audit
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Trace immuable des actions sensibles : connexions, uploads de
            scénarios, modifications de comptes.
          </p>
        </div>
        <Button
          variant="outline"
          onClick={() => query.refetch()}
          disabled={query.isFetching}
        >
          {query.isFetching ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <RefreshCcw className="h-4 w-4" />
          )}
          Rafraîchir
        </Button>
      </header>

      <Card>
        <CardContent className="pt-6">
          <div className="grid gap-3 sm:grid-cols-[2fr_2fr_120px]">
            <div>
              <label className="text-xs font-medium text-slate-600">
                Type d'événement
              </label>
              <select
                value={eventType}
                onChange={(e) => setEventType(e.target.value)}
                className="block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
              >
                {EVENT_TYPES.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-xs font-medium text-slate-600">
                Utilisateur
              </label>
              <Input
                value={usernameFilter}
                onChange={(e) => setUsernameFilter(e.target.value)}
                placeholder="Nom d'utilisateur (laisser vide pour tout afficher)"
                autoComplete="off"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-600">
                Limite
              </label>
              <select
                value={limit}
                onChange={(e) => setLimit(parseInt(e.target.value, 10))}
                className="block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
              >
                {[25, 50, 100, 200, 500].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-0">
          {query.isLoading ? (
            <p className="px-4 py-6 text-sm text-slate-500">Chargement...</p>
          ) : query.isError ? (
            <Alert variant="error">
              Impossible de charger le journal : {(query.error as Error).message}
            </Alert>
          ) : (query.data ?? []).length === 0 ? (
            <p className="px-4 py-6 text-sm text-slate-500">
              Aucun événement correspondant aux filtres.
            </p>
          ) : (
            <Table>
              <THead>
                <Tr>
                  <Th className="w-44">Horodatage</Th>
                  <Th className="w-56">Événement</Th>
                  <Th className="w-40">Acteur</Th>
                  <Th className="w-40">Cible</Th>
                  <Th>Détails</Th>
                </Tr>
              </THead>
              <TBody>
                {(query.data ?? []).map((event) => (
                  <AuditRow key={event.event_id} event={event} />
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function AuditRow({ event }: { event: AuditLogEvent }) {
  const meta = eventMeta(event.event_type);
  const Icon = meta.icon;
  const details = event.details ?? {};
  return (
    <Tr>
      <Td>
        <span className="text-xs font-mono text-slate-700">
          {new Date(event.timestamp).toLocaleString("fr-FR")}
        </span>
        {event.ip && (
          <p className="text-[10px] font-mono text-slate-400 mt-0.5">
            IP : {event.ip}
          </p>
        )}
      </Td>
      <Td>
        <Badge className={`gap-1 ${toneBadgeClass(meta.tone)}`}>
          <Icon className="h-3 w-3" />
          {meta.label}
        </Badge>
      </Td>
      <Td>
        <div className="flex flex-col">
          <span className="text-sm text-ccm-ink">
            {event.username ?? "—"}
          </span>
          {event.user_id && (
            <span className="font-mono text-[10px] text-slate-400">
              {event.user_id}
            </span>
          )}
        </div>
      </Td>
      <Td>
        {event.target_id ? (
          <span className="font-mono text-[11px] text-slate-600 break-all">
            {event.target_id}
          </span>
        ) : (
          <span className="text-xs text-slate-400">—</span>
        )}
      </Td>
      <Td>
        {Object.keys(details).length === 0 ? (
          <span className="text-xs text-slate-400">—</span>
        ) : (
          <ul className="space-y-0.5 text-xs">
            {Object.entries(details).map(([k, v]) => (
              <li key={k}>
                <span className="font-medium text-slate-600">{k}</span> :{" "}
                <span className="font-mono text-slate-700">
                  {typeof v === "string" ? v : JSON.stringify(v)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Td>
    </Tr>
  );
}
