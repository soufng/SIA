import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
} from "recharts";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BarChart3,
  CheckCircle2,
  Clock,
  Database,
  FileText,
  Files,
  Gauge,
  Landmark,
  Layers,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  TrendingDown,
  TrendingUp,
  Upload,
} from "lucide-react";
import { UploadForm } from "@/components/UploadForm";
import { StatusCards } from "@/components/StatusCards";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { fetchHistory, fetchStatistics } from "@/lib/api";
import { cn, formatRiskLabel, formatScore, riskColor } from "@/lib/utils";
import { useAnalysisStore } from "@/store/analysis";

// ---------- Tiny reusable bits ----------

/** Smoothly count from 0 to ``value`` in ~600ms with easeOutCubic. */
function useCountUp(value: number, durationMs = 700): number {
  const [display, setDisplay] = useState(0);
  useEffect(() => {
    if (!Number.isFinite(value)) {
      setDisplay(0);
      return;
    }
    const start = performance.now();
    let frame = 0;
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / durationMs);
      const eased = 1 - Math.pow(1 - t, 3);
      setDisplay(value * eased);
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [value, durationMs]);
  return display;
}

function Sparkline({
  data,
  stroke,
  height = 40,
}: {
  data: Array<{ x: string | number; y: number }>;
  stroke: string;
  height?: number;
}) {
  if (data.length === 0) return <div style={{ height }} />;
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, bottom: 4, left: 0, right: 0 }}>
          <Line
            type="monotone"
            dataKey="y"
            stroke={stroke}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------- KPI tile ----------

type KpiTone = "neutral" | "danger" | "success" | "warning" | "info";

const TONE_STYLES: Record<
  KpiTone,
  {
    iconBg: string;
    accentBar: string;
    deltaUp: string;
    deltaDown: string;
    glow: string;
    spark: string;
  }
> = {
  neutral: {
    iconBg: "bg-slate-100 text-slate-700",
    accentBar: "bg-slate-400",
    deltaUp: "text-slate-600",
    deltaDown: "text-slate-600",
    glow: "from-slate-100/60 to-transparent",
    spark: "#64748b",
  },
  info: {
    iconBg: "bg-sky-100 text-sky-700",
    accentBar: "bg-sky-500",
    deltaUp: "text-sky-700",
    deltaDown: "text-sky-700",
    glow: "from-sky-100/70 to-transparent",
    spark: "#0284c7",
  },
  success: {
    iconBg: "bg-emerald-100 text-emerald-700",
    accentBar: "bg-emerald-500",
    deltaUp: "text-emerald-700",
    deltaDown: "text-rose-600",
    glow: "from-emerald-100/70 to-transparent",
    spark: "#059669",
  },
  warning: {
    iconBg: "bg-amber-100 text-amber-700",
    accentBar: "bg-amber-500",
    deltaUp: "text-amber-700",
    deltaDown: "text-amber-700",
    glow: "from-amber-100/70 to-transparent",
    spark: "#d97706",
  },
  danger: {
    iconBg: "bg-rose-100 text-rose-700",
    accentBar: "bg-rose-500",
    deltaUp: "text-rose-700",
    deltaDown: "text-emerald-600",
    glow: "from-rose-100/70 to-transparent",
    spark: "#e11d48",
  },
};

function Kpi({
  icon: Icon,
  label,
  value,
  hint,
  tone = "neutral",
  delta,
  sparkline,
  format = "integer",
}: {
  icon: typeof FileText;
  label: string;
  value: number;
  hint?: string;
  tone?: KpiTone;
  /** Relative delta vs previous period, e.g. 0.12 → +12 %. */
  delta?: number;
  sparkline?: Array<{ x: string | number; y: number }>;
  format?: "integer" | "percent";
}) {
  const animated = useCountUp(value);
  const styles = TONE_STYLES[tone];

  const display =
    format === "percent"
      ? `${Math.round(animated)}%`
      : Math.round(animated).toLocaleString("fr-FR");

  return (
    <Card className="group relative overflow-hidden transition-shadow hover:shadow-md">
      <div
        className={cn(
          "pointer-events-none absolute inset-x-0 top-0 h-24 bg-gradient-to-b",
          styles.glow
        )}
      />
      <div
        className={cn(
          "absolute inset-x-0 top-0 h-1",
          styles.accentBar,
          "transition-all duration-300 group-hover:h-1.5"
        )}
      />
      <CardContent className="relative pt-5">
        <div className="flex items-center gap-3">
          <span
            className={cn(
              "inline-flex h-10 w-10 items-center justify-center rounded-lg shadow-sm transition-transform group-hover:scale-105",
              styles.iconBg
            )}
          >
            <Icon className="h-5 w-5" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500">
              {label}
            </p>
            <p className="mt-0.5 font-mono text-2xl font-semibold tabular-nums text-ccm-ink">
              {display}
            </p>
          </div>
        </div>

        <div className="mt-3 flex items-end justify-between gap-3">
          <div className="flex flex-col gap-1">
            {typeof delta === "number" && Number.isFinite(delta) && (
              <span
                className={cn(
                  "inline-flex items-center gap-0.5 rounded-full bg-white/80 px-1.5 py-0.5 text-[11px] font-semibold ring-1 ring-inset ring-slate-200",
                  delta >= 0 ? styles.deltaUp : styles.deltaDown
                )}
              >
                {delta >= 0 ? (
                  <TrendingUp className="h-3 w-3" />
                ) : (
                  <TrendingDown className="h-3 w-3" />
                )}
                {(delta * 100).toFixed(1)}%
              </span>
            )}
            {hint && (
              <p className="text-[11px] leading-tight text-slate-500">{hint}</p>
            )}
          </div>
          {sparkline && sparkline.length > 1 && (
            <div className="w-24 shrink-0">
              <Sparkline data={sparkline} stroke={styles.spark} height={36} />
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ---------- Risk donut ----------

function RiskDonut({
  low,
  medium,
  high,
  veryHigh = 0,
  unknown = 0,
}: {
  low: number;
  medium: number;
  high: number;
  veryHigh?: number;
  unknown?: number;
}) {
  const total = low + medium + high + veryHigh + unknown;
  const data = [
    { name: "low", value: low, color: "#10b981" },
    { name: "medium", value: medium, color: "#f59e0b" },
    { name: "high", value: high, color: "#ef4444" },
    { name: "tres_eleve", value: veryHigh, color: "#7f1d1d" },
    { name: "unknown", value: unknown, color: "#94a3b8" },
  ].filter((d) => d.value > 0);
  const animatedTotal = useCountUp(total);

  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-center gap-3">
          <span className="inline-flex h-10 w-10 items-center justify-center rounded-lg bg-ccm-red/10 text-ccm-red">
            <ShieldCheck className="h-5 w-5" />
          </span>
          <div>
            <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500">
              Répartition des risques
            </p>
            <p className="font-mono text-2xl font-semibold tabular-nums text-ccm-ink">
              {Math.round(animatedTotal).toLocaleString("fr-FR")}
            </p>
          </div>
        </div>
        <div className="mt-2 flex items-center gap-4">
          <div className="relative h-32 w-32 shrink-0">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={total > 0 ? data : [{ name: "empty", value: 1 }]}
                  dataKey="value"
                  innerRadius={36}
                  outerRadius={56}
                  stroke="none"
                  isAnimationActive
                >
                  {(total > 0 ? data : [{ name: "empty", value: 1 }]).map(
                    (entry, idx) => (
                      <Cell
                        key={idx}
                        fill={
                          (entry as { color?: string }).color ?? "#e2e8f0"
                        }
                      />
                    )
                  )}
                </Pie>
              </PieChart>
            </ResponsiveContainer>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <span className="text-[10px] uppercase text-slate-400">
                analyses
              </span>
              <span className="font-mono text-lg font-semibold text-ccm-ink">
                {total}
              </span>
            </div>
          </div>
          <div className="flex-1 space-y-1.5 text-xs">
            {[
              { label: "Risque très élevé", count: veryHigh, color: "bg-red-900",    pct: total ? (veryHigh / total) * 100 : 0 },
              { label: "Risque élevé",      count: high,     color: "bg-red-500",    pct: total ? (high / total) * 100 : 0 },
              { label: "Risque moyen",      count: medium,   color: "bg-amber-500",  pct: total ? (medium / total) * 100 : 0 },
              { label: "Risque faible",     count: low,      color: "bg-emerald-500", pct: total ? (low / total) * 100 : 0 },
              { label: "Non classé",        count: unknown,  color: "bg-slate-400",  pct: total ? (unknown / total) * 100 : 0 },
            ].filter((row) => row.count > 0).map((row) => (
              <div key={row.label} className="space-y-0.5">
                <div className="flex items-center justify-between">
                  <span className="flex items-center gap-1.5 text-slate-700">
                    <span className={cn("h-2 w-2 rounded-full", row.color)} />
                    {row.label}
                  </span>
                  <span className="font-mono tabular-nums text-slate-600">
                    {row.count}
                  </span>
                </div>
                <div className="h-1 w-full overflow-hidden rounded-full bg-slate-100">
                  <div
                    className={cn("h-full rounded-full", row.color)}
                    style={{ width: `${row.pct}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------- Section title ----------

function SectionTitle({
  icon: Icon,
  title,
  subtitle,
  actionLabel,
  onAction,
}: {
  icon: typeof FileText;
  title: string;
  subtitle?: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <div className="flex items-end justify-between gap-3">
      <div className="flex items-center gap-3">
        <span className="inline-flex h-9 w-9 items-center justify-center rounded-md bg-ccm-red/10 text-ccm-red">
          <Icon className="h-5 w-5" />
        </span>
        <div>
          <h2 className="text-lg font-semibold text-ccm-ink">{title}</h2>
          {subtitle && (
            <p className="text-xs text-slate-500">{subtitle}</p>
          )}
        </div>
      </div>
      {actionLabel && onAction && (
        <button
          type="button"
          onClick={onAction}
          className="inline-flex items-center gap-1 text-sm font-medium text-ccm-red hover:text-ccm-red-dark"
        >
          {actionLabel}
          <ArrowRight className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}

const PIPELINE_STEPS = [
  {
    icon: FileText,
    title: "1. Extraction",
    text: "Lecture, nettoyage et découpage page par page en segments adaptés au traitement NLP.",
  },
  {
    icon: Sparkles,
    title: "2. Similarité sémantique",
    text: "Plongements multilingues (e5-base) indexés dans Qdrant, recherche par similarité cosinus et détection des doublons exacts.",
  },
  {
    icon: ShieldCheck,
    title: "3. Modération multilingue",
    text: "Vulgarité et contenu adulte détectés en français, arabe et darija, scores normalisés.",
  },
  {
    icon: Landmark,
    title: "4. Constantes marocaines",
    text: "Vérification déterministe des quatre constantes nationales : Islam modéré, Unité nationale, Monarchie constitutionnelle, Choix démocratique.",
  },
  {
    icon: BarChart3,
    title: "5. Rapport IA",
    text: "Synthèse RAG des passages similaires, recommandations éditoriales et niveau de risque global.",
  },
];

// ---------- Page ----------

export function HomePage() {
  const analysis = useAnalysisStore((s) => s.analysis);
  const navigate = useNavigate();

  // Single source of truth: the same /statistics endpoint used by the
  // Analytics page. Keep the two views in sync — derived counters would
  // diverge from the official aggregate and confuse operators.
  const statsQuery = useQuery({
    queryKey: ["home-statistics"],
    queryFn: fetchStatistics,
    refetchOnWindowFocus: false,
    staleTime: 60_000,
  });
  // History feeds the "Analyses récentes" list and the risk-distribution
  // donut. The backend caps the limit at 100, so we ask for the max —
  // beyond that, only the aggregate ``risk_counts`` can describe the
  // full corpus, but it doesn't carry the ``tres_eleve`` bucket either.
  const historyQuery = useQuery({
    queryKey: ["home-history-full"],
    queryFn: () => fetchHistory(100),
    refetchOnWindowFocus: false,
    staleTime: 60_000,
  });

  const stats = statsQuery.data;
  const backendUp = !!stats && stats.status !== "backend_unreachable";

  const allHistory = useMemo(
    () => historyQuery.data ?? [],
    [historyQuery.data]
  );
  const recent = useMemo(() => allHistory.slice(0, 5), [allHistory]);

  // KPIs straight from the backend aggregate (identical to Analytics).
  const totalAnalyses = Number(stats?.total_analyses ?? 0);
  const riskCounts = stats?.risk_counts ?? {};
  const riskHigh = Number(riskCounts.high ?? 0);
  const riskMedium = Number(riskCounts.medium ?? 0);
  const riskLow = Number(riskCounts.low ?? 0);
  const toPct = (raw: unknown): number => {
    const n = Number(raw ?? 0);
    if (!Number.isFinite(n)) return 0;
    return n <= 1 ? n * 100 : n;
  };
  const avgSimilarity = toPct(
    stats?.average_similarity_score ?? stats?.average_score
  );
  const avgProfanity = toPct(stats?.average_profanity_score);
  const avgAdult = toPct(stats?.average_adult_content_score);

  // Risk distribution: derived from history so escalated ``tres_eleve``
  // analyses (and any unclassified row) are actually represented in the
  // donut. The backend aggregate ``risk_counts`` only holds low/medium/
  // high so it under-counts the total when escalation is in play.
  // Falls back to the backend aggregate when history is empty so the
  // donut isn't blank while history loads or errors out.
  const riskDistribution = useMemo(() => {
    const buckets = { tres_eleve: 0, high: 0, medium: 0, low: 0, unknown: 0 };
    for (const item of allHistory) {
      const raw = String(
        item.risk_level ??
          item.rag_report?.risk_level ??
          item.result?.rag_report?.risk_level ??
          ""
      )
        .toLowerCase()
        .trim();
      if (raw === "tres_eleve" || raw === "tres eleve" || raw === "très élevé")
        buckets.tres_eleve += 1;
      else if (raw === "high" || raw === "eleve" || raw === "élevé")
        buckets.high += 1;
      else if (raw === "medium" || raw === "moyen") buckets.medium += 1;
      else if (raw === "low" || raw === "faible") buckets.low += 1;
      else buckets.unknown += 1;
    }
    const sumFromHistory =
      buckets.tres_eleve +
      buckets.high +
      buckets.medium +
      buckets.low +
      buckets.unknown;
    if (sumFromHistory === 0 && totalAnalyses > 0) {
      const unclassified = Math.max(
        0,
        totalAnalyses - riskHigh - riskMedium - riskLow
      );
      return {
        tres_eleve: 0,
        high: riskHigh,
        medium: riskMedium,
        low: riskLow,
        unknown: unclassified,
      };
    }
    return buckets;
  }, [allHistory, totalAnalyses, riskHigh, riskMedium, riskLow]);

  // Sparkline data from analyses_by_date (last 14 days, backend source).
  const sparkBase = (stats?.analyses_by_date ?? []).slice(-14);
  const volumeSpark = sparkBase.map((d) => ({ x: d.date, y: d.count }));

  // Compare last 7 days vs previous 7 days for a delta.
  const last7 = volumeSpark.slice(-7).reduce((sum, p) => sum + p.y, 0);
  const prev7 = volumeSpark.slice(-14, -7).reduce((sum, p) => sum + p.y, 0);
  const volumeDelta =
    prev7 > 0 ? (last7 - prev7) / prev7 : last7 > 0 ? 1 : undefined;

  return (
    <div className="space-y-10">
      {/* ---------- Hero ---------- */}
      <section className="relative overflow-hidden rounded-2xl border border-ccm-red/25 bg-ccm-ink shadow-ccm-soft">
        {/* Layered background: deep CCM gradient + mesh blobs + grid pattern */}
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top_left,_rgba(193,39,45,0.55),_transparent_55%),radial-gradient(ellipse_at_bottom_right,_rgba(212,175,55,0.18),_transparent_50%),linear-gradient(135deg,#1A1A1A_0%,#8E1B22_100%)]" />
        <div
          className="absolute inset-0 opacity-[0.07]"
          style={{
            backgroundImage:
              "linear-gradient(rgba(255,255,255,0.5) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.5) 1px, transparent 1px)",
            backgroundSize: "40px 40px",
            maskImage:
              "radial-gradient(ellipse at center, black 30%, transparent 80%)",
          }}
        />
        <div className="pointer-events-none absolute -left-20 top-10 h-72 w-72 rounded-full bg-ccm-red/40 blur-3xl" />
        <div className="pointer-events-none absolute -right-20 bottom-0 h-80 w-80 rounded-full bg-ccm-gold/15 blur-3xl" />
        <div className="pointer-events-none absolute right-1/3 -top-10 h-40 w-40 rounded-full bg-ccm-red-light/25 blur-2xl" />

        <div className="relative grid items-center gap-10 px-6 py-12 md:grid-cols-[1.3fr_1fr] md:px-14 md:py-16">
          {/* Left column — content */}
          <div className="text-white">
            <p className="inline-flex items-center gap-2 rounded-full border border-ccm-gold/40 bg-white/10 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-white/95 backdrop-blur">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-ccm-gold opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-ccm-gold" />
              </span>
              Centre Cinématographique Marocain
            </p>
            <h1 className="mt-5 text-4xl font-bold leading-[1.1] tracking-tight md:text-5xl">
              <span className="block">Plateforme intelligente</span>
              <span className="mt-1 block bg-gradient-to-r from-white via-ccm-gold to-white bg-clip-text text-transparent">
                d'analyse de scénarios
              </span>
            </h1>
            <p className="mt-4 max-w-2xl text-base leading-relaxed text-slate-300">
              Détection de plagiat sémantique, modération multilingue
              <span className="mx-1 inline-flex items-center gap-1 rounded-md bg-white/10 px-1.5 py-0.5 text-xs font-semibold text-white">
                FR · AR · Darija
              </span>
              et synthèse RAG dans une seule interface, alignée sur les
              standards de production du CCM.
            </p>

            <div className="mt-7 flex flex-wrap gap-3">
              <Button
                className="bg-white text-ccm-red shadow-lg shadow-ccm-red-dark/40 hover:bg-ccm-parchment"
                onClick={() =>
                  document
                    .getElementById("upload-form")
                    ?.scrollIntoView({ behavior: "smooth", block: "start" })
                }
              >
                <Upload className="h-4 w-4" />
                Lancer une analyse
              </Button>
              <Button
                variant="outline"
                className="border-white/25 bg-white/5 text-white backdrop-blur hover:bg-white/15"
                onClick={() => navigate("/analytics")}
              >
                <BarChart3 className="h-4 w-4" />
                Voir les statistiques
              </Button>
              <Button
                variant="ghost"
                className="text-white/85 hover:bg-white/10 hover:text-white"
                onClick={() => navigate("/history")}
              >
                <Clock className="h-4 w-4" />
                Historique
              </Button>
            </div>

            <div className="mt-7 flex flex-wrap items-center gap-2 text-xs">
              <Badge
                className={cn(
                  "gap-1.5 border bg-white/10 backdrop-blur",
                  backendUp
                    ? "border-emerald-300/40 text-emerald-100"
                    : "border-amber-300/40 text-amber-100",
                )}
              >
                <span
                  className={cn(
                    "h-1.5 w-1.5 rounded-full",
                    backendUp
                      ? "bg-emerald-400 animate-pulse"
                      : "bg-amber-400",
                  )}
                />
                {backendUp ? "Backend en ligne" : "Backend injoignable"}
              </Badge>
              <Badge className="gap-1.5 border-white/15 bg-white/10 text-white/85 backdrop-blur">
                <Database className="h-3 w-3" />
                Qdrant 768d · e5-base
              </Badge>
              <Badge className="gap-1.5 border-white/15 bg-white/10 text-white/85 backdrop-blur">
                <Layers className="h-3 w-3" />
                Document → Plagiat → Modération → RAG
              </Badge>
            </div>
          </div>

          {/* Right column — floating brand card */}
          <div className="relative hidden items-center justify-center md:flex">
            {/* Decorative rings */}
            <div className="pointer-events-none absolute h-80 w-80 rounded-full border border-white/10" />
            <div className="pointer-events-none absolute h-64 w-64 rounded-full border border-white/10" />
            <div className="pointer-events-none absolute h-48 w-48 rounded-full border border-white/10" />

            <div className="relative flex flex-col items-center gap-4 rounded-2xl border border-white/15 bg-white/10 p-6 backdrop-blur-md shadow-2xl shadow-ccm-red-dark/40 transition-transform hover:-translate-y-1">
              <img
                src="/ccm-logo.png"
                alt="Centre Cinématographique Marocain"
                className="h-32 w-auto drop-shadow-[0_8px_24px_rgba(255,255,255,0.25)]"
              />
              <div className="text-center">
                <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-white/60">
                  Système d'Analyse
                </p>
                <p className="mt-1 bg-gradient-to-r from-white to-ccm-gold bg-clip-text font-mono text-2xl font-bold text-transparent">
                  SIA
                </p>
              </div>
              {/* Mini stat strip */}
              <div className="grid w-full grid-cols-3 gap-2 border-t border-white/10 pt-3 text-center">
                <div>
                  <p className="font-mono text-sm font-bold text-white">
                    {totalAnalyses}
                  </p>
                  <p className="text-[9px] uppercase tracking-wider text-white/60">
                    Analyses
                  </p>
                </div>
                <div className="border-x border-white/10">
                  <p className="font-mono text-sm font-bold text-white">3</p>
                  <p className="text-[9px] uppercase tracking-wider text-white/60">
                    Langues
                  </p>
                </div>
                <div>
                  <p className="font-mono text-sm font-bold text-white">4</p>
                  <p className="text-[9px] uppercase tracking-wider text-white/60">
                    Pipelines
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Bottom accent line — Moroccan flag-inspired gradient */}
        <div className="absolute inset-x-0 bottom-0 h-1 bg-gradient-to-r from-transparent via-ccm-red to-transparent" />
      </section>

      {/* ---------- Pipeline (How it works) ---------- */}
      <section className="space-y-5">
        <SectionTitle
          icon={Sparkles}
          title="Comment ça marche"
          subtitle="Le pipeline complet, du dépôt du fichier au rapport éditorial."
        />

        <div className="relative">
          {/* Connecting timeline (desktop only) — a soft dashed line
              behind the 4 cards that suggests the flow. */}
          <div
            className="pointer-events-none absolute left-[6%] right-[6%] top-12 hidden h-px lg:block"
            style={{
              backgroundImage:
                "repeating-linear-gradient(to right, rgba(193,39,45,0.4) 0 8px, transparent 8px 16px)",
            }}
          />

          <div className="relative grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-5">
            {PIPELINE_STEPS.map(({ icon: Icon, title, text }, idx) => {
              const stepNum = idx + 1;
              const isLast = idx === PIPELINE_STEPS.length - 1;
              return (
                <div key={title} className="group relative">
                  {/* Animated arrow connector to next step (desktop) */}
                  {!isLast && (
                    <span
                      className="pointer-events-none absolute -right-3 top-10 z-10 hidden h-5 w-5 items-center justify-center rounded-full bg-white text-ccm-red shadow-sm ring-1 ring-ccm-red/20 transition-transform group-hover:translate-x-0.5 lg:flex"
                      aria-hidden
                    >
                      <ArrowRight className="h-3 w-3" />
                    </span>
                  )}

                  <Card className="relative h-full overflow-hidden border-slate-200 bg-white transition-all duration-300 hover:-translate-y-1 hover:border-ccm-red/30 hover:shadow-[0_18px_40px_-20px_rgba(193,39,45,0.45)]">
                    {/* Soft red glow that intensifies on hover */}
                    <div className="pointer-events-none absolute -right-12 -top-12 h-32 w-32 rounded-full bg-ccm-red/0 blur-2xl transition-colors duration-300 group-hover:bg-ccm-red/15" />
                    {/* Watermark step number in the corner */}
                    <span className="pointer-events-none absolute -bottom-4 -right-2 select-none font-mono text-7xl font-black text-ccm-red/[0.05] transition-colors duration-300 group-hover:text-ccm-red/[0.1]">
                      0{stepNum}
                    </span>

                    <CardContent className="relative pt-6">
                      <div className="flex items-center gap-3">
                        {/* Numbered gradient badge */}
                        <span className="relative inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-ccm-red-light via-ccm-red to-ccm-red-dark text-white shadow-lg shadow-ccm-red/30 ring-1 ring-ccm-gold/20 transition-transform group-hover:scale-105">
                          <Icon className="h-5 w-5" />
                          {/* Step number chip */}
                          <span className="absolute -right-1.5 -top-1.5 inline-flex h-5 w-5 items-center justify-center rounded-full bg-ccm-ink text-[10px] font-bold text-ccm-gold ring-2 ring-white">
                            {stepNum}
                          </span>
                        </span>
                        <div className="min-w-0">
                          <p className="text-[10px] font-semibold uppercase tracking-[0.15em] text-ccm-red">
                            Étape {stepNum}
                          </p>
                          <h3 className="truncate text-base font-semibold text-ccm-ink">
                            {title.replace(/^\d+\.\s*/, "")}
                          </h3>
                        </div>
                      </div>

                      <p className="mt-4 text-sm leading-relaxed text-slate-600">
                        {text}
                      </p>
                    </CardContent>

                    {/* Bottom accent — fills from left on hover */}
                    <div className="absolute inset-x-0 bottom-0 h-1 bg-gradient-to-r from-ccm-red via-ccm-red-light to-ccm-gold opacity-60 transition-opacity duration-300 group-hover:opacity-100" />
                  </Card>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* ---------- KPI cards (modern) ---------- */}
      <section className="space-y-4">
        <SectionTitle
          icon={Activity}
          title="Vue d'ensemble"
          subtitle="Indicateurs clés du système et tendances sur 14 jours."
          actionLabel="Statistiques complètes"
          onAction={() => navigate("/analytics")}
        />
        {!backendUp && (
          <Alert variant="warning">
            Impossible de joindre le backend pour le moment. Les indicateurs
            ci-dessous restent à zéro jusqu'au retour des services.
          </Alert>
        )}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Kpi
            icon={Files}
            label="Analyses totales"
            value={totalAnalyses}
            hint={
              totalAnalyses > 0
                ? `${last7} sur les 7 derniers jours`
                : "Aucune analyse enregistrée"
            }
            tone="info"
            delta={volumeDelta}
            sparkline={volumeSpark}
          />
          <Kpi
            icon={Gauge}
            label="Similarité moyenne"
            value={avgSimilarity}
            hint={`Sur ${totalAnalyses} analyse${totalAnalyses > 1 ? "s" : ""}`}
            tone={avgSimilarity >= 75 ? "danger" : avgSimilarity >= 40 ? "warning" : "info"}
            format="percent"
          />
          <Kpi
            icon={ShieldAlert}
            label="Vulgarité moyenne"
            value={avgProfanity}
            hint={`Sur ${totalAnalyses} analyse${totalAnalyses > 1 ? "s" : ""}`}
            tone={avgProfanity >= 60 ? "danger" : avgProfanity >= 20 ? "warning" : "info"}
            format="percent"
          />
          <Kpi
            icon={AlertTriangle}
            label="Contenu adulte moyen"
            value={avgAdult}
            hint={`Sur ${totalAnalyses} analyse${totalAnalyses > 1 ? "s" : ""}`}
            tone={avgAdult >= 60 ? "danger" : avgAdult >= 20 ? "warning" : "info"}
            format="percent"
          />
        </div>

        {/* Donut + quick distribution */}
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[2fr_3fr]">
          <RiskDonut
            low={riskDistribution.low}
            medium={riskDistribution.medium}
            high={riskDistribution.high}
            veryHigh={riskDistribution.tres_eleve}
            unknown={riskDistribution.unknown}
          />
          <Card>
            <CardContent className="pt-6">
              <div className="flex items-center gap-3">
                <span className="inline-flex h-10 w-10 items-center justify-center rounded-lg bg-sky-100 text-sky-700">
                  <Activity className="h-5 w-5" />
                </span>
                <div>
                  <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500">
                    Volume d'analyses (14 derniers jours)
                  </p>
                  <p className="font-mono text-2xl font-semibold tabular-nums text-ccm-ink">
                    {last7.toLocaleString("fr-FR")}
                    <span className="ml-2 text-xs font-normal text-slate-500">
                      cette semaine
                    </span>
                  </p>
                </div>
              </div>
              <div className="mt-3 h-28">
                {volumeSpark.length > 0 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart
                      data={volumeSpark}
                      margin={{ top: 8, bottom: 0, left: 0, right: 0 }}
                    >
                      <Line
                        type="monotone"
                        dataKey="y"
                        stroke="#dc2626"
                        strokeWidth={2.5}
                        dot={{ r: 2, fill: "#dc2626" }}
                        isAnimationActive
                      />
                    </LineChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="flex h-full items-center justify-center rounded-md border border-dashed border-slate-200 text-xs text-slate-400">
                    Pas encore de données d'activité
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </div>
      </section>

      {/* ---------- Upload + last analysis ---------- */}
      <section
        id="upload-form"
        className="grid grid-cols-1 gap-6 lg:grid-cols-2 scroll-mt-24"
      >
        <UploadForm />
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <span className="inline-flex h-9 w-9 items-center justify-center rounded-md bg-ccm-red/10 text-ccm-red">
                  <CheckCircle2 className="h-5 w-5" />
                </span>
                <div>
                  <h2 className="text-base font-semibold text-ccm-ink">
                    Dernier résultat
                  </h2>
                  <p className="text-xs text-slate-500">
                    {analysis
                      ? "Aperçu rapide de la dernière analyse de cette session."
                      : "Aucune analyse pour l'instant."}
                  </p>
                </div>
              </div>
              {analysis && (
                <Button
                  variant="outline"
                  onClick={() => navigate("/results")}
                >
                  Rapport complet
                  <ArrowRight className="h-3.5 w-3.5" />
                </Button>
              )}
            </div>
            <div className="mt-4">
              {analysis ? (
                <StatusCards analysis={analysis} />
              ) : (
                <Alert variant="info">
                  Chargez un fichier PDF a gauche pour lancer une analyse
                  complete : extraction, embeddings, plagiat, modération et
                  rapport RAG.
                </Alert>
              )}
            </div>
          </CardContent>
        </Card>
      </section>

      {/* ---------- Recent analyses ---------- */}
      <section className="space-y-4">
        <SectionTitle
          icon={Clock}
          title="Analyses récentes"
          subtitle="Les 5 derniers scénarios analysés."
          actionLabel="Tout l'historique"
          onAction={() => navigate("/history")}
        />
        <Card>
          <CardContent className="p-0">
            {historyQuery.isLoading ? (
              <p className="px-4 py-6 text-sm text-slate-500">Chargement...</p>
            ) : recent.length === 0 ? (
              <p className="px-4 py-6 text-sm text-slate-500">
                Aucune analyse encore enregistrée.
              </p>
            ) : (
              <ul className="divide-y divide-slate-100">
                {recent.map((item, idx) => {
                  const score =
                    item.similarity_score ??
                    item.plagiarism?.global_similarity_score ??
                    0;
                  const risk = String(
                    item.risk_level ??
                      item.rag_report?.risk_level ??
                      "unknown"
                  );
                  const when =
                    item.analysis_timestamp ?? item.created_at ?? "";
                  return (
                    <li
                      key={item.scenario_id ?? idx}
                      className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-slate-50"
                    >
                      <span className="inline-flex h-9 w-9 items-center justify-center rounded-md bg-slate-100 text-slate-600">
                        <FileText className="h-4 w-4" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-ccm-ink">
                          {item.filename ??
                            item.document_stats?.original_filename ??
                            "(sans nom)"}
                        </p>
                        <p className="truncate text-xs text-slate-500">
                          {when
                            ? new Date(when).toLocaleString("fr-FR")
                            : "Date inconnue"}
                        </p>
                      </div>
                      <Badge className="bg-slate-100 text-slate-700">
                        {formatScore(score, "%")}
                      </Badge>
                      <Badge className={riskColor(risk)}>
                        {formatRiskLabel(risk)}
                      </Badge>
                    </li>
                  );
                })}
              </ul>
            )}
          </CardContent>
        </Card>
      </section>

    </div>
  );
}
