import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  CalendarClock,
  ChevronRight,
  FileText,
  Gauge,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import { fetchStatistics } from "@/lib/api";
import type { Statistics } from "@/lib/types";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { cn, formatRiskLabel, riskColor } from "@/lib/utils";

// ---------- Helpers ----------

const RISK_COLORS = {
  very_high: "#b91c1c",
  high: "#ef4444",
  medium: "#f59e0b",
  low: "#10b981",
} as const;

function toPct(value: unknown): number {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  const scaled = n <= 1 ? n * 100 : n;
  return Math.max(0, Math.min(100, Math.round(scaled)));
}

// ---------- Dimension row (Doublon / Constantes / Plagiat) ----------

function DimensionRow({
  label,
  active,
  badge,
}: {
  label: string;
  active: boolean;
  badge: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex items-center justify-between text-xs gap-2",
        active ? "text-slate-900" : "text-slate-500"
      )}
    >
      <span className="flex items-center gap-1.5">
        <span
          className={cn(
            "h-1.5 w-1.5 rounded-full",
            active ? "bg-red-500" : "bg-slate-300"
          )}
        />
        {label}
      </span>
      {badge}
    </div>
  );
}

// ---------- KPI card ----------

const TONE = {
  neutral: {
    surface: "from-slate-50 to-white",
    ring: "ring-slate-200/60",
    accent: "bg-slate-400",
    icon: "text-slate-600 bg-slate-100",
    value: "text-slate-900",
  },
  red: {
    surface: "from-red-50 to-white",
    ring: "ring-red-200/60",
    accent: "bg-red-500",
    icon: "text-red-700 bg-red-100",
    value: "text-red-700",
  },
  crimson: {
    surface: "from-red-100 to-white",
    ring: "ring-red-300/70",
    accent: "bg-red-700",
    icon: "text-red-800 bg-red-200/70",
    value: "text-red-800",
  },
  amber: {
    surface: "from-amber-50 to-white",
    ring: "ring-amber-200/60",
    accent: "bg-amber-500",
    icon: "text-amber-700 bg-amber-100",
    value: "text-amber-700",
  },
  emerald: {
    surface: "from-emerald-50 to-white",
    ring: "ring-emerald-200/60",
    accent: "bg-emerald-500",
    icon: "text-emerald-700 bg-emerald-100",
    value: "text-emerald-700",
  },
  blue: {
    surface: "from-blue-50 to-white",
    ring: "ring-blue-200/60",
    accent: "bg-blue-500",
    icon: "text-blue-700 bg-blue-100",
    value: "text-blue-700",
  },
} as const;
type Tone = keyof typeof TONE;

function Kpi({
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
  tone?: Tone;
}) {
  const s = TONE[tone];
  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-xl bg-gradient-to-br ring-1 transition-all duration-200",
        "hover:shadow-md hover:-translate-y-0.5",
        s.surface,
        s.ring
      )}
    >
      <span className={cn("absolute inset-x-0 top-0 h-0.5", s.accent)} />
      <div className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
              {label}
            </p>
            <p
              className={cn(
                "mt-2 text-3xl font-bold tabular-nums leading-none",
                s.value
              )}
            >
              {value}
            </p>
            {hint && (
              <p className="text-[11px] text-slate-500 mt-1.5">{hint}</p>
            )}
          </div>
          <span
            className={cn(
              "inline-flex h-9 w-9 items-center justify-center rounded-lg shrink-0",
              s.icon
            )}
          >
            <Icon className="h-4 w-4" />
          </span>
        </div>
      </div>
    </div>
  );
}

// ---------- Page ----------

export function AnalyticsPage() {
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["statistics"],
    queryFn: fetchStatistics,
  });

  return (
    <div className="space-y-6">
      <header className="relative overflow-hidden rounded-2xl border border-slate-200 bg-gradient-to-br from-white via-slate-50 to-blue-50/40 p-6">
        <div className="absolute -top-12 -right-12 h-40 w-40 rounded-full bg-blue-500/5 blur-3xl" />
        <div className="relative flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold text-ccm-ink flex items-center gap-3">
              <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-blue-500/10 text-blue-600 ring-1 ring-blue-200">
                <BarChart3 className="h-5 w-5" />
              </span>
              Tableau de bord statistiques
            </h1>
            <p className="text-slate-500 mt-2 text-sm max-w-2xl">
              Vue d'ensemble des analyses et indicateurs clés pour piloter le
              suivi des scénarios.
            </p>
          </div>
          <Button
            variant="outline"
            onClick={() => refetch()}
            disabled={isFetching}
          >
            <Sparkles className={cn("h-4 w-4", isFetching && "animate-spin")} />
            {isFetching ? "Actualisation…" : "Actualiser"}
          </Button>
        </div>
      </header>

      {isLoading && (
        <Card>
          <CardContent className="py-10 text-center text-slate-500">
            Chargement des statistiques…
          </CardContent>
        </Card>
      )}
      {error && <Alert variant="error">{(error as Error).message}</Alert>}
      {data && <AnalyticsContent stats={data} />}
    </div>
  );
}

function AnalyticsContent({ stats }: { stats: Statistics }) {
  const navigate = useNavigate();
  const total = Number(stats.total_analyses ?? 0);
  if (!total) {
    return (
      <Card>
        <CardContent className="py-12 text-center space-y-3">
          <BarChart3 className="h-10 w-10 text-slate-300 mx-auto" />
          <p className="text-slate-700 font-medium">
            Aucune analyse disponible pour le moment.
          </p>
          <p className="text-sm text-slate-500">
            Les statistiques apparaîtront dès qu'un premier scénario aura été
            analysé.
          </p>
          <Button onClick={() => navigate("/results")}>
            Lancer une analyse
          </Button>
        </CardContent>
      </Card>
    );
  }

  const veryHigh = stats.risk_counts?.very_high ?? 0;
  const high = stats.risk_counts?.high ?? 0;
  const medium = stats.risk_counts?.medium ?? 0;
  const low = stats.risk_counts?.low ?? 0;
  const flagged = veryHigh + high + medium;
  const flaggedRate = total > 0 ? Math.round((flagged / total) * 100) : 0;
  const avgSimilarity = toPct(stats.average_similarity_score);

  const volume = Array.isArray(stats.analyses_by_date)
    ? stats.analyses_by_date
    : [];
  const last7Days = useMemo(() => {
    const cutoff = Date.now() - 7 * 86400_000;
    return volume.reduce((acc, item) => {
      const t = new Date(item.date).getTime();
      return Number.isNaN(t) || t < cutoff ? acc : acc + (item.count ?? 0);
    }, 0);
  }, [volume]);

  const topSimilar = Array.isArray(stats.top_similar_scenarios)
    ? stats.top_similar_scenarios
    : [];
  // ``risky_analyses`` now carries per-dimension flags (exact duplicate,
  // moroccan constants, plagiarism). Each card surfaces the dimension(s)
  // that triggered it. Old documents without those flags fall back to the
  // similarity-based heuristic so they still appear correctly.
  const risky = Array.isArray(stats.risky_analyses)
    ? stats.risky_analyses
    : [];

  const pieData = [
    { name: "TRÈS ÉLEVÉ", value: veryHigh, color: RISK_COLORS.very_high },
    { name: "ÉLEVÉ", value: high, color: RISK_COLORS.high },
    { name: "MOYEN", value: medium, color: RISK_COLORS.medium },
    { name: "FAIBLE", value: low, color: RISK_COLORS.low },
  ].filter((slice) => slice.value > 0);

  // Histogram of similarity scores across top similar scenarios.
  const histogram = useMemo(() => {
    const buckets = [
      { range: "0-30", min: 0, max: 30, count: 0 },
      { range: "30-55", min: 30, max: 55, count: 0 },
      { range: "55-75", min: 55, max: 75, count: 0 },
      { range: "75-100", min: 75, max: 100, count: 0 },
    ];
    for (const row of topSimilar) {
      const r = row as Record<string, unknown>;
      const score = toPct(r.similarity_score);
      const bucket = buckets.find(
        (b) => score >= b.min && (score < b.max || (score === 100 && b.max === 100))
      );
      if (bucket) bucket.count++;
    }
    return buckets;
  }, [topSimilar]);

  return (
    <>
      {/* ---------- KPI strip ---------- */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <Kpi
          label="Analyses totales"
          value={total}
          hint={`${last7Days} sur 7 jours`}
          icon={FileText}
          tone="blue"
        />
        <Kpi
          label="Plagiats à traiter"
          value={flagged}
          hint={`${flaggedRate}% des analyses`}
          icon={AlertTriangle}
          tone="amber"
        />
        <Kpi
          label="Risque TRÈS ÉLEVÉ"
          value={veryHigh}
          icon={ShieldAlert}
          tone="crimson"
        />
        <Kpi
          label="Risque ÉLEVÉ"
          value={high}
          icon={ShieldAlert}
          tone="red"
        />
        <Kpi
          label="Sans plagiat"
          value={low}
          hint={total > 0 ? `${Math.round((low / total) * 100)}% du total` : ""}
          icon={ShieldCheck}
          tone="emerald"
        />
        <Kpi
          label="Similarité moyenne"
          value={`${avgSimilarity}%`}
          icon={Gauge}
        />
      </div>

      {/* ---------- Charts row ---------- */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Risk donut */}
        <Card className="lg:col-span-1">
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <Activity className="h-4 w-4 text-slate-500" />
              Répartition des risques
            </CardTitle>
          </CardHeader>
          <CardContent>
            {pieData.length === 0 ? (
              <p className="text-sm text-slate-500 py-8 text-center">
                Pas de données suffisantes.
              </p>
            ) : (
              <div className="relative">
                <ResponsiveContainer width="100%" height={220}>
                  <PieChart>
                    <Pie
                      data={pieData}
                      dataKey="value"
                      nameKey="name"
                      innerRadius={55}
                      outerRadius={85}
                      paddingAngle={2}
                      stroke="#fff"
                      strokeWidth={2}
                    >
                      {pieData.map((entry, i) => (
                        <Cell key={i} fill={entry.color} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{
                        background: "white",
                        border: "1px solid #e2e8f0",
                        borderRadius: 8,
                        fontSize: 12,
                      }}
                    />
                  </PieChart>
                </ResponsiveContainer>
                <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                  <p className="text-3xl font-bold text-slate-900 tabular-nums">
                    {total}
                  </p>
                  <p className="text-[11px] uppercase tracking-wider text-slate-500">
                    analyses
                  </p>
                </div>
              </div>
            )}
            <div className="mt-4 space-y-2">
              {pieData.map((slice) => {
                const pct = total > 0 ? Math.round((slice.value / total) * 100) : 0;
                return (
                  <div
                    key={slice.name}
                    className="flex items-center justify-between text-xs"
                  >
                    <span className="inline-flex items-center gap-2 text-slate-700">
                      <span
                        className="h-2.5 w-2.5 rounded-full"
                        style={{ background: slice.color }}
                      />
                      {slice.name}
                    </span>
                    <span className="tabular-nums text-slate-600">
                      <span className="font-semibold text-slate-900">
                        {slice.value}
                      </span>{" "}
                      <span className="text-slate-400">({pct}%)</span>
                    </span>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>

        {/* Volume timeline */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <TrendingUp className="h-4 w-4 text-slate-500" />
              Volume d'analyses dans le temps
            </CardTitle>
          </CardHeader>
          <CardContent>
            {volume.length === 0 ? (
              <p className="text-sm text-slate-500 py-12 text-center">
                Pas encore assez de données pour afficher une évolution
                temporelle.
              </p>
            ) : (
              <ResponsiveContainer width="100%" height={260}>
                <AreaChart
                  data={volume}
                  margin={{ top: 8, right: 12, bottom: 0, left: -20 }}
                >
                  <defs>
                    <linearGradient id="vol" x1="0" y1="0" x2="0" y2="1">
                      <stop
                        offset="0%"
                        stopColor="#3b82f6"
                        stopOpacity={0.35}
                      />
                      <stop
                        offset="100%"
                        stopColor="#3b82f6"
                        stopOpacity={0}
                      />
                    </linearGradient>
                  </defs>
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 11, fill: "#64748b" }}
                    tickLine={false}
                    axisLine={{ stroke: "#e2e8f0" }}
                  />
                  <YAxis
                    allowDecimals={false}
                    tick={{ fontSize: 11, fill: "#64748b" }}
                    tickLine={false}
                    axisLine={false}
                  />
                  <Tooltip
                    contentStyle={{
                      background: "white",
                      border: "1px solid #e2e8f0",
                      borderRadius: 8,
                      fontSize: 12,
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="count"
                    stroke="#3b82f6"
                    strokeWidth={2}
                    fill="url(#vol)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
      </div>

      {/* ---------- Histogram of similarity ---------- */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Gauge className="h-4 w-4 text-slate-500" />
            Distribution des scores de similarité (top 10)
          </CardTitle>
        </CardHeader>
        <CardContent>
          {histogram.every((b) => b.count === 0) ? (
            <p className="text-sm text-slate-500 py-8 text-center">
              Pas encore de scénarios similaires.
            </p>
          ) : (
            <ResponsiveContainer width="100%" height={180}>
              <BarChart
                data={histogram}
                margin={{ top: 8, right: 12, bottom: 0, left: -20 }}
              >
                <XAxis
                  dataKey="range"
                  tick={{ fontSize: 11, fill: "#64748b" }}
                  tickLine={false}
                  axisLine={{ stroke: "#e2e8f0" }}
                  tickFormatter={(v) => `${v}%`}
                />
                <YAxis
                  allowDecimals={false}
                  tick={{ fontSize: 11, fill: "#64748b" }}
                  tickLine={false}
                  axisLine={false}
                />
                <Tooltip
                  contentStyle={{
                    background: "white",
                    border: "1px solid #e2e8f0",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={(v: number) => [v, "scénarios"]}
                  labelFormatter={(v) => `Similarité ${v}%`}
                />
                <Bar dataKey="count" radius={[6, 6, 0, 0]}>
                  {histogram.map((b, i) => {
                    const color =
                      b.min >= 75
                        ? RISK_COLORS.very_high
                        : b.min >= 55
                          ? RISK_COLORS.high
                          : b.min >= 30
                            ? RISK_COLORS.medium
                            : RISK_COLORS.low;
                    return <Cell key={i} fill={color} />;
                  })}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      {/* ---------- Risky analyses (cards) ---------- */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-slate-900 flex items-center gap-2">
            <ShieldAlert className="h-5 w-5 text-red-500" />
            Analyses à surveiller en priorité
          </h2>
          <Badge className="bg-slate-100 text-slate-600">
            {risky.length} dossier{risky.length > 1 ? "s" : ""}
          </Badge>
        </div>
        {risky.length === 0 ? (
          <Card>
            <CardContent className="py-8 text-center text-sm text-slate-500 flex items-center justify-center gap-2">
              <ShieldCheck className="h-5 w-5 text-emerald-500" />
              Aucune analyse à risque moyen ou élevé.
            </CardContent>
          </Card>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {risky.slice(0, 6).map((row, i) => {
              const r = row as Record<string, unknown>;
              const sim = toPct(r.similarity_score);
              const plagiarismRisk = String(
                r.plagiarism_risk ?? ""
              ).toLowerCase();
              const moroccanRisk = String(r.moroccan_risk ?? "").toLowerCase();
              const exactDuplicate = Boolean(r.exact_duplicate);
              const primary = String(r.primary_signal ?? "").toLowerCase();
              const scenarioId = String(r.scenario_id ?? "—");
              const filename = String(r.original_filename ?? "").trim();
              const matched = String(r.matched_filename ?? "").trim();
              const matchedId = String(r.matched_scenario_id ?? "").trim();
              const displayName =
                filename || `Scénario ${scenarioId.slice(0, 8)}…`;
              const displayMatched =
                matched ||
                (matchedId ? `Scénario ${matchedId.slice(0, 8)}…` : "");

              const isHigh = (k: string) => k === "high" || k === "very_high";
              const plagiarismFlagged = isHigh(plagiarismRisk);
              const moroccanFlagged = isHigh(moroccanRisk);

              const accent = exactDuplicate
                ? "bg-red-700"
                : primary === "moroccan_constants"
                  ? "bg-orange-600"
                  : sim >= 75
                    ? "bg-red-600"
                    : sim >= 55
                      ? "bg-red-500"
                      : "bg-amber-500";

              return (
                <Card
                  key={i}
                  className="relative overflow-hidden hover:shadow-md transition-shadow cursor-default"
                >
                  <span
                    className={cn(
                      "absolute left-0 top-0 bottom-0 w-1",
                      accent
                    )}
                    aria-hidden
                  />
                  <CardContent className="p-4 pl-5 space-y-3">
                    {/* Header: filename + similarity score */}
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-start gap-2 min-w-0">
                          <FileText className="h-4 w-4 text-slate-400 shrink-0 mt-0.5" />
                          <p
                            className="text-sm font-medium text-slate-800 truncate"
                            title={displayName}
                          >
                            {displayName}
                          </p>
                        </div>
                        {displayMatched && (
                          <div className="flex items-start gap-2 min-w-0 pl-6 mt-0.5">
                            <span className="text-slate-400 text-xs shrink-0 mt-0.5">
                              ↔
                            </span>
                            <p
                              className="text-xs text-slate-600 truncate"
                              title={displayMatched}
                            >
                              {displayMatched}
                            </p>
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Triple-metric badges */}
                    <div className="space-y-2 pt-1">
                      <DimensionRow
                        label="Doublon exact"
                        active={exactDuplicate}
                        badge={
                          exactDuplicate ? (
                            <Badge className="bg-red-200 text-red-900 border-red-300 uppercase font-semibold text-[10px]">
                              CONFIRMÉ
                            </Badge>
                          ) : (
                            <Badge className="bg-emerald-50 text-emerald-700 border-emerald-100 uppercase font-semibold text-[10px]">
                              NON
                            </Badge>
                          )
                        }
                      />
                      <DimensionRow
                        label="Constantes Maroc"
                        active={moroccanFlagged}
                        badge={
                          moroccanRisk && moroccanRisk !== "unknown" ? (
                            <Badge
                              className={cn(
                                riskColor(moroccanRisk),
                                "uppercase font-semibold text-[10px]"
                              )}
                            >
                              {formatRiskLabel(moroccanRisk)}
                            </Badge>
                          ) : (
                            <span className="text-[11px] text-slate-400">
                              n/a
                            </span>
                          )
                        }
                      />
                      <DimensionRow
                        label="Plagiat global"
                        active={plagiarismFlagged}
                        badge={
                          <div className="flex items-center gap-2">
                            <div className="h-1.5 w-16 bg-slate-100 rounded-full overflow-hidden">
                              <div
                                className={cn(
                                  "h-full",
                                  sim >= 75
                                    ? "bg-red-600"
                                    : sim >= 55
                                      ? "bg-red-500"
                                      : sim >= 30
                                        ? "bg-amber-500"
                                        : "bg-emerald-500"
                                )}
                                style={{ width: `${sim}%` }}
                              />
                            </div>
                            <span className="font-mono text-xs font-semibold tabular-nums text-slate-900 w-9 text-right">
                              {sim}%
                            </span>
                          </div>
                        }
                      />
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </div>

      {/* ---------- Top similar ---------- */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-slate-900 flex items-center gap-2">
            <CalendarClock className="h-5 w-5 text-slate-500" />
            Top 10 des scénarios les plus similaires
          </h2>
        </div>
        {topSimilar.length === 0 ? (
          <Card>
            <CardContent className="py-8 text-center text-sm text-slate-500">
              Aucun scénario similaire à afficher.
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardContent className="p-0 divide-y divide-slate-100">
              {topSimilar.slice(0, 10).map((row, i) => {
                const r = row as Record<string, unknown>;
                const sim = toPct(r.similarity_score);
                // Same rationale as the cards above: align the badge to
                // the similarity bucket actually shown in the row.
                const risk =
                  sim >= 75
                    ? "very_high"
                    : sim >= 55
                      ? "high"
                      : sim >= 30
                        ? "medium"
                        : "low";
                const scenarioId = String(r.scenario_id ?? "—");
                const filename = String(r.original_filename ?? "").trim();
                const matched = String(r.matched_filename ?? "").trim();
                const matchedId = String(r.matched_scenario_id ?? "").trim();
                const displayName =
                  filename || `Scénario ${scenarioId.slice(0, 8)}…`;
                const displayMatched =
                  matched ||
                  (matchedId ? `Scénario ${matchedId.slice(0, 8)}…` : "");
                return (
                  <div
                    key={i}
                    className="flex items-center gap-4 p-3 hover:bg-slate-50/60 transition-colors"
                  >
                    <span className="font-mono text-xs text-slate-400 tabular-nums w-6 text-right">
                      {i + 1}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 min-w-0">
                        <p
                          className="text-sm font-medium text-slate-800 truncate"
                          title={displayName}
                        >
                          {displayName}
                        </p>
                        {displayMatched && (
                          <>
                            <span className="text-slate-300 shrink-0">↔</span>
                            <p
                              className="text-sm text-slate-600 truncate"
                              title={displayMatched}
                            >
                              {displayMatched}
                            </p>
                          </>
                        )}
                      </div>
                      <p className="text-[11px] text-slate-400 mt-0.5">
                        {String(r.analysis_timestamp ?? "—")}
                      </p>
                    </div>
                    <div className="hidden sm:block flex-1 max-w-[180px]">
                      <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
                        <div
                          className={cn(
                            "h-full",
                            sim >= 75
                              ? "bg-red-600"
                              : sim >= 55
                                ? "bg-red-500"
                                : sim >= 30
                                  ? "bg-amber-500"
                                  : "bg-emerald-500"
                          )}
                          style={{ width: `${sim}%` }}
                        />
                      </div>
                    </div>
                    <span className="font-mono text-sm font-semibold tabular-nums text-slate-900 w-12 text-right">
                      {sim}%
                    </span>
                    <Badge
                      className={cn(
                        riskColor(risk),
                        "uppercase text-[10px] font-semibold"
                      )}
                    >
                      {formatRiskLabel(risk)}
                    </Badge>
                    <ChevronRight className="h-4 w-4 text-slate-300" />
                  </div>
                );
              })}
            </CardContent>
          </Card>
        )}
      </div>
    </>
  );
}
