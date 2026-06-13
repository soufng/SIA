import { useEffect, useState } from "react";
import {
  AlertTriangle,
  FileText,
  Layers,
  Search,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import type { Analysis } from "@/lib/types";
import { cn, formatRiskLabel } from "@/lib/utils";

interface Props {
  analysis: Analysis;
}

type RiskKey =
  | "tres_eleve"
  | "high"
  | "medium"
  | "low"
  | "unknown";

function normalizeRiskKey(level: string): RiskKey {
  const k = level.toLowerCase().trim();
  if (k === "tres_eleve" || k === "tres eleve" || k === "très élevé")
    return "tres_eleve";
  if (k === "high" || k === "eleve" || k === "élevé") return "high";
  if (k === "medium" || k === "moyen") return "medium";
  if (k === "low" || k === "faible") return "low";
  return "unknown";
}

function riskTheme(level: RiskKey) {
  switch (level) {
    case "tres_eleve":
      return {
        gradient: "from-red-600 via-rose-600 to-orange-500",
        glow: "shadow-[0_10px_40px_-12px_rgba(220,38,38,0.55)]",
        ring: "#b91c1c",
        accent: "text-red-50",
        pulse: "bg-red-300",
      };
    case "high":
      return {
        gradient: "from-rose-500 via-red-500 to-amber-500",
        glow: "shadow-[0_10px_40px_-12px_rgba(244,63,94,0.5)]",
        ring: "#e11d48",
        accent: "text-rose-50",
        pulse: "bg-rose-300",
      };
    case "medium":
      return {
        gradient: "from-amber-400 via-amber-500 to-orange-500",
        glow: "shadow-[0_10px_40px_-12px_rgba(245,158,11,0.5)]",
        ring: "#d97706",
        accent: "text-amber-50",
        pulse: "bg-amber-200",
      };
    case "low":
      return {
        gradient: "from-emerald-400 via-emerald-500 to-teal-500",
        glow: "shadow-[0_10px_40px_-12px_rgba(16,185,129,0.5)]",
        ring: "#059669",
        accent: "text-emerald-50",
        pulse: "bg-emerald-200",
      };
    default:
      return {
        gradient: "from-slate-400 via-slate-500 to-slate-600",
        glow: "shadow-[0_10px_40px_-12px_rgba(100,116,139,0.5)]",
        ring: "#475569",
        accent: "text-slate-50",
        pulse: "bg-slate-300",
      };
  }
}

function useCountUp(target: number, durationMs = 900) {
  const [value, setValue] = useState(0);
  useEffect(() => {
    let raf = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / durationMs);
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(target * eased);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, durationMs]);
  return value;
}

function toPercent(raw: unknown): number {
  const n = Number(raw ?? 0);
  if (!Number.isFinite(n)) return 0;
  return n <= 1 ? n * 100 : n;
}

function formatInt(n: number): string {
  return Math.round(n).toLocaleString("fr-FR");
}

/** Score → flag (low | medium | high) for the mini bars. */
function scoreFlag(pct: number, thresholds: { medium: number; high: number }) {
  if (pct >= thresholds.high) return "high";
  if (pct >= thresholds.medium) return "medium";
  return "low";
}

function flagColors(flag: "low" | "medium" | "high") {
  return flag === "high"
    ? { bar: "bg-rose-500", text: "text-rose-600", chip: "bg-rose-50 text-rose-700 ring-rose-200" }
    : flag === "medium"
      ? { bar: "bg-amber-500", text: "text-amber-600", chip: "bg-amber-50 text-amber-700 ring-amber-200" }
      : { bar: "bg-emerald-500", text: "text-emerald-600", chip: "bg-emerald-50 text-emerald-700 ring-emerald-200" };
}

interface CountTileProps {
  label: string;
  value: number;
  icon: LucideIcon;
  tint: string;
}

function CountTile({ label, value, icon: Icon, tint }: CountTileProps) {
  const animated = useCountUp(value);
  return (
    <div className="group relative overflow-hidden rounded-xl border border-slate-200 bg-white p-4 transition-all hover:-translate-y-0.5 hover:shadow-md">
      <div
        className="absolute -right-6 -top-6 h-20 w-20 rounded-full opacity-10 transition-opacity group-hover:opacity-20"
        style={{ background: tint }}
      />
      <div className="flex items-center justify-between">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
          {label}
        </p>
        <span
          className="inline-flex h-7 w-7 items-center justify-center rounded-lg"
          style={{ background: `${tint}1f`, color: tint }}
        >
          <Icon className="h-3.5 w-3.5" />
        </span>
      </div>
      <p className="mt-2 font-mono text-2xl font-bold tabular-nums text-ccm-ink">
        {formatInt(animated)}
      </p>
    </div>
  );
}

interface ScoreTileProps {
  label: string;
  value: number;
  icon: LucideIcon;
  thresholds: { medium: number; high: number };
}

function ScoreTile({ label, value, icon: Icon, thresholds }: ScoreTileProps) {
  const animated = useCountUp(value);
  const flag = scoreFlag(value, thresholds);
  const c = flagColors(flag);
  return (
    <div className="group relative overflow-hidden rounded-xl border border-slate-200 bg-white p-4 transition-all hover:-translate-y-0.5 hover:shadow-md">
      <div className="flex items-center justify-between">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
          {label}
        </p>
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wide ring-1",
            c.chip,
          )}
        >
          <Icon className="h-3 w-3" />
          {flag === "high" ? "élevé" : flag === "medium" ? "moyen" : "faible"}
        </span>
      </div>
      <div className="mt-2 flex items-baseline gap-1">
        <span
          className={cn(
            "font-mono text-2xl font-bold tabular-nums",
            c.text,
          )}
        >
          {Math.round(animated)}
        </span>
        <span className="text-xs font-medium text-slate-400">%</span>
      </div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-700 ease-out",
            c.bar,
          )}
          style={{ width: `${Math.min(100, Math.max(2, animated))}%` }}
        />
      </div>
    </div>
  );
}

interface RiskHeroProps {
  level: RiskKey;
  rawLevel: string;
}

function RiskHero({ level, rawLevel }: RiskHeroProps) {
  const theme = riskTheme(level);
  const intensity =
    level === "tres_eleve" ? 100 : level === "high" ? 80 : level === "medium" ? 50 : level === "low" ? 20 : 0;
  const animated = useCountUp(intensity, 1200);
  const radius = 36;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - animated / 100);

  const Icon =
    level === "tres_eleve" || level === "high"
      ? ShieldAlert
      : level === "medium"
        ? AlertTriangle
        : level === "low"
          ? ShieldCheck
          : Sparkles;

  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-xl bg-gradient-to-br p-5 text-white",
        theme.gradient,
        theme.glow,
      )}
    >
      {/* Decorative blobs */}
      <div className="pointer-events-none absolute -right-10 -top-10 h-40 w-40 rounded-full bg-white/10 blur-2xl" />
      <div className="pointer-events-none absolute -left-6 -bottom-6 h-32 w-32 rounded-full bg-white/10 blur-xl" />

      <div className="relative flex h-full items-center gap-4">
        {/* Radial gauge */}
        <div className="relative h-24 w-24 shrink-0">
          <svg
            viewBox="0 0 100 100"
            className="h-full w-full -rotate-90"
            aria-hidden
          >
            <circle
              cx="50"
              cy="50"
              r={radius}
              fill="none"
              stroke="rgba(255,255,255,0.25)"
              strokeWidth="8"
            />
            <circle
              cx="50"
              cy="50"
              r={radius}
              fill="none"
              stroke="white"
              strokeWidth="8"
              strokeLinecap="round"
              strokeDasharray={circumference}
              strokeDashoffset={offset}
              style={{ transition: "stroke-dashoffset 600ms ease-out" }}
            />
          </svg>
          <div className="absolute inset-0 flex items-center justify-center">
            <Icon className={cn("h-8 w-8", theme.accent)} />
          </div>
        </div>

        {/* Label */}
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-white/70">
            Niveau de risque
          </p>
          <div className="mt-1 flex items-center gap-2">
            <span className="relative flex h-2 w-2">
              <span
                className={cn(
                  "absolute inline-flex h-full w-full animate-ping rounded-full opacity-75",
                  theme.pulse,
                )}
              />
              <span
                className={cn(
                  "relative inline-flex h-2 w-2 rounded-full",
                  theme.pulse,
                )}
              />
            </span>
            <p className="text-2xl font-bold leading-tight">
              {formatRiskLabel(rawLevel)}
            </p>
          </div>
          <p className="mt-1 text-xs text-white/80">
            Synthèse éditoriale agrégée
          </p>
        </div>
      </div>
    </div>
  );
}

export function StatusCards({ analysis }: Props) {
  const docStats = analysis.document_stats ?? {};
  const plagiarism = analysis.plagiarism ?? {};
  const profanity = analysis.profanity ?? {};
  const adult = analysis.adult_content ?? {};
  const rag = analysis.rag_report ?? {};

  const words = Number(docStats.words_count ?? docStats.word_count ?? 0);
  const chunks = Number(docStats.chunks_count ?? docStats.chunk_count ?? 0);
  const similarity = toPercent(
    plagiarism.score ?? plagiarism.global_similarity_score ?? 0,
  );
  const profanityScore = toPercent(profanity.profanity_score);
  const adultScore = toPercent(adult.adult_content_score);
  const rawRisk = String(rag.risk_level ?? "unknown");
  const risk = normalizeRiskKey(rawRisk);

  return (
    <div className="space-y-3">
      <RiskHero level={risk} rawLevel={rawRisk} />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <CountTile
          label="Mots"
          value={words}
          icon={FileText}
          tint="#6366f1"
        />
        <CountTile
          label="Segments"
          value={chunks}
          icon={Layers}
          tint="#0ea5e9"
        />
        <ScoreTile
          label="Similarité"
          value={similarity}
          icon={Search}
          thresholds={{ medium: 40, high: 75 }}
        />
        <ScoreTile
          label="Vulgarité"
          value={profanityScore}
          icon={ShieldAlert}
          thresholds={{ medium: 20, high: 60 }}
        />
        <ScoreTile
          label="Contenu adulte"
          value={adultScore}
          icon={AlertTriangle}
          thresholds={{ medium: 20, high: 60 }}
        />
      </div>
    </div>
  );
}
