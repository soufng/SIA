import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  FileText,
  History as HistoryIcon,
  Search,
  ShieldAlert,
  TrendingUp,
  Upload,
} from "lucide-react";
import { fetchHistory } from "@/lib/api";
import type { Analysis, HistoryItem } from "@/lib/types";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { cn, formatRiskLabel, formatScore, riskColor } from "@/lib/utils";
import { useAnalysisStore } from "@/store/analysis";

// ---------- Helpers ----------

type Row = {
  date: string | null;
  scenarioId: string;
  filename: string;
  similarity: number;
  risk: string;
  profanity: number;
  adult: number;
  summary: string;
  wordCount: number;
  chunkCount: number;
  totalMatches: number;
  fullAnalysis: Analysis | null;
  raw: HistoryItem;
};

function getNested(data: unknown, path: string[]): unknown {
  let cur: unknown = data;
  for (const k of path) {
    if (cur && typeof cur === "object" && k in (cur as Record<string, unknown>)) {
      cur = (cur as Record<string, unknown>)[k];
    } else {
      return undefined;
    }
  }
  return cur;
}

function normalize(record: HistoryItem): Row {
  const analysis = (record.analysis ?? record.result ?? record) as Analysis;
  const a = analysis as Record<string, unknown>;
  const get = (path: string[]) => getNested(a, path);
  const stats = (analysis.document_stats ?? {}) as Record<string, unknown>;
  return {
    date:
      (get(["analysis_timestamp"]) as string | undefined) ??
      record.analysis_timestamp ??
      record.created_at ??
      null,
    scenarioId: String(
      get(["scenario_id"]) ?? record.scenario_id ?? "non disponible"
    ),
    filename: String(
      stats.original_filename ??
        record.filename ??
        stats.file_name ??
        "—"
    ),
    similarity: Number(
      get(["plagiarism", "global_similarity_score"]) ?? 0
    ),
    risk: String(get(["rag_report", "risk_level"]) ?? "unknown"),
    profanity: Number(get(["profanity", "profanity_score"]) ?? 0),
    adult: Number(get(["adult_content", "adult_content_score"]) ?? 0),
    summary: String(
      get(["rag_report", "summary"]) ?? "Aucun résumé disponible."
    ),
    wordCount: Number(
      stats.words_count ??
        stats.word_count ??
        (record as unknown as Record<string, unknown>).word_count ??
        0
    ),
    chunkCount: Number(
      stats.chunks_count ??
        stats.chunk_count ??
        (record as unknown as Record<string, unknown>).chunk_count ??
        0
    ),
    totalMatches: Number(
      get(["plagiarism", "total_matches"]) ?? 0
    ),
    fullAnalysis: analysis ?? null,
    raw: record,
  };
}

function formatRelative(value: string | null): string {
  if (!value) return "non disponible";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const now = Date.now();
  const diff = Math.round((now - date.getTime()) / 1000);
  if (diff < 60) return "il y a quelques secondes";
  if (diff < 3600) return `il y a ${Math.floor(diff / 60)} min`;
  if (diff < 86400) return `il y a ${Math.floor(diff / 3600)} h`;
  if (diff < 86400 * 7) return `il y a ${Math.floor(diff / 86400)} j`;
  return date.toLocaleDateString("fr-FR", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatExactDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("fr-FR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ---------- KPI cards ----------

function KpiCard({
  label,
  value,
  hint,
  icon: Icon,
  tone = "neutral",
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  icon: typeof FileText;
  tone?: "neutral" | "red" | "amber" | "emerald";
}) {
  const toneClasses: Record<typeof tone, string> = {
    neutral: "text-slate-600 bg-slate-100",
    red: "text-red-700 bg-red-100",
    amber: "text-amber-700 bg-amber-100",
    emerald: "text-emerald-700 bg-emerald-100",
  };
  return (
    <Card>
      <CardContent className="pt-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs uppercase tracking-wide text-slate-500">
              {label}
            </p>
            <p className="mt-1 text-2xl font-semibold text-slate-900">
              {value}
            </p>
            {hint && (
              <p className="text-xs text-slate-500 mt-0.5">{hint}</p>
            )}
          </div>
          <span
            className={cn(
              "inline-flex h-9 w-9 items-center justify-center rounded-md shrink-0",
              toneClasses[tone]
            )}
          >
            <Icon className="h-4 w-4" />
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------- Score mini-bar ----------

function MiniBar({
  value,
  unit = "%",
}: {
  value: number;
  unit?: "%" | "/100";
}) {
  const pct =
    unit === "%"
      ? Math.max(0, Math.min(100, value <= 1 ? value * 100 : value))
      : Math.max(0, Math.min(100, value));
  const color =
    pct >= 75
      ? "bg-red-500"
      : pct >= 40
        ? "bg-amber-500"
        : pct > 0
          ? "bg-emerald-500"
          : "bg-slate-200";
  const display = unit === "%" ? `${pct.toFixed(2)}%` : `${pct.toFixed(2)} / 100`;
  return (
    <div className="space-y-1">
      <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
        <div
          className={cn("h-full transition-all", color)}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-[11px] font-mono text-slate-600 tabular-nums">
        {display}
      </p>
    </div>
  );
}

// ---------- History row ----------

function HistoryRow({
  row,
  expanded,
  onToggle,
  onOpen,
}: {
  row: Row;
  expanded: boolean;
  onToggle: () => void;
  onOpen: () => void;
}) {
  const riskUpper = formatRiskLabel(row.risk);
  const riskKey = String(row.risk || "").toLowerCase();
  return (
    <Card className="overflow-hidden">
      <button
        type="button"
        onClick={onToggle}
        className="w-full p-4 text-left flex flex-wrap gap-3 items-center hover:bg-slate-50/60 transition-colors"
      >
        {/* Left: icon + name + meta */}
        <div className="flex items-start gap-3 min-w-0 flex-1">
          <span
            className={cn(
              "inline-flex h-10 w-10 items-center justify-center rounded-md shrink-0",
              (riskKey === "high" || riskKey === "tres_eleve") &&
                "bg-red-100 text-red-700",
              riskKey === "medium" && "bg-amber-100 text-amber-700",
              riskKey === "low" && "bg-emerald-100 text-emerald-700",
              !["high", "tres_eleve", "medium", "low"].includes(riskKey) &&
                "bg-slate-100 text-slate-600"
            )}
          >
            <FileText className="h-5 w-5" />
          </span>
          <div className="min-w-0">
            <p
              className="font-medium text-slate-900 truncate"
              title={row.filename}
            >
              {row.filename}
            </p>
            <p className="text-xs text-slate-500 flex items-center gap-2 mt-0.5">
              <span title={formatExactDate(row.date) ?? undefined}>
                {formatRelative(row.date)}
              </span>
              <span className="text-slate-300">•</span>
              <span className="font-mono truncate" title={row.scenarioId}>
                {row.scenarioId.slice(0, 8)}…
              </span>
              {row.wordCount > 0 && (
                <>
                  <span className="text-slate-300">•</span>
                  <span>{row.wordCount.toLocaleString("fr-FR")} mots</span>
                </>
              )}
            </p>
          </div>
        </div>

        {/* Right: badges + chevron */}
        <div className="flex items-center gap-3 flex-wrap">
          <Badge className={riskColor(row.risk)}>{riskUpper}</Badge>
          <div className="hidden md:flex items-center gap-1 text-xs text-slate-500">
            <Search className="h-3.5 w-3.5" />
            <span className="font-mono">
              {(row.similarity <= 1 ? row.similarity * 100 : row.similarity).toFixed(2)}%
            </span>
          </div>
          <ChevronDown
            className={cn(
              "h-4 w-4 text-slate-400 transition-transform",
              expanded && "rotate-180"
            )}
          />
        </div>
      </button>

      {expanded && (
        <CardContent className="border-t border-slate-100 bg-slate-50/30 space-y-4">
          {/* Scores */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <p className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">
                Similarité globale
              </p>
              <MiniBar value={row.similarity} unit="%" />
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">
                Vulgarité
              </p>
              <MiniBar value={row.profanity} unit="/100" />
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">
                Contenu adulte
              </p>
              <MiniBar value={row.adult} unit="/100" />
            </div>
          </div>

          {/* Detail rows */}
          <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-sm">
            <div className="flex justify-between gap-3">
              <dt className="text-slate-500">Date</dt>
              <dd className="font-mono text-xs text-slate-800">
                {formatExactDate(row.date)}
              </dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-slate-500">Scénario ID</dt>
              <dd
                className="font-mono text-xs text-slate-800 truncate"
                title={row.scenarioId}
              >
                {row.scenarioId}
              </dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-slate-500">Chunks</dt>
              <dd className="font-mono text-xs text-slate-800">
                {row.chunkCount}
              </dd>
            </div>
            {row.totalMatches > 0 && (
              <div className="flex justify-between gap-3">
                <dt className="text-slate-500">Passages similaires</dt>
                <dd className="font-mono text-xs text-slate-800">
                  {row.totalMatches}
                </dd>
              </div>
            )}
          </dl>

          {/* Summary */}
          <div className="rounded-md bg-white border border-slate-200 p-3">
            <p className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">
              Résumé RAG
            </p>
            <p
              className="text-sm text-slate-700 leading-relaxed"
              dir="auto"
            >
              {row.summary}
            </p>
          </div>

          {/* Action */}
          <div className="flex justify-end">
            <Button
              onClick={onOpen}
              disabled={!row.fullAnalysis}
            >
              Ouvrir le rapport complet
              <ArrowRight className="h-4 w-4" />
            </Button>
          </div>
        </CardContent>
      )}
    </Card>
  );
}

// ---------- Page ----------

export function HistoryPage() {
  const navigate = useNavigate();
  const setAnalysis = useAnalysisStore((s) => s.setAnalysis);
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["history"],
    queryFn: () => fetchHistory(50),
  });

  const [openIndex, setOpenIndex] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [riskFilter, setRiskFilter] = useState<
    "all" | "high" | "medium" | "low"
  >("all");
  const [sortBy, setSortBy] = useState<"date" | "similarity" | "risk">(
    "date"
  );

  const rows = useMemo(
    () => (data ?? []).map((rec) => normalize(rec)),
    [data]
  );

  const filtered = useMemo(() => {
    let list = rows;
    if (query.trim()) {
      const q = query.toLowerCase();
      list = list.filter(
        (r) =>
          r.filename.toLowerCase().includes(q) ||
          r.scenarioId.toLowerCase().includes(q) ||
          r.summary.toLowerCase().includes(q)
      );
    }
    if (riskFilter !== "all") {
      list = list.filter((r) => r.risk.toLowerCase() === riskFilter);
    }
    list = [...list].sort((a, b) => {
      if (sortBy === "similarity") return b.similarity - a.similarity;
      if (sortBy === "risk") {
        const order: Record<string, number> = {
          high: 0,
          medium: 1,
          low: 2,
          unknown: 3,
        };
        return (
          (order[a.risk.toLowerCase()] ?? 4) -
          (order[b.risk.toLowerCase()] ?? 4)
        );
      }
      // date desc by default
      const ta = a.date ? new Date(a.date).getTime() : 0;
      const tb = b.date ? new Date(b.date).getTime() : 0;
      return tb - ta;
    });
    return list;
  }, [rows, query, riskFilter, sortBy]);

  const kpis = useMemo(() => {
    const total = rows.length;
    const byRisk = { high: 0, medium: 0, low: 0 };
    let scoreSum = 0;
    let last7Days = 0;
    const sevenDaysAgo = Date.now() - 7 * 86400_000;
    for (const r of rows) {
      const k = r.risk.toLowerCase();
      if (k === "high" || k === "medium" || k === "low") byRisk[k]++;
      scoreSum +=
        r.similarity <= 1 ? r.similarity * 100 : r.similarity;
      if (r.date) {
        const t = new Date(r.date).getTime();
        if (!Number.isNaN(t) && t >= sevenDaysAgo) last7Days++;
      }
    }
    return {
      total,
      high: byRisk.high,
      medium: byRisk.medium,
      low: byRisk.low,
      avgScore: total > 0 ? scoreSum / total : 0,
      last7Days,
    };
  }, [rows]);

  const openReport = (row: Row) => {
    if (!row.fullAnalysis) return;
    setAnalysis(row.fullAnalysis, row.scenarioId);
    // Pass an explicit hint so the analyse page doesn't reset the store
    // we just populated. Normal navigation to /upload (menu, refresh, …)
    // does NOT carry this flag, so the previous analyse is wiped as the
    // user expects.
    navigate("/upload", { state: { keepResults: true } });
  };

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold text-ccm-ink flex items-center gap-3">
            <HistoryIcon className="h-7 w-7 text-ccm-red" />
            Historique des analyses
          </h1>
          <p className="text-slate-500 mt-1 text-sm">
            Tous les scénarios déjà analysés et sauvegardés dans MongoDB.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            onClick={() => refetch()}
            disabled={isFetching}
          >
            {isFetching ? "Actualisation…" : "Actualiser"}
          </Button>
          <Button onClick={() => navigate("/upload")}>
            <Upload className="h-4 w-4" />
            Nouvelle analyse
          </Button>
        </div>
      </header>

      {/* KPI strip */}
      {data && data.length > 0 && (
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
          <KpiCard
            label="Analyses totales"
            value={kpis.total}
            hint={`${kpis.last7Days} sur 7 jours`}
            icon={FileText}
          />
          <KpiCard
            label="Risque ÉLEVÉ"
            value={kpis.high}
            icon={ShieldAlert}
            tone="red"
          />
          <KpiCard
            label="Risque MOYEN"
            value={kpis.medium}
            icon={AlertTriangle}
            tone="amber"
          />
          <KpiCard
            label="Risque FAIBLE"
            value={kpis.low}
            icon={CheckCircle2}
            tone="emerald"
          />
          <KpiCard
            label="Similarité moyenne"
            value={`${kpis.avgScore.toFixed(2)}%`}
            icon={TrendingUp}
          />
        </div>
      )}

      {/* Filter bar */}
      {data && data.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white p-3 flex flex-wrap gap-3 items-center">
          <div className="relative flex-1 min-w-[220px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
            <Input
              placeholder="Rechercher : nom de fichier, scénario ID, résumé…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="pl-9"
            />
          </div>
          <select
            value={riskFilter}
            onChange={(e) => setRiskFilter(e.target.value as typeof riskFilter)}
            className="h-10 rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-ccm-red"
          >
            <option value="all">Tous risques</option>
            <option value="high">Risque ÉLEVÉ</option>
            <option value="medium">Risque MOYEN</option>
            <option value="low">Risque FAIBLE</option>
          </select>
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as typeof sortBy)}
            className="h-10 rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-ccm-red"
          >
            <option value="date">Trier par date (récent)</option>
            <option value="similarity">Trier par similarité ↓</option>
            <option value="risk">Trier par risque ↓</option>
          </select>
          <p className="text-xs text-slate-500">
            {filtered.length} / {rows.length}
          </p>
        </div>
      )}

      {/* States */}
      {isLoading && (
        <Card>
          <CardContent className="py-10 text-center text-slate-500">
            Chargement de l'historique…
          </CardContent>
        </Card>
      )}
      {error && (
        <Alert variant="error">
          {(error as Error).message ?? "Erreur lors du chargement de l'historique."}
        </Alert>
      )}
      {data && data.length === 0 && (
        <Card>
          <CardContent className="py-12 text-center space-y-3">
            <HistoryIcon className="h-10 w-10 text-slate-300 mx-auto" />
            <p className="text-slate-700 font-medium">
              Aucun scénario analysé pour le moment
            </p>
            <p className="text-sm text-slate-500">
              Lancez votre première analyse pour la voir apparaître ici.
            </p>
            <Button onClick={() => navigate("/upload")}>
              <Upload className="h-4 w-4" />
              Démarrer une analyse
            </Button>
          </CardContent>
        </Card>
      )}
      {data && data.length > 0 && filtered.length === 0 && (
        <Alert variant="info">
          Aucun résultat ne correspond à vos filtres.
        </Alert>
      )}

      {/* List */}
      {filtered.length > 0 && (
        <div className="space-y-2">
          {filtered.map((row, i) => (
            <HistoryRow
              key={`${row.scenarioId}-${i}`}
              row={row}
              expanded={openIndex === i}
              onToggle={() => setOpenIndex(openIndex === i ? null : i)}
              onOpen={() => openReport(row)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
