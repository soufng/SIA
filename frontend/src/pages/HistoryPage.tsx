import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  ChevronDown,
  Clock,
  FileText,
  History as HistoryIcon,
  Search,
  Sparkles,
  Upload,
} from "lucide-react";
import { fetchHistory } from "@/lib/api";
import type { Analysis, HistoryItem } from "@/lib/types";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { cn, formatRiskLabel, riskColor } from "@/lib/utils";
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


// ---------- Inline risk readout (for the dashboard strip) ----------

function RiskInline({
  dot,
  label,
  value,
}: {
  dot: string;
  label: string;
  value: number;
}) {
  return (
    <span className="inline-flex items-baseline gap-2 text-sm">
      <span className={cn("h-2 w-2 rounded-full self-center", dot)} />
      <span className="text-slate-600">{label}</span>
      <span className="font-bold tabular-nums text-slate-900">{value}</span>
    </span>
  );
}

// ---------- Similarity gauge (semi-circle SVG, for the dashboard strip) ----------

function SimilarityGauge({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, value <= 1 ? value * 100 : value));
  const color =
    pct >= 75
      ? "#b91c1c"
      : pct >= 55
        ? "#ef4444"
        : pct >= 30
          ? "#f59e0b"
          : "#10b981";
  const radius = 22;
  const circumference = Math.PI * radius;
  const dash = (pct / 100) * circumference;
  return (
    <div className="relative h-12 w-20 shrink-0">
      <svg className="h-12 w-20" viewBox="0 0 56 32">
        <path
          d="M 6 28 A 22 22 0 0 1 50 28"
          fill="none"
          stroke="#e2e8f0"
          strokeWidth="5"
          strokeLinecap="round"
        />
        <path
          d="M 6 28 A 22 22 0 0 1 50 28"
          fill="none"
          stroke={color}
          strokeWidth="5"
          strokeLinecap="round"
          strokeDasharray={`${dash} ${circumference}`}
          className="transition-[stroke-dasharray] duration-500"
        />
      </svg>
    </div>
  );
}

// ---------- Score ring (mini gauge) ----------

function ScoreRing({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, value <= 1 ? value * 100 : value));
  const rounded = Math.round(pct);
  const radius = 18;
  const circumference = 2 * Math.PI * radius;
  const dash = (pct / 100) * circumference;
  const color =
    pct >= 75
      ? "#ef4444"
      : pct >= 55
        ? "#f59e0b"
        : pct >= 30
          ? "#eab308"
          : "#10b981";
  return (
    <div className="relative h-12 w-12 shrink-0">
      <svg className="h-12 w-12 -rotate-90" viewBox="0 0 44 44">
        <circle
          cx="22"
          cy="22"
          r={radius}
          fill="none"
          stroke="#e2e8f0"
          strokeWidth="3.5"
        />
        <circle
          cx="22"
          cy="22"
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth="3.5"
          strokeLinecap="round"
          strokeDasharray={`${dash} ${circumference}`}
          className="transition-[stroke-dasharray] duration-500"
        />
      </svg>
      <span
        className="absolute inset-0 grid place-items-center text-[10px] font-bold tabular-nums"
        style={{ color }}
      >
        {rounded}
      </span>
    </div>
  );
}

// ---------- Score mini-bar ----------

function MiniBar({
  value,
}: {
  value: number;
  unit?: "%" | "/100";
}) {
  // Les scores backend peuvent arriver en 0..1 (fraction) ou 0..100 (%).
  // On normalise systématiquement en pourcentage entier.
  const pct = Math.max(0, Math.min(100, value <= 1 ? value * 100 : value));
  const color =
    pct >= 75
      ? "bg-red-500"
      : pct >= 40
        ? "bg-amber-500"
        : pct > 0
          ? "bg-emerald-500"
          : "bg-slate-200";
  const display = `${Math.round(pct)}%`;
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
  const accentClass =
    riskKey === "high" || riskKey === "tres_eleve"
      ? "bg-red-500"
      : riskKey === "medium"
        ? "bg-amber-500"
        : riskKey === "low"
          ? "bg-emerald-500"
          : "bg-slate-300";
  return (
    <Card
      className={cn(
        "overflow-hidden relative transition-all duration-200",
        "hover:shadow-md hover:-translate-y-px",
        expanded && "shadow-md ring-1 ring-slate-200"
      )}
    >
      {/* Coloured accent strip on the left */}
      <span
        className={cn(
          "absolute left-0 top-0 bottom-0 w-1 transition-colors",
          accentClass
        )}
        aria-hidden
      />
      <button
        type="button"
        onClick={onToggle}
        className="w-full p-4 pl-5 text-left flex flex-wrap gap-3 items-center hover:bg-slate-50/60 transition-colors"
      >
        {/* Left: icon + name + meta */}
        <div className="flex items-start gap-3 min-w-0 flex-1">
          <span
            className={cn(
              "inline-flex h-10 w-10 items-center justify-center rounded-lg shrink-0 ring-1",
              (riskKey === "high" || riskKey === "tres_eleve") &&
                "bg-red-50 text-red-600 ring-red-100",
              riskKey === "medium" && "bg-amber-50 text-amber-600 ring-amber-100",
              riskKey === "low" && "bg-emerald-50 text-emerald-600 ring-emerald-100",
              !["high", "tres_eleve", "medium", "low"].includes(riskKey) &&
                "bg-slate-50 text-slate-500 ring-slate-100"
            )}
          >
            <FileText className="h-5 w-5" />
          </span>
          <div className="min-w-0">
            <p
              className="font-semibold text-slate-900 truncate"
              title={row.filename}
            >
              {row.filename}
            </p>
            <p className="text-xs text-slate-500 flex items-center gap-2 mt-1 flex-wrap">
              <span
                className="inline-flex items-center gap-1"
                title={formatExactDate(row.date) ?? undefined}
              >
                <Clock className="h-3 w-3" />
                {formatRelative(row.date)}
              </span>
              <span className="text-slate-300">•</span>
              <span
                className="font-mono truncate text-slate-400"
                title={row.scenarioId}
              >
                {row.scenarioId.slice(0, 8)}…
              </span>
              {row.wordCount > 0 && (
                <>
                  <span className="text-slate-300">•</span>
                  <span>{row.wordCount.toLocaleString("fr-FR")} mots</span>
                </>
              )}
              {row.totalMatches > 0 && (
                <>
                  <span className="text-slate-300">•</span>
                  <span className="inline-flex items-center gap-1 text-slate-600">
                    <Search className="h-3 w-3" />
                    {row.totalMatches} passage
                    {row.totalMatches > 1 ? "s" : ""}
                  </span>
                </>
              )}
            </p>
          </div>
        </div>

        {/* Right: gauge + badge + chevron */}
        <div className="flex items-center gap-3">
          <ScoreRing value={row.similarity} />
          <Badge
            className={cn(
              riskColor(row.risk),
              "uppercase font-semibold tracking-wide text-[10px] px-2"
            )}
          >
            {riskUpper}
          </Badge>
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
    "all" | "very_high" | "high" | "medium" | "low"
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
      list = list.filter((r) => {
        const k = r.risk.toLowerCase().trim();
        if (riskFilter === "very_high") {
          // Accept both the backend slug and the French variants.
          return k === "very_high" || k === "tres_eleve" || k === "tres eleve";
        }
        return k === riskFilter;
      });
    }
    list = [...list].sort((a, b) => {
      if (sortBy === "similarity") return b.similarity - a.similarity;
      if (sortBy === "risk") {
        const order: Record<string, number> = {
          very_high: 0,
          tres_eleve: 0,
          "tres eleve": 0,
          high: 1,
          medium: 2,
          low: 3,
          unknown: 4,
        };
        return (
          (order[a.risk.toLowerCase().trim()] ?? 5) -
          (order[b.risk.toLowerCase().trim()] ?? 5)
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
    const byRisk = { very_high: 0, high: 0, medium: 0, low: 0 };
    let scoreSum = 0;
    let last7Days = 0;
    const sevenDaysAgo = Date.now() - 7 * 86400_000;
    for (const r of rows) {
      const k = r.risk.toLowerCase().trim();
      if (k === "very_high" || k === "tres_eleve" || k === "tres eleve") {
        byRisk.very_high++;
      } else if (k === "high" || k === "medium" || k === "low") {
        byRisk[k]++;
      }
      scoreSum +=
        r.similarity <= 1 ? r.similarity * 100 : r.similarity;
      if (r.date) {
        const t = new Date(r.date).getTime();
        if (!Number.isNaN(t) && t >= sevenDaysAgo) last7Days++;
      }
    }
    return {
      total,
      veryHigh: byRisk.very_high,
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
    navigate("/results", { state: { keepResults: true } });
  };

  return (
    <div className="space-y-6">
      <header className="relative overflow-hidden rounded-2xl border border-slate-200 bg-gradient-to-br from-white via-slate-50 to-red-50/30 p-6">
        <div className="absolute -top-12 -right-12 h-40 w-40 rounded-full bg-ccm-red/5 blur-3xl" />
        <div className="relative flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold text-ccm-ink flex items-center gap-3">
              <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-ccm-red/10 text-ccm-red ring-1 ring-ccm-red/20">
                <HistoryIcon className="h-5 w-5" />
              </span>
              Historique des analyses
            </h1>
            <p className="text-slate-500 mt-2 text-sm max-w-xl">
              Tous les scénarios déjà analysés et sauvegardés dans MongoDB.
              Cliquez sur une ligne pour explorer le détail, ou ouvrez le
              rapport complet.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              onClick={() => refetch()}
              disabled={isFetching}
            >
              <Sparkles className={cn("h-4 w-4", isFetching && "animate-spin")} />
              {isFetching ? "Actualisation…" : "Actualiser"}
            </Button>
            <Button onClick={() => navigate("/results")}>
              <Upload className="h-4 w-4" />
              Nouvelle analyse
            </Button>
          </div>
        </div>
      </header>

      {/* Dashboard strip — single panel with sectioned content */}
      {data && data.length > 0 && (
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
          <div className="flex flex-wrap items-stretch divide-x divide-slate-100">
            {/* Section 1: total + delta */}
            <div className="flex items-center gap-3 px-5 py-4 flex-1 min-w-[180px]">
              <span className="inline-flex h-11 w-11 items-center justify-center rounded-lg bg-slate-900 text-white shrink-0">
                <FileText className="h-5 w-5" />
              </span>
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                  Total analyses
                </p>
                <div className="flex items-baseline gap-2 mt-0.5">
                  <span className="text-3xl font-bold text-slate-900 tabular-nums leading-none">
                    {kpis.total}
                  </span>
                  <span className="text-[11px] text-slate-500">
                    +{kpis.last7Days} en 7j
                  </span>
                </div>
              </div>
            </div>

            {/* Section 2: risk breakdown inline */}
            <div className="px-5 py-4 flex-[2] min-w-[320px]">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500 mb-2">
                Répartition par niveau de risque
              </p>
              <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5">
                <RiskInline
                  dot="bg-red-700"
                  label="Très élevé"
                  value={kpis.veryHigh}
                />
                <RiskInline
                  dot="bg-red-500"
                  label="Élevé"
                  value={kpis.high}
                />
                <RiskInline
                  dot="bg-amber-500"
                  label="Moyen"
                  value={kpis.medium}
                />
                <RiskInline
                  dot="bg-emerald-500"
                  label="Faible"
                  value={kpis.low}
                />
              </div>
            </div>

            {/* Section 3: average similarity gauge */}
            <div className="flex items-center gap-3 px-5 py-4 flex-1 min-w-[180px]">
              <SimilarityGauge value={kpis.avgScore} />
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                  Similarité moyenne
                </p>
                <div className="flex items-baseline gap-2 mt-0.5">
                  <span className="text-3xl font-bold tabular-nums leading-none text-slate-900">
                    {Math.round(kpis.avgScore)}
                  </span>
                  <span className="text-sm text-slate-500">%</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Filter bar */}
      {data && data.length > 0 && (
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-3 flex flex-wrap gap-2 items-center">
          <div className="relative flex-1 min-w-[220px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
            <Input
              placeholder="Rechercher : nom de fichier, scénario ID, résumé…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="pl-9"
            />
          </div>
          {/* Risk filter chips */}
          <div className="flex items-center gap-1 rounded-lg bg-slate-100 p-1">
            {(
              [
                { key: "all", label: "Tous", color: "bg-slate-700" },
                { key: "very_high", label: "Très élevé", color: "bg-red-700" },
                { key: "high", label: "Élevé", color: "bg-red-500" },
                { key: "medium", label: "Moyen", color: "bg-amber-500" },
                { key: "low", label: "Faible", color: "bg-emerald-500" },
              ] as const
            ).map((chip) => {
              const active = riskFilter === chip.key;
              return (
                <button
                  key={chip.key}
                  type="button"
                  onClick={() => setRiskFilter(chip.key)}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                    active
                      ? "bg-white text-slate-900 shadow-sm"
                      : "text-slate-600 hover:text-slate-900"
                  )}
                >
                  {chip.key !== "all" && (
                    <span className={cn("h-1.5 w-1.5 rounded-full", chip.color)} />
                  )}
                  {chip.label}
                </button>
              );
            })}
          </div>
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as typeof sortBy)}
            className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-xs text-slate-700 focus:outline-none focus:ring-2 focus:ring-ccm-red"
          >
            <option value="date">Trier par date (récent)</option>
            <option value="similarity">Trier par similarité ↓</option>
            <option value="risk">Trier par risque ↓</option>
          </select>
          <span className="text-xs text-slate-500 px-2 tabular-nums">
            {filtered.length}
            <span className="text-slate-300"> / </span>
            {rows.length}
          </span>
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
            <Button onClick={() => navigate("/results")}>
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
