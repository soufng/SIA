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
      ? `${animated.toFixed(2)}%`
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
}: {
  low: number;
  medium: number;
  high: number;
}) {
  const total = low + medium + high;
  const data = [
    { name: "low", value: low, color: "#10b981" },
    { name: "medium", value: medium, color: "#f59e0b" },
    { name: "high", value: high, color: "#ef4444" },
  ];
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
              { label: "Risque élevé",  count: high,   color: "bg-red-500",     pct: total ? (high / total) * 100 : 0 },
              { label: "Risque moyen",  count: medium, color: "bg-amber-500",   pct: total ? (medium / total) * 100 : 0 },
              { label: "Risque faible", count: low,    color: "bg-emerald-500", pct: total ? (low / total) * 100 : 0 },
            ].map((row) => (
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
    text: "Plongements multilingues (e5-base) indexés dans Qdrant, recherche par similarité cosinus.",
  },
  {
    icon: ShieldCheck,
    title: "3. Modération multilingue",
    text: "Vulgarité et contenu adulte détectés en français, arabe et darija, scores normalisés.",
  },
  {
    icon: BarChart3,
    title: "4. Rapport IA",
    text: "Synthèse RAG des passages similaires, recommandations éditoriales et niveau de risque global.",
  },
];

// ---------- Page ----------

export function HomePage() {
  const analysis = useAnalysisStore((s) => s.analysis);
  const navigate = useNavigate();

  const statsQuery = useQuery({
    queryKey: ["home-statistics"],
    queryFn: fetchStatistics,
    refetchOnWindowFocus: false,
    staleTime: 60_000,
  });
  const historyQuery = useQuery({
    queryKey: ["home-history-recent"],
    queryFn: () => fetchHistory(5),
    refetchOnWindowFocus: false,
    staleTime: 60_000,
  });

  const stats = statsQuery.data;
  const backendUp = !!stats && stats.status !== "backend_unreachable";

  const recent = useMemo(
    () => (historyQuery.data ?? []).slice(0, 5),
    [historyQuery.data]
  );

  const totalAnalyses = stats?.total_analyses ?? 0;
  const plagiarismDetected = stats?.plagiarism_detected ?? 0;
  const riskCounts = stats?.risk_counts ?? {};
  const riskHigh = riskCounts.high ?? 0;
  const riskMedium = riskCounts.medium ?? 0;
  const riskLow = riskCounts.low ?? 0;
  const avgSimilarity =
    stats?.average_similarity_score ?? stats?.average_score ?? 0;

  // Sparkline data from analyses_by_date (last 14 days).
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
      <section className="relative overflow-hidden rounded-xl border border-ccm-red/15 bg-white shadow-ccm-soft">
        <div className="ccm-gradient-soft absolute inset-0" />
        <div className="relative grid items-center gap-8 px-6 py-10 md:grid-cols-[1.4fr_1fr] md:px-12 md:py-14">
          <div>
            <p className="inline-flex items-center gap-2 rounded-full bg-ccm-red/10 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-ccm-red">
              <span className="h-1.5 w-1.5 rounded-full bg-ccm-red animate-pulse" />
              Centre Cinematographique Marocain
            </p>
            <h1 className="mt-4 text-3xl font-bold tracking-tight text-ccm-ink md:text-4xl">
              Plateforme intelligente d'analyse de scenarios
            </h1>
            <p className="mt-3 max-w-2xl text-slate-600">
              Détection de plagiat sémantique, moderation multilingue (FR / AR /
              Darija) et synthèse RAG dans une seule interface, alignée sur les
              standards de production du CCM.
            </p>
            <div className="mt-6 flex flex-wrap gap-3">
              <Button
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
                onClick={() => navigate("/analytics")}
              >
                <BarChart3 className="h-4 w-4" />
                Voir les statistiques
              </Button>
              <Button variant="ghost" onClick={() => navigate("/history")}>
                <Clock className="h-4 w-4" />
                Historique
              </Button>
            </div>

            <div className="mt-6 flex flex-wrap items-center gap-2 text-xs text-slate-500">
              <Badge
                className={cn(
                  "gap-1.5",
                  backendUp
                    ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                    : "border-amber-200 bg-amber-50 text-amber-700"
                )}
              >
                <span
                  className={cn(
                    "h-1.5 w-1.5 rounded-full",
                    backendUp
                      ? "bg-emerald-500 animate-pulse"
                      : "bg-amber-500"
                  )}
                />
                {backendUp ? "Backend en ligne" : "Backend injoignable"}
              </Badge>
              <Badge className="gap-1.5 border-slate-200 bg-white text-slate-600">
                <Database className="h-3 w-3" />
                Qdrant 768d / e5-base
              </Badge>
              <Badge className="gap-1.5 border-slate-200 bg-white text-slate-600">
                <Layers className="h-3 w-3" />
                Pipelines : Document - Plagiat - Modération - RAG
              </Badge>
            </div>
          </div>
          <div className="hidden justify-center md:flex">
            <img
              src="/ccm-logo.png"
              alt="Centre Cinematographique Marocain"
              className="max-h-44 w-auto drop-shadow-xl"
            />
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
            hint="Cumul depuis le démarrage"
            tone="info"
            delta={volumeDelta}
            sparkline={volumeSpark}
          />
          <Kpi
            icon={ShieldAlert}
            label="Plagiat détecté"
            value={plagiarismDetected}
            hint="Rapports avec passage similaire"
            tone={plagiarismDetected > 0 ? "danger" : "success"}
          />
          <Kpi
            icon={Gauge}
            label="Similarité moyenne"
            value={
              avgSimilarity > 1 ? avgSimilarity : avgSimilarity * 100
            }
            hint="Score global moyen du corpus"
            tone="info"
            format="percent"
          />
          <Kpi
            icon={AlertTriangle}
            label="Risque élevé"
            value={riskHigh}
            hint={`Medium: ${riskMedium} | Low: ${riskLow}`}
            tone={riskHigh > 0 ? "danger" : "success"}
          />
        </div>

        {/* Donut + quick distribution */}
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[2fr_3fr]">
          <RiskDonut low={riskLow} medium={riskMedium} high={riskHigh} />
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

      {/* ---------- How it works ---------- */}
      <section className="space-y-4">
        <SectionTitle
          icon={Sparkles}
          title="Comment ça marche"
          subtitle="Le pipeline complet, du dépôt du fichier au rapport éditorial."
        />
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
          {PIPELINE_STEPS.map(({ icon: Icon, title, text }) => (
            <Card
              key={title}
              className="group relative overflow-hidden transition-shadow hover:shadow-md"
            >
              <div className="absolute inset-x-0 top-0 h-1 bg-ccm-red transition-all duration-300 group-hover:h-1.5" />
              <CardContent className="pt-6">
                <div className="flex items-center gap-3">
                  <span className="inline-flex h-10 w-10 items-center justify-center rounded-md bg-ccm-red/10 text-ccm-red transition-transform group-hover:scale-105">
                    <Icon className="h-5 w-5" />
                  </span>
                  <h3 className="font-semibold text-ccm-ink">{title}</h3>
                </div>
                <p className="mt-3 text-sm text-slate-600">{text}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      {/* ---------- Footer ---------- */}
      <footer className="border-t border-slate-200 pt-6 text-xs text-slate-500">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span>
            SIA - Plateforme d'analyse de scenarios | Centre Cinematographique
            Marocain
          </span>
          <span className="flex items-center gap-2">
            <span
              className={cn(
                "h-1.5 w-1.5 rounded-full",
                backendUp ? "bg-emerald-500 animate-pulse" : "bg-amber-500"
              )}
            />
            {backendUp ? "Tous les services opérationnels" : "Service dégradé"}
          </span>
        </div>
      </footer>
    </div>
  );
}
