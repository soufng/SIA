import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import {
  PolarAngleAxis,
  RadialBar,
  RadialBarChart,
  ResponsiveContainer,
} from "recharts";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  ArrowRight,
  BarChart3,
  CheckCircle2,
  ClipboardCheck,
  ClipboardCopy,
  Crown,
  Download,
  FileText,
  Flag,
  Info,
  Landmark,
  ListChecks,
  Loader2,
  Moon,
  Search,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Target,
  Vote,
} from "lucide-react";
import { StatusCards } from "@/components/StatusCards";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Table, TBody, THead, Td, Th, Tr } from "@/components/ui/table";
import { useAnalysisStore } from "@/store/analysis";
// jspdf + jspdf-autotable + html2canvas weigh ~350 KB combined. We import
// them lazily so users who never download a PDF report don't pay that cost
// on page load.
async function downloadPdfReport(...args: Parameters<typeof import("@/lib/pdf").downloadPdfReport>) {
  const mod = await import("@/lib/pdf");
  return mod.downloadPdfReport(...args);
}
import { generateAdvancedReport, type AdvancedReport } from "@/lib/api";
import { cn, formatRiskLabel, formatScore, riskColor } from "@/lib/utils";
import type {
  Analysis,
  DuplicateAnalysis,
  MoroccanCategoryKey,
  MoroccanConstants,
  MoroccanFlag,
  MoroccanMention,
  NudityMatch,
  Plagiarism,
  PlagiarismMatch,
  PlagiarismSource,
  StrictMatch,
  StrictMatchVerdict,
  VulgarityMatch,
} from "@/lib/types";

// ---------- Helpers ----------

const CATEGORY_LABELS_FR: Record<string, string> = {
  offensive_words: "mots offensants",
  profanity: "vulgarité",
  insults: "insultes",
  violent_terms: "termes violents",
  sexual_terms: "termes sexuels",
  adult_content: "contenu adulte",
  wiqaya: "détecté par wiqaya",
  terms: "termes",
  unknown: "non classé",
  uncategorized: "non classé",
};

const LANGUAGE_LABELS_FR: Record<string, string> = {
  fr: "français",
  ar: "arabe",
  en: "anglais",
  darija: "darija",
  "ar/darija": "arabe/darija",
};

function translateCategory(category?: string): string {
  if (!category) return "non classé";
  const key = category.trim();
  return CATEGORY_LABELS_FR[key] ?? CATEGORY_LABELS_FR[key.toLowerCase()] ?? `non classé (${key})`;
}

function translateLanguage(language?: string): string {
  if (!language) return "inconnue";
  const key = language.trim();
  return LANGUAGE_LABELS_FR[key] ?? LANGUAGE_LABELS_FR[key.toLowerCase()] ?? key;
}

function truncate(text: string, max = 400): string {
  if (!text) return "";
  const compact = text.replace(/\s+/g, " ").trim();
  if (compact.length <= max) return compact;
  return compact.slice(0, max).trimEnd() + "...";
}

const RETRIEVAL_STATUS_FR: Record<string, string> = {
  qdrant_unavailable: "Qdrant indisponible",
  corpus_empty: "corpus vide",
  below_threshold: "sous le seuil de similarité",
  no_match: "aucune correspondance",
};

function translateRetrievalStatus(status?: string): string {
  if (!status) return "";
  return RETRIEVAL_STATUS_FR[status] ?? status;
}

function fallback(value: unknown): string {
  if (value === null || value === undefined) return "non disponible";
  const s = String(value).trim();
  if (!s || s.toLowerCase() === "none" || s.toLowerCase() === "null") {
    return "non disponible";
  }
  return s;
}

// Normalise les Arabic Presentation Forms (FExx) en caracteres standard,
// force un saut de ligne a chaque transition arabe<->latin, et separe les
// numeros de scene / didascalies. Sans ca les extraits PDF s'affichent en
// un seul pave illisible ou le mix FR/AR rend la lecture impossible.
function normalizeBilingualText(text: string | null | undefined): string {
  if (!text) return "";
  let out = String(text).normalize("NFKC");
  // Saut de ligne a chaque transition entre alphabet latin et arabe.
  // \p{Script=Latin} et \p{Script=Arabic} ciblent uniquement les lettres
  // (les chiffres et ponctuations ne declenchent pas un retour ligne).
  out = out.replace(/(\p{Script=Latin})(\s*)(\p{Script=Arabic})/gu, "$1\n$3");
  out = out.replace(/(\p{Script=Arabic})(\s*)(\p{Script=Latin})/gu, "$1\n$3");
  // Saut de ligne avant "<num>. " ou "<num> - " typiquement debut de scene
  out = out.replace(/\s*(\d{1,3}\s*[-.]\s+)/g, "\n$1");
  // Saut de ligne avant les didascalies majuscules courantes
  out = out.replace(
    /\s+((?:Int|Ext|INT|EXT|FONDU|RACCORD)[\s.\-/])/g,
    "\n$1"
  );
  // Saut de ligne quand un changement de speaker apparait apres un point
  out = out.replace(/([.!?…])\s+(?=[A-ZÀ-Ý؀-ۿ])/g, "$1\n");
  // Limite a 2 retours consecutifs
  out = out.replace(/\n{3,}/g, "\n\n");
  return out.trim();
}

// Bloc d'extrait bilingue FR/AR : NFKC + unicode-bidi:plaintext qui isole
// chaque ligne dans son propre contexte de direction, ce qui rend les
// melanges français/arabe lisibles sans casser le RTL.
function BilingualBlock({
  text,
  tone = "blue",
}: {
  text: string;
  tone?: "blue" | "amber";
}) {
  const colors =
    tone === "amber"
      ? "border-amber-200 bg-amber-50/50"
      : "border-blue-200 bg-blue-50/50";
  return (
    <p
      className={`text-slate-800 leading-loose whitespace-pre-wrap font-sans ${colors}`}
      style={{
        unicodeBidi: "plaintext",
        wordBreak: "break-word",
        fontFamily:
          "'Noto Sans Arabic', 'Segoe UI', system-ui, -apple-system, sans-serif",
      }}
      dir="auto"
    >
      {normalizeBilingualText(text)}
    </p>
  );
}

// Parsing minimaliste du markdown produit par le LLM RAG. On ne tire pas
// react-markdown pour ne pas alourdir le bundle, le format est tres
// previsible (titres "##", listes numerotees ou puces, gras "**...**").
type RAGInline =
  | { kind: "text"; value: string }
  | { kind: "bold"; value: string };

type RAGBlock =
  | { kind: "heading"; text: string }
  | { kind: "paragraph"; inlines: RAGInline[] }
  | { kind: "list"; ordered: boolean; items: RAGInline[][] };

function parseInlines(line: string): RAGInline[] {
  const parts: RAGInline[] = [];
  const re = /\*\*([^*]+)\*\*/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(line)) !== null) {
    if (m.index > last) {
      parts.push({ kind: "text", value: line.slice(last, m.index) });
    }
    parts.push({ kind: "bold", value: m[1] });
    last = m.index + m[0].length;
  }
  if (last < line.length) {
    parts.push({ kind: "text", value: line.slice(last) });
  }
  return parts.length ? parts : [{ kind: "text", value: line }];
}

function parseRAGMarkdown(text: string): RAGBlock[] {
  const blocks: RAGBlock[] = [];
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i].trim();
    if (!line) {
      i++;
      continue;
    }
    const heading = line.match(/^#{1,4}\s+(.+?):?\s*$/);
    if (heading) {
      blocks.push({ kind: "heading", text: heading[1].replace(/\*+/g, "") });
      i++;
      continue;
    }
    // Le LLM ecrit parfois "**Titre :**" seul sur sa ligne — on le promeut en titre.
    const boldHeading = line.match(/^\*\*([^*]+?)\s*:?\*\*\s*:?\s*$/);
    if (boldHeading) {
      blocks.push({ kind: "heading", text: boldHeading[1] });
      i++;
      continue;
    }
    if (/^(\d+\.|[-*•])\s+/.test(line)) {
      const items: RAGInline[][] = [];
      const ordered = /^\d+\./.test(line);
      while (i < lines.length) {
        const cur = lines[i].trim();
        if (!cur) {
          i++;
          continue;
        }
        const itemMatch = cur.match(/^(?:\d+\.|[-*•])\s+(.+)$/);
        if (!itemMatch) break;
        items.push(parseInlines(itemMatch[1]));
        i++;
      }
      blocks.push({ kind: "list", ordered, items });
      continue;
    }
    blocks.push({ kind: "paragraph", inlines: parseInlines(line) });
    i++;
  }
  return blocks;
}

const RAG_SECTION_META: Record<
  string,
  { icon: typeof FileText; tone: string; accent: string }
> = {
  synthese: { icon: Sparkles, tone: "text-blue-700", accent: "border-l-blue-500" },
  interpretation: { icon: BarChart3, tone: "text-violet-700", accent: "border-l-violet-500" },
  analyse: { icon: Search, tone: "text-amber-700", accent: "border-l-amber-500" },
  consequence: { icon: AlertTriangle, tone: "text-rose-700", accent: "border-l-rose-500" },
  duplication: { icon: ClipboardCopy, tone: "text-orange-700", accent: "border-l-orange-500" },
  moderation: { icon: ShieldAlert, tone: "text-rose-700", accent: "border-l-rose-500" },
  constantes: { icon: Landmark, tone: "text-emerald-700", accent: "border-l-emerald-500" },
  limites: { icon: Info, tone: "text-slate-700", accent: "border-l-slate-400" },
  action: { icon: ListChecks, tone: "text-emerald-700", accent: "border-l-emerald-500" },
  recommand: { icon: ListChecks, tone: "text-emerald-700", accent: "border-l-emerald-500" },
  conclusion: { icon: Target, tone: "text-slate-800", accent: "border-l-slate-700" },
};

function sectionMetaFor(title: string) {
  const norm = title
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "");
  for (const key of Object.keys(RAG_SECTION_META)) {
    if (norm.includes(key)) return RAG_SECTION_META[key];
  }
  return { icon: FileText, tone: "text-slate-700", accent: "border-l-slate-400" };
}

function RAGInlineRun({ parts }: { parts: RAGInline[] }) {
  return (
    <>
      {parts.map((part, i) =>
        part.kind === "bold" ? (
          <strong key={i} className="font-semibold text-slate-900">
            {part.value}
          </strong>
        ) : (
          <span key={i}>{part.value}</span>
        )
      )}
    </>
  );
}

function RAGNarrative({ narrative }: { narrative: string }) {
  const blocks = useMemo(() => parseRAGMarkdown(narrative || ""), [narrative]);
  if (!narrative?.trim()) {
    return (
      <div className="rounded-md border border-slate-200 bg-slate-50 p-4 text-sm text-slate-500 italic">
        Rapport vide.
      </div>
    );
  }

  // Regroupe les blocs par section : un heading ouvre une nouvelle section,
  // les blocs suivants y sont accumules jusqu'au prochain heading.
  const sections: { title: string | null; blocks: RAGBlock[] }[] = [];
  let current: { title: string | null; blocks: RAGBlock[] } = {
    title: null,
    blocks: [],
  };
  for (const b of blocks) {
    if (b.kind === "heading") {
      if (current.title || current.blocks.length) sections.push(current);
      current = { title: b.text, blocks: [] };
    } else {
      current.blocks.push(b);
    }
  }
  if (current.title || current.blocks.length) sections.push(current);

  return (
    <div className="space-y-4">
      {sections.map((section, idx) => {
        const meta = section.title
          ? sectionMetaFor(section.title)
          : { icon: FileText, tone: "text-slate-700", accent: "border-l-slate-300" };
        const Icon = meta.icon;
        return (
          <section
            key={idx}
            className={cn(
              "rounded-r-md border-l-4 bg-white shadow-sm border border-slate-200 px-5 py-4",
              meta.accent
            )}
          >
            {section.title && (
              <h3
                className={cn(
                  "flex items-center gap-2 font-semibold text-base mb-3",
                  meta.tone
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                {section.title}
              </h3>
            )}
            <div className="space-y-3 text-sm leading-relaxed text-slate-800">
              {section.blocks.map((b, j) => {
                if (b.kind === "paragraph") {
                  return (
                    <p
                      key={j}
                      dir="auto"
                      style={{ unicodeBidi: "plaintext" }}
                    >
                      <RAGInlineRun parts={b.inlines} />
                    </p>
                  );
                }
                if (b.kind === "list") {
                  if (b.ordered) {
                    return (
                      <ol
                        key={j}
                        className="space-y-2 ml-1"
                        dir="auto"
                        style={{ unicodeBidi: "plaintext" }}
                      >
                        {b.items.map((item, k) => (
                          <li key={k} className="flex gap-3">
                            <span className="shrink-0 h-6 w-6 rounded-full bg-ccm-red/10 text-ccm-red text-xs font-semibold flex items-center justify-center">
                              {k + 1}
                            </span>
                            <span className="flex-1">
                              <RAGInlineRun parts={item} />
                            </span>
                          </li>
                        ))}
                      </ol>
                    );
                  }
                  return (
                    <ul
                      key={j}
                      className="space-y-2 ml-1"
                      dir="auto"
                      style={{ unicodeBidi: "plaintext" }}
                    >
                      {b.items.map((item, k) => (
                        <li key={k} className="flex gap-3">
                          <span className="shrink-0 mt-1.5 h-1.5 w-1.5 rounded-full bg-ccm-red" />
                          <span className="flex-1">
                            <RAGInlineRun parts={item} />
                          </span>
                        </li>
                      ))}
                    </ul>
                  );
                }
                return null;
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}

interface VulgarityGroup {
  word: string;
  count: number;
  language: string;
  category: string;
}

function groupVulgarity(matches: VulgarityMatch[]): VulgarityGroup[] {
  const groups = new Map<string, VulgarityGroup>();
  const latin = /^[A-Za-zÀ-ÿ' \-]+$/;
  for (const m of matches) {
    if (!m.word) continue;
    const key = latin.test(m.word) ? m.word.toLowerCase() : m.word;
    const display = latin.test(m.word) ? m.word.toLowerCase() : m.word;
    const existing = groups.get(key);
    if (existing) {
      existing.count += 1;
    } else {
      groups.set(key, {
        word: display,
        count: 1,
        language: translateLanguage(m.language),
        category: translateCategory(m.category),
      });
    }
  }
  return Array.from(groups.values()).sort((a, b) => b.count - a.count);
}

// ---------- Section components ----------

// ---------- Strict-similarity verdict banner (renewal workflow) ----------

function StrictMatchBanner({ match }: { match: StrictMatch | undefined }) {
  const navigate = useNavigate();
  const setAnalysisInStore = useAnalysisStore((s) => s.setAnalysis);

  if (!match) return null;

  const VERDICT_STYLES: Record<
    StrictMatchVerdict,
    {
      bg: string;
      border: string;
      iconBg: string;
      icon: typeof ShieldAlert;
      title: string;
      tone: "high" | "medium" | "low" | "info";
      gaugeStroke: string;
      gaugeText: string;
      kicker: string;
      kickerColor: string;
      accentBar: string;
    }
  > = {
    identical: {
      bg: "bg-gradient-to-br from-red-50 via-white to-red-50/40",
      border: "border-ccm-red/40",
      iconBg: "bg-gradient-to-br from-ccm-red-light via-ccm-red to-ccm-red-dark text-white ring-ccm-gold/30",
      icon: ShieldAlert,
      title: "Scénario STRICTEMENT IDENTIQUE",
      tone: "high",
      gaugeStroke: "#b91c1c",
      gaugeText: "text-ccm-red-dark",
      kicker: "Doublon exact détecté",
      kickerColor: "text-ccm-red-dark",
      accentBar: "from-ccm-red via-ccm-red-light to-ccm-gold",
    },
    near_identical: {
      bg: "bg-gradient-to-br from-amber-50 via-white to-amber-50/40",
      border: "border-amber-400/50",
      iconBg: "bg-gradient-to-br from-amber-300 via-amber-500 to-orange-600 text-white ring-amber-200",
      icon: AlertTriangle,
      title: "Scénario QUASI-IDENTIQUE",
      tone: "medium",
      gaugeStroke: "#d97706",
      gaugeText: "text-amber-700",
      kicker: "Modifications mineures détectées",
      kickerColor: "text-amber-700",
      accentBar: "from-amber-400 via-orange-500 to-amber-500",
    },
    highly_similar: {
      bg: "bg-gradient-to-br from-yellow-50 via-white to-yellow-50/40",
      border: "border-yellow-400/50",
      iconBg: "bg-gradient-to-br from-yellow-300 via-yellow-500 to-amber-600 text-white ring-yellow-200",
      icon: Info,
      title: "Scénario FORTEMENT SIMILAIRE",
      tone: "medium",
      gaugeStroke: "#ca8a04",
      gaugeText: "text-yellow-700",
      kicker: "Forte proximité avec un scénario antérieur",
      kickerColor: "text-yellow-700",
      accentBar: "from-yellow-400 via-amber-500 to-yellow-500",
    },
    different: {
      bg: "bg-gradient-to-br from-emerald-50 via-white to-teal-50/40",
      border: "border-emerald-300/60",
      iconBg: "bg-gradient-to-br from-emerald-400 via-emerald-500 to-teal-600 text-white ring-emerald-200",
      icon: CheckCircle2,
      title: "NOUVEAU scénario",
      tone: "low",
      gaugeStroke: "#10b981",
      gaugeText: "text-emerald-700",
      kicker: "Aucune correspondance antérieure",
      kickerColor: "text-emerald-700",
      accentBar: "from-emerald-400 via-teal-500 to-emerald-500",
    },
  };

  const style = VERDICT_STYLES[match.verdict];
  const Icon = style.icon;
  const scorePct = Math.max(0, Math.min(100, match.score_percent));
  const radius = 32;
  const circumference = 2 * Math.PI * radius;
  const dashOffset = circumference * (1 - scorePct / 100);
  const matched = match.matched_analysis;

  const matchedName =
    matched?.original_filename ||
    matched?.stored_filename ||
    null;

  // The verdict headline puts the previous scenario's filename front and
  // centre — operators recognise filenames, not UUIDs. We keep the
  // technical scenario_id available, but tuck it inside a details
  // disclosure so it doesn't compete with the filename for attention.
  const headline: string = (() => {
    switch (match.verdict) {
      case "identical":
        return matchedName
          ? `Ce scénario est identique à « ${matchedName} »`
          : "Scénario STRICTEMENT IDENTIQUE";
      case "near_identical":
        return matchedName
          ? `Quasi-identique à « ${matchedName} »`
          : "Scénario QUASI-IDENTIQUE";
      case "highly_similar":
        return matchedName
          ? `Très proche de « ${matchedName} »`
          : "Scénario FORTEMENT SIMILAIRE";
      case "different":
      default:
        return "Nouveau scénario";
    }
  })();

  const openMatchedAnalysis = () => {
    // We don't have the full analysis cached — navigate to history; the
    // operator can click the matched entry there. We also push the
    // scenarioId hint via store so the history page can highlight it later.
    if (matched?.scenario_id) {
      setAnalysisInStore({ scenario_id: matched.scenario_id }, matched.scenario_id);
    }
    navigate("/history");
  };

  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-2xl border-2 p-5 shadow-sm space-y-4",
        style.bg,
        style.border,
      )}
    >
      {/* Decorative blobs to lift the surface */}
      <div
        className="pointer-events-none absolute -right-12 -top-12 h-40 w-40 rounded-full blur-3xl opacity-30"
        style={{ background: style.gaugeStroke }}
      />
      <div
        className="pointer-events-none absolute -left-16 -bottom-16 h-40 w-40 rounded-full blur-3xl opacity-20"
        style={{ background: style.gaugeStroke }}
      />

      <div className="relative flex flex-wrap items-start justify-between gap-5">
        {/* Left — kicker + headline + reason */}
        <div className="flex items-start gap-4 min-w-0 flex-1">
          <span
            className={cn(
              "relative inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-xl shadow-md ring-1",
              style.iconBg,
            )}
          >
            <Icon className="h-5 w-5" />
            {match.verdict === "different" && (
              <span className="absolute -right-1 -top-1 inline-flex h-3.5 w-3.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex h-3.5 w-3.5 rounded-full bg-emerald-500 ring-2 ring-white" />
              </span>
            )}
          </span>
          <div className="min-w-0">
            <p
              className={cn(
                "text-[10px] font-semibold uppercase tracking-[0.18em]",
                style.kickerColor,
              )}
            >
              {style.kicker}
            </p>
            <p className="mt-1 text-xl font-bold tracking-tight text-ccm-ink">
              {headline}
            </p>
            <p className="mt-1.5 text-sm leading-relaxed text-slate-600">
              {match.reason}
            </p>
            {match.is_renewal_candidate && (
              <span className="mt-2 inline-flex items-center gap-1.5 rounded-full border border-ccm-red bg-white px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-ccm-red">
                <CheckCircle2 className="h-3 w-3" />
                Prolongation candidate
              </span>
            )}
          </div>
        </div>

        {/* Right — circular gauge with score */}
        <div className="flex shrink-0 flex-col items-center gap-1">
          <div className="relative h-20 w-20">
            <svg
              viewBox="0 0 80 80"
              className="h-full w-full -rotate-90"
              aria-hidden
            >
              <circle
                cx="40"
                cy="40"
                r={radius}
                fill="none"
                stroke="rgba(15,23,42,0.08)"
                strokeWidth="6"
              />
              <circle
                cx="40"
                cy="40"
                r={radius}
                fill="none"
                stroke={style.gaugeStroke}
                strokeWidth="6"
                strokeLinecap="round"
                strokeDasharray={circumference}
                strokeDashoffset={dashOffset}
                style={{ transition: "stroke-dashoffset 700ms ease-out" }}
              />
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <span
                className={cn(
                  "font-mono text-base font-bold tabular-nums",
                  style.gaugeText,
                )}
              >
                {scorePct.toFixed(0)}%
              </span>
            </div>
          </div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
            Similarité
          </p>
        </div>
      </div>

      {matched && match.verdict !== "different" && (
        <div className="rounded-md bg-white/70 border border-current/20 p-3 text-sm space-y-3">
          <p className="text-xs uppercase tracking-wide opacity-70 font-semibold">
            Scénario antérieur correspondant
          </p>
          <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
            <div className="min-w-0 flex-1">
              <p className="text-xs opacity-60">Nom du fichier original</p>
              <p className="text-base font-semibold break-all">
                {matchedName ?? "(nom non disponible)"}
              </p>
            </div>
            <div>
              <p className="text-xs opacity-60">Uploadé le</p>
              <p className="font-medium">
                {matched.analyzed_at
                  ? new Date(matched.analyzed_at).toLocaleString("fr-FR")
                  : "non disponible"}
              </p>
            </div>
            <div>
              <p className="text-xs opacity-60">Type de correspondance</p>
              <p className="font-medium">
                {match.match_type === "file_hash" &&
                  "Fichier PDF strictement identique"}
                {match.match_type === "text_hash" &&
                  "Texte du document strictement identique"}
                {match.match_type === "global_jaccard" &&
                  "Similarité globale élevée"}
                {match.match_type === "none" && "—"}
              </p>
            </div>
          </div>
          <details className="text-xs">
            <summary className="cursor-pointer opacity-70 hover:opacity-100">
              Informations techniques (scénario ID, hash)
            </summary>
            <dl className="mt-2 grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1.5 font-mono">
              <div>
                <dt className="opacity-60">Scénario ID</dt>
                <dd className="break-all">
                  {matched.scenario_id || "—"}
                </dd>
              </div>
              {matched.file_hash && (
                <div>
                  <dt className="opacity-60">File hash</dt>
                  <dd className="break-all">{matched.file_hash}</dd>
                </div>
              )}
              {matched.text_hash && (
                <div>
                  <dt className="opacity-60">Text hash</dt>
                  <dd className="break-all">{matched.text_hash}</dd>
                </div>
              )}
            </dl>
          </details>
        </div>
      )}

      {match.extras && match.extras.length > 0 && match.verdict !== "different" && (
        <details className="rounded-md bg-white/40 border border-current/20 p-2 text-xs">
          <summary className="cursor-pointer font-medium">
            Voir les {match.extras.length} autre(s) analyse(s) proche(s)
          </summary>
          <ul className="mt-2 space-y-1.5 pl-4 list-disc">
            {match.extras.map((e, i) => {
              const name =
                e.original_filename || e.stored_filename || "(sans nom)";
              return (
                <li key={i}>
                  <span className="font-medium">« {name} »</span> —{" "}
                  {Math.round(e.score_percent)}%
                </li>
              );
            })}
          </ul>
        </details>
      )}

      {matched && match.verdict !== "different" && (
        <div className="flex justify-end pt-1">
          <Button
            variant="outline"
            onClick={openMatchedAnalysis}
            className="bg-white"
          >
            <ArrowRight className="h-4 w-4" />
            Voir l'analyse antérieure
          </Button>
        </div>
      )}

      <div className="relative flex flex-wrap items-center justify-between gap-3 border-t border-slate-200/60 pt-3">
        <span className="inline-flex items-center gap-2 rounded-full bg-white/70 px-2.5 py-1 text-[11px] font-medium text-slate-600 ring-1 ring-slate-200 backdrop-blur">
          <Search className="h-3 w-3" />
          Comparé à
          <span className="font-mono font-bold tabular-nums text-ccm-ink">
            {match.candidates_compared}
          </span>
          analyse{match.candidates_compared > 1 ? "s" : ""} historique
          {match.candidates_compared > 1 ? "s" : ""}
        </span>
        {match.is_renewal_candidate && match.verdict === "identical" && (
          <span className="inline-flex items-center gap-1.5 rounded-md bg-ccm-red/10 px-2.5 py-1 text-[11px] font-semibold text-ccm-red-dark ring-1 ring-ccm-red/30">
            <Info className="h-3 w-3" />
            Réutilisable pour une demande de prolongation d'autorisation
          </span>
        )}
      </div>

      {/* Bottom accent line — consistent with the recommendations /
          conclusion / pipeline cards. */}
      <div
        className={cn(
          "absolute inset-x-0 bottom-0 h-1 bg-gradient-to-r opacity-80",
          style.accentBar,
        )}
      />
    </div>
  );
}

function HeaderSection({
  analysis,
  scenarioId,
  onDownload,
}: {
  analysis: Analysis;
  scenarioId: string | null;
  onDownload: () => void;
}) {
  const risk = String(analysis.rag_report?.risk_level ?? "unknown");
  return (
    <div className="space-y-2">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold text-ccm-ink">
            Résultats de l'analyse
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Scenario ID :{" "}
            <span className="font-mono text-slate-700">
              {fallback(scenarioId ?? analysis.scenario_id)}
            </span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge className={cn(riskColor(risk), "text-sm px-3 py-1")}>
            Risque : {formatRiskLabel(risk)}
          </Badge>
          <Button onClick={onDownload} variant="outline">
            <Download className="h-4 w-4" />
            Télécharger PDF
          </Button>
        </div>
      </header>
    </div>
  );
}

// ---------- Synthèse hero ----------

/** Easing count-up. Kept local so the hook doesn't leak outside this file. */
function useCountUp(target: number, durationMs = 900): number {
  const [display, setDisplay] = useState(0);
  const startedAtRef = useRef<number | null>(null);
  useEffect(() => {
    if (!Number.isFinite(target)) {
      setDisplay(0);
      return;
    }
    startedAtRef.current = performance.now();
    let frame = 0;
    const step = (now: number) => {
      const started = startedAtRef.current ?? now;
      const t = Math.min(1, (now - started) / durationMs);
      const eased = 1 - Math.pow(1 - t, 3);
      setDisplay(target * eased);
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [target, durationMs]);
  return display;
}

interface SummaryIndicator {
  label: string;
  /** Value in 0..100 (already normalised). */
  value: number;
  /** Source unit shown next to the value (% / 100). */
  unit: "%" | "/100";
  thresholds: { medium: number; high: number };
  icon: typeof FileText;
}

function summaryRiskTheme(risk: string): {
  bar: string;
  border: string;
  tint: string;
  dot: string;
  text: string;
  badge: string;
  label: string;
  ring: string;
} {
  const k = String(risk || "").toLowerCase().trim();
  if (k === "tres_eleve" || k === "tres eleve" || k === "très élevé") {
    return {
      bar: "from-red-100 via-red-50 to-white",
      border: "border-red-300",
      tint: "bg-red-200 text-red-900",
      dot: "bg-red-600",
      text: "text-red-800",
      badge: "bg-red-700 text-white",
      label: "Risque très élevé",
      ring: "#b91c1c",
    };
  }
  if (k === "high" || k === "eleve" || k === "élevé") {
    return {
      bar: "from-rose-50 via-rose-50 to-white",
      border: "border-rose-200",
      tint: "bg-rose-100 text-rose-700",
      dot: "bg-rose-500",
      text: "text-rose-700",
      badge: "bg-rose-600 text-white",
      label: "Risque élevé",
      ring: "#ef4444",
    };
  }
  if (k === "medium" || k === "moyen") {
    return {
      bar: "from-amber-50 via-amber-50 to-white",
      border: "border-amber-200",
      tint: "bg-amber-100 text-amber-700",
      dot: "bg-amber-500",
      text: "text-amber-700",
      badge: "bg-amber-500 text-white",
      label: "Risque modéré",
      ring: "#f59e0b",
    };
  }
  return {
    bar: "from-emerald-50 via-emerald-50 to-white",
    border: "border-emerald-200",
    tint: "bg-emerald-100 text-emerald-700",
    dot: "bg-emerald-500",
    text: "text-emerald-700",
    badge: "bg-emerald-600 text-white",
    label: "Risque faible",
    ring: "#10b981",
  };
}

function IndicatorBar({
  indicator,
  riskRing,
}: {
  indicator: SummaryIndicator;
  riskRing: string;
}) {
  const animated = useCountUp(indicator.value);
  const level =
    indicator.value >= indicator.thresholds.high
      ? "high"
      : indicator.value >= indicator.thresholds.medium
        ? "medium"
        : "low";
  const levelColor =
    level === "high"
      ? "bg-rose-500"
      : level === "medium"
        ? "bg-amber-500"
        : "bg-emerald-500";
  const Icon = indicator.icon;
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="flex items-center gap-2 text-slate-700">
          <span
            className="inline-flex h-6 w-6 items-center justify-center rounded-md"
            style={{ background: `${riskRing}1f`, color: riskRing }}
          >
            <Icon className="h-3.5 w-3.5" />
          </span>
          <span className="font-medium text-ccm-ink">{indicator.label}</span>
        </span>
        <span className="flex items-center gap-2">
          <span className="font-mono text-sm font-semibold tabular-nums text-ccm-ink">
            {`${Math.round(animated)}%`}
          </span>
          <Badge
            className={cn(
              "text-[10px] uppercase tracking-wide",
              level === "high" && "bg-rose-100 text-rose-700 border-rose-200",
              level === "medium" &&
                "bg-amber-100 text-amber-700 border-amber-200",
              level === "low" &&
                "bg-emerald-100 text-emerald-700 border-emerald-200",
            )}
          >
            {level === "high" ? "élevé" : level === "medium" ? "moyen" : "faible"}
          </Badge>
        </span>
      </div>
      <div className="relative h-2 w-full overflow-hidden rounded-full bg-slate-100">
        {/* Threshold ticks */}
        <div
          className="absolute inset-y-0 w-px bg-slate-300"
          style={{ left: `${indicator.thresholds.medium}%` }}
        />
        <div
          className="absolute inset-y-0 w-px bg-slate-300"
          style={{ left: `${indicator.thresholds.high}%` }}
        />
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-500 ease-out",
            levelColor,
          )}
          style={{ width: `${Math.min(100, Math.max(0, animated))}%` }}
        />
      </div>
      <div className="flex justify-between text-[10px] uppercase tracking-wide text-slate-400">
        <span>0</span>
        <span style={{ marginLeft: `${indicator.thresholds.medium - 5}%` }}>
          moyen
        </span>
        <span style={{ marginLeft: `${indicator.thresholds.high - 60}%` }}>
          élevé
        </span>
        <span>100</span>
      </div>
    </div>
  );
}

function SummarySection({ analysis }: { analysis: Analysis }) {
  const rag = analysis.rag_report ?? {};
  const plagiarism = analysis.plagiarism ?? {};
  const profanity = analysis.profanity ?? {};
  const adult = analysis.adult_content ?? {};
  const moroccan = analysis.moroccan_constants ?? {};

  // Backend scores arrivent en 0..1 (fraction) OU en 0..100 (pourcentage)
  // selon le pipeline. On normalise toujours en 0..100 pour l'affichage.
  const toPercent = (raw: unknown): number => {
    const n = Number(raw ?? 0);
    if (!Number.isFinite(n)) return 0;
    return n <= 1 ? n * 100 : n;
  };
  const plagiarismScore = toPercent(
    plagiarism.global_similarity_score ?? plagiarism.score ?? 0,
  );
  const profanityScore = toPercent(profanity.profanity_score);
  const adultScore = toPercent(adult.adult_content_score);
  const moroccanScore = toPercent(moroccan.score);
  const isDuplicate = Boolean(plagiarism.exact_duplicate);
  const duplicateCount = Number(plagiarism.duplicate_count ?? 0);
  const duplicateScore = isDuplicate ? 100 : 0;

  // Score global pondéré : addition des cinq indicateurs
  //   plagiat 30% · doublon 20% · vulgarité 15% · contenu adulte 15%
  //   · constantes Maroc 20%
  let globalRiskScore = Math.min(
    100,
    Math.max(
      0,
      0.3 * plagiarismScore +
        0.2 * duplicateScore +
        0.15 * profanityScore +
        0.15 * adultScore +
        0.2 * moroccanScore,
    ),
  );

  // Plancher : si le backend a escaladé le risque (atteinte aux constantes
  // nationales, etc.), le score global ne peut pas être inférieur au seuil
  // ÉLEVÉ — sinon la jauge contredit le badge.
  if (rag.risk_level_floored_by) {
    globalRiskScore = Math.max(globalRiskScore, 75);
  }

  // Le niveau de risque éditorial vient du backend (rag.risk_level) ; si
  // absent, on dérive un niveau du score global agrégé.
  const aggregatedRisk =
    globalRiskScore >= 75
      ? "high"
      : globalRiskScore >= 40
        ? "medium"
        : "low";
  const risk = String(rag.risk_level ?? aggregatedRisk);
  const theme = summaryRiskTheme(risk);
  const Icon =
    risk === "high"
      ? ShieldAlert
      : risk === "medium"
        ? AlertTriangle
        : CheckCircle2;

  const animatedScore = useCountUp(globalRiskScore);

  // RadialBar gauge data — single value rendered as an arc.
  const gaugeData = [
    {
      name: "score",
      value: Math.max(0, Math.min(100, globalRiskScore)),
      fill: theme.ring,
    },
  ];

  const indicators: SummaryIndicator[] = [
    {
      label: isDuplicate
        ? `Doublon exact (${duplicateCount} copie${duplicateCount > 1 ? "s" : ""})`
        : "Doublon exact",
      value: duplicateScore,
      unit: "/100",
      thresholds: { medium: 50, high: 100 },
      icon: ClipboardCheck,
    },
    {
      label: "Plagiat (similarité globale)",
      value: plagiarismScore,
      unit: "%",
      thresholds: { medium: 40, high: 75 },
      icon: Search,
    },
    {
      label: "Vulgarité",
      value: profanityScore,
      unit: "%",
      thresholds: { medium: 20, high: 60 },
      icon: ShieldAlert,
    },
    {
      label: "Contenu adulte",
      value: adultScore,
      unit: "%",
      thresholds: { medium: 20, high: 60 },
      icon: AlertTriangle,
    },
    {
      label: "Constantes nationales Maroc",
      value: moroccanScore,
      unit: "/100",
      thresholds: { medium: 20, high: 60 },
      icon: Landmark,
    },
  ];

  // Paragraphe de synthèse : résume les métriques et explique comment le
  // score global est obtenu.
  const aggregatedParagraph =
    `Le score global de risque s'élève à ${Math.round(globalRiskScore)}%, ` +
    `obtenu par addition pondérée des cinq indicateurs : ` +
    `${isDuplicate ? `doublon exact détecté (${duplicateCount} copie${duplicateCount > 1 ? "s antérieures" : " antérieure"})` : "aucun doublon exact"}, ` +
    `plagiat ${Math.round(plagiarismScore)}%, ` +
    `vulgarité ${Math.round(profanityScore)}%, ` +
    `contenu adulte ${Math.round(adultScore)}%, ` +
    `constantes Maroc ${Math.round(moroccanScore)}%.` +
    (rag.risk_level_floored_by
      ? ` Le score a été relevé au niveau ${theme.label.replace("Risque ", "").toUpperCase()} ` +
        `parce que la vérification des constantes nationales a déclenché ` +
        `une escalade automatique.`
      : "");

  return (
    <Card
      className={cn(
        "relative overflow-hidden border-2",
        theme.border,
      )}
    >
      {/* Soft gradient backdrop tinted by risk level */}
      <div
        className={cn(
          "pointer-events-none absolute inset-0 bg-gradient-to-br",
          theme.bar,
        )}
      />
      <div
        className={cn(
          "absolute inset-x-0 top-0 h-1.5",
          risk === "high" && "bg-rose-500",
          risk === "medium" && "bg-amber-500",
          (risk === "low" || risk === "unknown") && "bg-emerald-500",
        )}
      />
      <CardContent className="relative space-y-6 pt-6">
        {/* ---------- Top row : gauge + verdict ---------- */}
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[220px_1fr] items-center">
          {/* Gauge */}
          <div className="relative mx-auto h-44 w-44">
            <ResponsiveContainer width="100%" height="100%">
              <RadialBarChart
                data={gaugeData}
                startAngle={210}
                endAngle={-30}
                innerRadius="74%"
                outerRadius="100%"
              >
                <PolarAngleAxis
                  type="number"
                  domain={[0, 100]}
                  tick={false}
                />
                <RadialBar
                  dataKey="value"
                  background={{ fill: "#e2e8f0" }}
                  cornerRadius={12}
                  isAnimationActive
                />
              </RadialBarChart>
            </ResponsiveContainer>
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
              <span className="text-[10px] uppercase tracking-wider text-slate-500">
                Score global
              </span>
              <span
                className="font-mono text-3xl font-bold tabular-nums"
                style={{ color: theme.ring }}
              >
                {animatedScore.toFixed(0)}%
              </span>
              <Badge className={cn("mt-1", theme.badge)}>
                {formatRiskLabel(risk)}
              </Badge>
            </div>
          </div>

          {/* Verdict + summary */}
          <div className="space-y-3">
            <div className="flex items-start gap-3">
              <div
                className={cn(
                  "inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg",
                  theme.tint,
                )}
              >
                <Icon className="h-5 w-5" />
              </div>
              <div>
                <p
                  className={cn(
                    "text-[11px] font-semibold uppercase tracking-wider",
                    theme.text,
                  )}
                >
                  Synthèse éditoriale
                </p>
                <p className="mt-0.5 text-lg font-semibold text-ccm-ink">
                  {theme.label}
                </p>
              </div>
            </div>

            {rag.risk_level_floored_by && (
              <div
                className={cn(
                  "rounded-md border px-3 py-2 text-xs leading-relaxed",
                  theme.tint,
                )}
              >
                <span className="font-semibold">Niveau de risque relevé.</span>{" "}
                Le badge affiche{" "}
                <span className="font-semibold">{theme.label}</span> parce
                que la vérification des constantes nationales
                {rag.risk_level_floored_by === "moroccan_constants"
                  ? " marocaines"
                  : ""}{" "}
                a détecté un signal supérieur à ce que dit la synthèse
                automatique ci-dessous (rédigée avant l'escalade).
              </div>
            )}

            <p className="text-sm leading-relaxed text-slate-700">
              {rag.summary ?? "Aucun résumé disponible."}
            </p>

            {/* Paragraphe d'agrégation : addition pondérée des 4 indicateurs */}
            <div
              className={cn(
                "rounded-md border bg-white/80 px-3 py-2.5 text-sm leading-relaxed",
                theme.border,
              )}
            >
              <p className="text-[10px] uppercase tracking-wider font-semibold text-slate-500 mb-1">
                Score global agrégé
              </p>
              <p className="text-slate-700">{aggregatedParagraph}</p>
              <p className="mt-1 text-[10px] text-slate-400 italic">
                Pondération : plagiat 30% · doublon 20% · vulgarité 15% ·
                contenu adulte 15% · constantes Maroc 20%.
              </p>
            </div>

            {rag.risk_justification &&
              rag.risk_justification !== rag.summary && (
                <blockquote
                  className={cn(
                    "border-l-4 pl-4 text-sm italic leading-relaxed text-slate-600",
                    (risk === "tres_eleve" || risk === "très élevé") &&
                      "border-red-500",
                    risk === "high" && "border-rose-400",
                    risk === "medium" && "border-amber-400",
                    (risk === "low" || risk === "unknown") &&
                      "border-emerald-400",
                  )}
                >
                  {rag.risk_justification}
                </blockquote>
              )}
          </div>
        </div>

        {/* ---------- Bottom row : indicator bars ---------- */}
        <div className="space-y-4 rounded-lg border border-slate-200 bg-white/70 p-4 backdrop-blur">
          <div className="flex items-center justify-between">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              Indicateurs clés
            </p>
            <p className="text-[10px] text-slate-400">
              seuils : 50 / 100 sur le doublon — 40 / 75 sur le plagiat —
              20 / 60 sur la modération
            </p>
          </div>
          {indicators.map((ind) => (
            <IndicatorBar
              key={ind.label}
              indicator={ind}
              riskRing={theme.ring}
            />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function DocumentStatsTable({ analysis }: { analysis: Analysis }) {
  const stats = analysis.document_stats ?? {};
  const rows: Array<[string, unknown]> = [
    ["Nom du fichier original", stats.original_filename],
    ["Nom stocké", stats.file_name],
    ["Nombre de mots", stats.words_count ?? stats.word_count],
    ["Nombre de segments", stats.chunks_count ?? stats.chunk_count],
    [
      "Caractères extraits",
      (stats as Record<string, unknown>).raw_characters_count,
    ],
    [
      "Caractères nettoyés",
      (stats as Record<string, unknown>).cleaned_characters_count,
    ],
  ].filter(([, v]) => v !== undefined && v !== null && v !== "") as Array<
    [string, unknown]
  >;

  if (rows.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Statistiques du document</CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TBody>
            {rows.map(([label, value]) => (
              <Tr key={label}>
                <Td className="font-medium text-slate-600 w-1/2">{label}</Td>
                <Td className="text-slate-900">{String(value)}</Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function ScoreTable({ analysis }: { analysis: Analysis }) {
  const similarity = Number(
    analysis.plagiarism?.global_similarity_score ??
      analysis.plagiarism?.score ??
      0
  );
  const profanity = Number(analysis.profanity?.profanity_score ?? 0);
  const adult = Number(analysis.adult_content?.adult_content_score ?? 0);

  const rows = [
    {
      label: "Similarité globale",
      score: formatScore(similarity, "%"),
      raw: similarity <= 1 ? similarity * 100 : similarity,
      threshold: "≥ 75% → HIGH · ≥ 40% → MEDIUM",
      flag:
        similarity >= 0.75 || similarity >= 75
          ? "high"
          : similarity >= 0.4 || similarity >= 40
            ? "medium"
            : "low",
    },
    {
      label: "Vulgarité",
      score: `${profanity.toFixed(2)} / 100`,
      raw: profanity,
      threshold: "> 60 → HIGH · > 20 → MEDIUM",
      flag: profanity > 60 ? "high" : profanity > 20 ? "medium" : profanity > 0 ? "low" : "low",
    },
    {
      label: "Contenu adulte",
      score: `${adult.toFixed(2)} / 100`,
      raw: adult,
      threshold: "> 60 → HIGH · > 20 → MEDIUM",
      flag: adult > 60 ? "high" : adult > 20 ? "medium" : adult > 0 ? "low" : "low",
    },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Scores</CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <THead>
            <Tr>
              <Th>Indicateur</Th>
              <Th>Score</Th>
              <Th className="hidden md:table-cell">Seuils</Th>
              <Th>Statut</Th>
            </Tr>
          </THead>
          <TBody>
            {rows.map((r) => (
              <Tr key={r.label}>
                <Td className="font-medium text-slate-800">{r.label}</Td>
                <Td className="font-mono text-slate-900">{r.score}</Td>
                <Td className="hidden md:table-cell text-xs text-slate-500">
                  {r.threshold}
                </Td>
                <Td>
                  <Badge className={riskColor(r.flag)}>
                    {r.flag.toUpperCase()}
                  </Badge>
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function ScoreBar({ value }: { value: number }) {
  const pct = Math.max(
    0,
    Math.min(100, value <= 1 ? value * 100 : value)
  );
  const color =
    pct >= 75
      ? "bg-red-500"
      : pct >= 40
        ? "bg-amber-500"
        : "bg-emerald-500";
  const label =
    pct >= 75 ? "ÉLEVÉ" : pct >= 40 ? "MODÉRÉ" : "FAIBLE";
  return (
    <div className="flex items-center gap-2 min-w-[180px]">
      <div className="flex-1 h-2 bg-slate-200 rounded-full overflow-hidden">
        <div
          className={cn("h-full transition-all", color)}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="font-mono font-semibold text-sm tabular-nums text-slate-800">
        {Math.round(pct)}%
      </span>
      <Badge
        className={cn(
          "text-[10px]",
          pct >= 75 && "bg-red-100 text-red-700 border-red-200",
          pct >= 40 &&
            pct < 75 &&
            "bg-amber-100 text-amber-700 border-amber-200",
          pct < 40 && "bg-emerald-100 text-emerald-700 border-emerald-200"
        )}
      >
        {label}
      </Badge>
    </div>
  );
}

function HighlightedExtract({
  text,
  highlight,
  expanded,
  previewChars = 350,
}: {
  text: string;
  highlight?: string | null;
  expanded: boolean;
  previewChars?: number;
}) {
  // NFKC : convertit les Arabic Presentation Forms (FExx) en caracteres
  // arabes standard et reassemble les ligatures, sinon les PDFs marocains
  // s'affichent en caracteres "decomposes" illisibles.
  const cleanText = (text || "").normalize("NFKC").replace(/\s+/g, " ").trim();
  const cleanHighlight = (highlight || "").normalize("NFKC").replace(/\s+/g, " ").trim();

  if (!cleanText) return <span className="text-slate-400">non disponible</span>;

  const lowerText = cleanText.toLowerCase();
  const lowerHl = cleanHighlight.toLowerCase();
  const idx = cleanHighlight ? lowerText.indexOf(lowerHl) : -1;

  // No exact highlight found: just show text with optional truncation.
  if (idx < 0) {
    const display =
      expanded || cleanText.length <= previewChars
        ? cleanText
        : `${cleanText.slice(0, previewChars).trimEnd()}…`;
    return (
      <span dir="auto" style={{ unicodeBidi: "plaintext" }}>
        {display}
      </span>
    );
  }

  // Window selection for non-expanded view: centre on the highlight.
  let windowStart = 0;
  let windowEnd = cleanText.length;
  if (!expanded && cleanText.length > previewChars) {
    const padding = Math.max(
      40,
      Math.floor((previewChars - cleanHighlight.length) / 2)
    );
    windowStart = Math.max(0, idx - padding);
    windowEnd = Math.min(
      cleanText.length,
      idx + cleanHighlight.length + padding
    );
  }

  const before = cleanText.slice(windowStart, idx);
  const matched = cleanText.slice(idx, idx + cleanHighlight.length);
  const after = cleanText.slice(idx + cleanHighlight.length, windowEnd);

  return (
    <span dir="auto" style={{ unicodeBidi: "plaintext" }}>
      {windowStart > 0 && <span className="text-slate-400">… </span>}
      {before}
      <mark className="bg-amber-200 text-amber-900 px-0.5 rounded font-medium">
        {matched}
      </mark>
      {after}
      {windowEnd < cleanText.length && (
        <span className="text-slate-400"> …</span>
      )}
    </span>
  );
}

function formatMatchPosition(m: PlagiarismMatch): {
  current: string;
  source: string;
} {
  const current =
    m.current_page_number != null
      ? `page ${m.current_page_number}`
      : m.current_chunk_index != null
        ? `chunk ${m.current_chunk_index}`
        : m.chunk_index != null
          ? `chunk ${m.chunk_index}`
          : "—";
  const source =
    m.source_page_number != null
      ? `page ${m.source_page_number}`
      : m.source_chunk_index != null
        ? `chunk ${m.source_chunk_index}`
        : m.page_number != null
          ? `page ${m.page_number}`
          : "—";
  return { current, source };
}

function PlagiarismMatchCard({
  match,
  index,
}: {
  match: PlagiarismMatch;
  index: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const [compare, setCompare] = useState(false);

  const extract = String(
    match.matched_chunk_text_display ??
      match.matched_chunk_text ??
      match.snippet ??
      ""
  );
  const currentChunk = String(match.chunk_text ?? "");
  const overlap = match.overlap_text ?? match.snippet ?? null;
  const score = Number(
    match.similarity_score ?? match.similarity ?? match.score ?? 0
  );
  const { current, source } = formatMatchPosition(match);
  const isLong = extract.length > 400;

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 space-y-3 hover:border-slate-300 transition-colors">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="inline-flex items-center justify-center h-6 min-w-[1.75rem] rounded bg-slate-100 text-slate-700 font-mono text-xs font-semibold px-1.5">
            #{index + 1}
          </span>
          <Badge className="bg-slate-100 text-slate-700 border-slate-200 font-normal">
            votre {current} <span className="opacity-50 mx-1">↔</span> source {source}
          </Badge>
          {match.grouped_copies != null && match.grouped_copies > 1 && (
            <Badge className="bg-amber-100 text-amber-700 border-amber-200">
              ×{match.grouped_copies} copies similaires
            </Badge>
          )}
        </div>
        <ScoreBar value={score} />
      </div>

      {/* Highlighted extract */}
      <div className="rounded-md bg-slate-50 border border-slate-200 p-3 text-sm leading-relaxed text-slate-800">
        <HighlightedExtract
          text={extract}
          highlight={overlap}
          expanded={expanded}
        />
      </div>

      {/* Actions */}
      <div className="flex flex-wrap gap-3 text-xs">
        {isLong && (
          <button
            type="button"
            onClick={() => setExpanded((e) => !e)}
            className="text-ccm-red hover:text-ccm-red-dark font-medium"
          >
            {expanded ? "↑ Réduire l'extrait" : "↓ Voir le passage complet"}
          </button>
        )}
        {currentChunk && (
          <button
            type="button"
            onClick={() => setCompare((c) => !c)}
            className="text-slate-600 hover:text-slate-900 font-medium"
          >
            {compare ? "Masquer la comparaison" : "Comparer côte à côte"}
          </button>
        )}
      </div>

      {/* Side-by-side comparison */}
      {compare && currentChunk && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
          <div className="rounded-md border border-blue-200 bg-blue-50/40 p-4">
            <p className="text-[10px] uppercase tracking-wide font-semibold text-blue-700 mb-3 border-b border-blue-200 pb-2">
              Dans votre document — {current}
            </p>
            <BilingualBlock text={currentChunk} tone="blue" />
          </div>
          <div className="rounded-md border border-amber-200 bg-amber-50/40 p-4">
            <p className="text-[10px] uppercase tracking-wide font-semibold text-amber-800 mb-3 border-b border-amber-200 pb-2">
              Dans le document source — {source}
            </p>
            <BilingualBlock text={extract} tone="amber" />
          </div>
        </div>
      )}
    </div>
  );
}

function PlagiarismMatchesTable({
  matches,
  startIndex = 0,
}: {
  matches: PlagiarismMatch[];
  startIndex?: number;
}) {
  return (
    <div className="space-y-3">
      {matches.map((m, i) => (
        <PlagiarismMatchCard
          key={i}
          match={m}
          index={startIndex + i}
        />
      ))}
    </div>
  );
}

function PlagiarismSourceCard({
  source,
  index,
}: {
  source: PlagiarismSource;
  index: number;
}) {
  const matches = source.matches ?? [];
  const totalCount = source.matches_count ?? matches.length;
  const displayedCount = source.displayed_matches_count ?? matches.length;
  const bestScore = formatScore(source.best_score ?? 0, "%");
  const moreCount = Math.max(0, totalCount - displayedCount);

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <CardTitle className="text-base flex items-center gap-2 flex-wrap">
              <span className="font-mono text-slate-500">
                Source {index + 1}
              </span>
              <span className="text-slate-900 break-all">
                {fallback(source.original_filename)}
              </span>
            </CardTitle>
            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
              <span>
                Stocké :{" "}
                <span className="font-mono">
                  {fallback(source.stored_filename)}
                </span>
              </span>
              <span>
                Scénario :{" "}
                <span className="font-mono">
                  {fallback(source.source_scenario_id)}
                </span>
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge className={cn(riskColor("high"), "font-mono")}>
              Meilleur score : {bestScore}
            </Badge>
            <Badge className="bg-slate-100 text-slate-700 border-slate-200">
              {displayedCount} / {totalCount} passage(s)
            </Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <PlagiarismMatchesTable matches={matches} />
        {moreCount > 0 && (
          <p className="mt-2 text-xs text-slate-500 italic">
            +{moreCount} passage(s) supplémentaire(s) regroupé(s) sur ce
            document source.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function ExactDuplicatePanel({
  duplicateCount,
  duplicateAnalyses,
}: {
  duplicateCount: number;
  duplicateAnalyses: DuplicateAnalysis[];
}) {
  return (
    <div className="rounded-md border border-amber-200 bg-amber-50/70 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-amber-900">
            Doublon exact détecté
          </h3>
          <p className="mt-1 text-sm text-amber-800">
            Ce document a déjà été analysé auparavant.
          </p>
        </div>
        <Badge className="bg-amber-100 text-amber-800 border-amber-300">
          Copies identiques trouvées : {duplicateCount}
        </Badge>
      </div>

      {duplicateAnalyses.length > 0 && (
        <div className="mt-4">
          <h4 className="mb-2 text-sm font-semibold text-amber-950">
            Anciennes analyses
          </h4>
          <Table>
            <THead>
              <Tr>
                <Th>Scenario ID</Th>
                <Th>Fichier</Th>
                <Th>Date</Th>
              </Tr>
            </THead>
            <TBody>
              {duplicateAnalyses.map((item, index) => (
                <Tr key={`${item.scenario_id ?? item.stored_filename ?? index}`}>
                  <Td className="font-mono text-xs">
                    {fallback(item.scenario_id)}
                  </Td>
                  <Td>
                    {fallback(item.original_filename ?? item.stored_filename)}
                  </Td>
                  <Td className="text-xs text-slate-600">
                    {fallback(item.created_at)}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </div>
      )}
    </div>
  );
}

function PlagiarismSection({ plagiarism }: { plagiarism: Plagiarism }) {
  const [showAllSources, setShowAllSources] = useState(false);
  const [flatExpanded, setFlatExpanded] = useState(false);
  const sources = plagiarism.plagiarism_sources ?? [];
  const allMatches = plagiarism.matches ?? [];
  const totalMatches = plagiarism.total_matches ?? allMatches.length;
  const displayedMatches =
    plagiarism.displayed_matches ?? allMatches.length;
  const totalSources = plagiarism.total_sources ?? sources.length;
  const isTruncated = plagiarism.is_truncated ?? false;
  const exactDuplicate = Boolean(plagiarism.exact_duplicate);
  const duplicateAnalyses = Array.isArray(plagiarism.duplicate_analyses)
    ? plagiarism.duplicate_analyses
    : [];
  const duplicateCount =
    plagiarism.duplicate_count ?? duplicateAnalyses.length;

  // If grouped sources are not available (legacy backend), fall back to a
  // single flat table.
  const useGroupedView = sources.length > 0;

  const visibleSources = showAllSources ? sources : sources.slice(0, 3);
  const visibleFlat = flatExpanded ? allMatches : allMatches.slice(0, 15);

  if (allMatches.length === 0 && sources.length === 0 && !exactDuplicate) {
    return (
      <Card className="relative overflow-hidden border-slate-200">
        <div className="pointer-events-none absolute -right-16 -top-16 h-40 w-40 rounded-full bg-emerald-200/40 blur-3xl" />
        <CardHeader className="relative">
          <CardTitle className="flex items-center gap-2.5">
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-emerald-400 via-emerald-500 to-teal-600 text-white shadow-md ring-1 ring-emerald-200">
              <Search className="h-4 w-4" />
            </span>
            <span>
              Analyse plagiat
              <span className="ml-2 text-xs font-normal text-emerald-700">
                Aucun signal détecté
              </span>
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="relative">
          <Alert variant="success">
            Aucun passage similaire significatif n'a été détecté.
          </Alert>
        </CardContent>
        <div className="h-1 bg-gradient-to-r from-emerald-400 via-teal-500 to-emerald-500 opacity-70" />
      </Card>
    );
  }

  return (
    <Card className="relative overflow-hidden border-slate-200">
      {/* CCM identity glow */}
      <div className="pointer-events-none absolute -right-16 -top-16 h-44 w-44 rounded-full bg-ccm-red/10 blur-3xl" />
      <div className="pointer-events-none absolute -left-20 -bottom-20 h-40 w-40 rounded-full bg-ccm-gold/10 blur-3xl" />

      <CardHeader className="relative">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <CardTitle className="flex items-center gap-2.5">
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-ccm-red-light via-ccm-red to-ccm-red-dark text-white shadow-md shadow-ccm-red/30 ring-1 ring-ccm-gold/30">
              <Search className="h-4 w-4" />
            </span>
            <span>
              Analyse plagiat
              <span className="ml-2 text-xs font-normal text-slate-500">
                Recherche par similarité sémantique
              </span>
            </span>
          </CardTitle>
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-ccm-red/10 px-2.5 py-1 font-semibold text-ccm-red-dark ring-1 ring-ccm-red/30">
              <span className="font-mono tabular-nums">{totalMatches}</span>
              passage{totalMatches > 1 ? "s" : ""}
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-700 ring-1 ring-slate-200">
              <FileText className="h-3 w-3" />
              <span className="font-mono tabular-nums">{totalSources}</span>
              source{totalSources > 1 ? "s" : ""}
            </span>
            {isTruncated && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-100 px-2.5 py-1 font-semibold text-amber-800 ring-1 ring-amber-200">
                <AlertTriangle className="h-3 w-3" />
                Résultats tronqués
              </span>
            )}
          </div>
        </div>
        {isTruncated && (
          <p className="mt-2 text-xs italic text-slate-500">
            {displayedMatches} passages affichés sur {totalMatches} détectés.
            Les passages sont regroupés par document source et triés par score
            décroissant après déduplication.
          </p>
        )}
      </CardHeader>
      <CardContent className="relative space-y-5">
        {exactDuplicate && (
          <ExactDuplicatePanel
            duplicateCount={duplicateCount}
            duplicateAnalyses={duplicateAnalyses}
          />
        )}
        {allMatches.length > 0 || sources.length > 0 ? (
          <div className="flex items-center gap-2 border-l-4 border-ccm-red/40 pl-3">
            <h3 className="text-sm font-semibold text-ccm-ink">
              Passages similaires partiels
            </h3>
            <span className="text-xs text-slate-500">
              {sources.length > 0
                ? `regroupés sur ${sources.length} document${sources.length > 1 ? "s" : ""} source`
                : `${allMatches.length} passage${allMatches.length > 1 ? "s" : ""}`}
            </span>
          </div>
        ) : (
          <Alert variant="success">
            Aucun passage similaire partiel significatif n'a été détecté.
          </Alert>
        )}
        {(allMatches.length > 0 || sources.length > 0) && useGroupedView ? (
          <>
            <div className="space-y-3">
              {visibleSources.map((source, i) => (
                <PlagiarismSourceCard key={i} source={source} index={i} />
              ))}
            </div>
            {sources.length > 3 && (
              <Button
                variant="outline"
                onClick={() => setShowAllSources((v) => !v)}
              >
                {showAllSources
                  ? "Afficher moins de sources"
                  : `Afficher les ${sources.length - 3} autre(s) source(s)`}
              </Button>
            )}
          </>
        ) : (allMatches.length > 0 || sources.length > 0) ? (
          <>
            <PlagiarismMatchesTable matches={visibleFlat} />
            {allMatches.length > 15 && (
              <Button
                variant="outline"
                onClick={() => setFlatExpanded((v) => !v)}
              >
                {flatExpanded
                  ? "Afficher moins"
                  : `Voir plus (${allMatches.length - 15} de plus)`}
              </Button>
            )}
          </>
        ) : null}
      </CardContent>
      <div className="h-1 bg-gradient-to-r from-ccm-red via-ccm-red-light to-ccm-gold opacity-70" />
    </Card>
  );
}

function formatPage(page: number | string | null | undefined): string {
  if (page === null || page === undefined || page === "") return "—";
  const n = Number(page);
  return Number.isFinite(n) ? `Page ${n}` : `Page ${page}`;
}

function ModerationSection({ analysis }: { analysis: Analysis }) {
  const profanity = analysis.profanity ?? {};
  const adult = analysis.adult_content ?? {};
  const vulgMatches: VulgarityMatch[] = profanity.vulgarity_matches ?? [];
  const nudityMatches: NudityMatch[] = adult.nudity_matches ?? [];
  const grouped = useMemo(() => groupVulgarity(vulgMatches), [vulgMatches]);
  const profanityScore = Number(profanity.profanity_score ?? 0);
  const adultScore = Number(adult.adult_content_score ?? 0);
  const profPct = profanityScore <= 1 ? profanityScore * 100 : profanityScore;
  const adultPct = adultScore <= 1 ? adultScore * 100 : adultScore;

  // The headline visual of the moderation card adapts to the worst of the
  // two scores. A low/clean moderation reads as green, a borderline one as
  // amber, a flagged one as the CCM red. Without this the icon and halos
  // stayed red even on perfectly clean scenarios.
  const modPct = Math.max(profPct, adultPct);
  const modTone: "low" | "medium" | "high" =
    modPct >= 60 ? "high" : modPct >= 20 ? "medium" : "low";
  const modVisual = {
    low: {
      glowA: "bg-emerald-300/15",
      glowB: "bg-emerald-200/15",
      iconWrap:
        "bg-gradient-to-br from-emerald-400 via-emerald-500 to-emerald-600 shadow-emerald-500/30",
      ring: "ring-emerald-300/40",
    },
    medium: {
      glowA: "bg-amber-300/20",
      glowB: "bg-amber-200/15",
      iconWrap:
        "bg-gradient-to-br from-amber-400 via-amber-500 to-amber-600 shadow-amber-500/30",
      ring: "ring-amber-300/40",
    },
    high: {
      glowA: "bg-ccm-red/10",
      glowB: "bg-rose-300/15",
      iconWrap:
        "bg-gradient-to-br from-ccm-red-light via-ccm-red to-ccm-red-dark shadow-ccm-red/30",
      ring: "ring-ccm-gold/30",
    },
  }[modTone];

  return (
    <Card className="relative overflow-hidden border-slate-200">
      {/* Tone-adaptive ambient glows */}
      <div
        className={cn(
          "pointer-events-none absolute -right-16 -top-16 h-44 w-44 rounded-full blur-3xl transition-colors",
          modVisual.glowA
        )}
      />
      <div
        className={cn(
          "pointer-events-none absolute -left-20 -bottom-20 h-40 w-40 rounded-full blur-3xl transition-colors",
          modVisual.glowB
        )}
      />

      <CardHeader className="relative">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <CardTitle className="flex items-center gap-2.5">
            <span
              className={cn(
                "inline-flex h-9 w-9 items-center justify-center rounded-lg text-white shadow-md ring-1 transition-colors",
                modVisual.iconWrap,
                modVisual.ring
              )}
            >
              {modTone === "low" ? (
                <ShieldCheck className="h-4 w-4" />
              ) : (
                <ShieldAlert className="h-4 w-4" />
              )}
            </span>
            <span>
              Analyse modération
              <span className="ml-2 text-xs font-normal text-slate-500">
                Vulgarité &amp; contenu adulte · FR / AR / Darija
              </span>
            </span>
          </CardTitle>
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-semibold ring-1",
                profPct >= 60
                  ? "bg-rose-100 text-rose-700 ring-rose-200"
                  : profPct >= 20
                    ? "bg-amber-100 text-amber-700 ring-amber-200"
                    : "bg-emerald-100 text-emerald-700 ring-emerald-200",
              )}
            >
              <ShieldAlert className="h-3 w-3" />
              Vulgarité
              <span className="font-mono tabular-nums">
                {profPct.toFixed(0)}%
              </span>
            </span>
            <span
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-semibold ring-1",
                adultPct >= 60
                  ? "bg-rose-100 text-rose-700 ring-rose-200"
                  : adultPct >= 20
                    ? "bg-amber-100 text-amber-700 ring-amber-200"
                    : "bg-emerald-100 text-emerald-700 ring-emerald-200",
              )}
            >
              <AlertTriangle className="h-3 w-3" />
              Adulte
              <span className="font-mono tabular-nums">
                {adultPct.toFixed(0)}%
              </span>
            </span>
          </div>
        </div>
      </CardHeader>
      <CardContent className="relative space-y-6">
        {/* Vulgarité — Résumé groupé. The "Résumé des mots détectés"
            heading only makes sense when there *is* something to list;
            otherwise we just show the success notice on its own. */}
        <div>
          {grouped.length > 0 && (
            <div className="mb-2 flex items-center gap-2 border-l-4 border-ccm-red/40 pl-3">
              <h3 className="text-sm font-semibold text-ccm-ink">
                Résumé des mots détectés
              </h3>
              <span className="text-xs text-slate-500">
                {grouped.length} mot{grouped.length > 1 ? "s" : ""} unique
                {grouped.length > 1 ? "s" : ""}
              </span>
            </div>
          )}
          {grouped.length === 0 ? (
            <Alert variant="success">
              Aucune vulgarité significative n'a été détectée.
            </Alert>
          ) : (
            <Table>
              <THead>
                <Tr>
                  <Th>Mot</Th>
                  <Th className="w-24">Occurrences</Th>
                  <Th>Langue</Th>
                  <Th>Catégorie</Th>
                </Tr>
              </THead>
              <TBody>
                {grouped.map((g) => (
                  <Tr key={g.word}>
                    <Td className="font-mono text-red-700" dir="auto">
                      {g.word}
                    </Td>
                    <Td className="font-semibold">{g.count}</Td>
                    <Td>{g.language}</Td>
                    <Td>{g.category}</Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          )}
        </div>

        {/* Vulgarité — passages détaillés */}
        {vulgMatches.length > 0 && (
          <div>
            <div className="mb-2 flex items-center gap-2 border-l-4 border-ccm-red/40 pl-3">
              <h3 className="text-sm font-semibold text-ccm-ink">
                Passages contenant des mots vulgaires
              </h3>
              <span className="inline-flex items-center gap-1 rounded-full bg-ccm-red/10 px-2 py-0.5 text-[10px] font-semibold text-ccm-red-dark ring-1 ring-ccm-red/30">
                <span className="font-mono tabular-nums">{vulgMatches.length}</span>
                occurrence{vulgMatches.length > 1 ? "s" : ""}
              </span>
            </div>
            <Table>
              <THead>
                <Tr>
                  <Th className="w-10">#</Th>
                  <Th>Mot détecté</Th>
                  <Th className="w-20">Page</Th>
                  <Th>Langue</Th>
                  <Th>Catégorie</Th>
                  <Th>Passage</Th>
                </Tr>
              </THead>
              <TBody>
                {vulgMatches.map((m, i) => (
                  <Tr key={i}>
                    <Td className="font-mono text-slate-500">{i + 1}</Td>
                    <Td className="font-mono text-red-700" dir="auto">
                      {m.word}
                    </Td>
                    <Td className="font-mono text-xs text-slate-600">
                      {formatPage(m.page_number)}
                    </Td>
                    <Td>{translateLanguage(m.language)}</Td>
                    <Td>{translateCategory(m.category)}</Td>
                    <Td className="text-slate-700 italic text-xs" dir="auto">
                      {m.snippet || "(passage non disponible)"}
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </div>
        )}

        {/* Contenu adulte — passages détaillés avec page */}
        {nudityMatches.length > 0 && (
          <div>
            <div className="mb-2 flex items-center gap-2 border-l-4 border-rose-400/50 pl-3">
              <h3 className="text-sm font-semibold text-ccm-ink">
                Passages contenant du contenu adulte / nudité
              </h3>
              <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-2 py-0.5 text-[10px] font-semibold text-rose-700 ring-1 ring-rose-200">
                <span className="font-mono tabular-nums">{nudityMatches.length}</span>
                occurrence{nudityMatches.length > 1 ? "s" : ""}
              </span>
            </div>
            <Table>
              <THead>
                <Tr>
                  <Th className="w-10">#</Th>
                  <Th>Terme détecté</Th>
                  <Th className="w-20">Page</Th>
                  <Th>Langue</Th>
                  <Th>Catégorie</Th>
                  <Th>Passage</Th>
                </Tr>
              </THead>
              <TBody>
                {nudityMatches.map((m, i) => (
                  <Tr key={i}>
                    <Td className="font-mono text-slate-500">{i + 1}</Td>
                    <Td className="font-mono text-rose-700" dir="auto">
                      {m.word || m.term}
                    </Td>
                    <Td className="font-mono text-xs text-slate-600">
                      {formatPage(m.page_number)}
                    </Td>
                    <Td>{translateLanguage(m.language)}</Td>
                    <Td>{translateCategory(m.category)}</Td>
                    <Td className="text-slate-700 italic text-xs" dir="auto">
                      {m.snippet || "(passage non disponible)"}
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </div>
        )}
      </CardContent>
      <div
        className={cn(
          "h-1 bg-gradient-to-r opacity-70 transition-colors",
          modTone === "low" &&
            "from-emerald-400 via-teal-500 to-emerald-500",
          modTone === "medium" &&
            "from-amber-400 via-amber-500 to-orange-500",
          modTone === "high" && "from-ccm-red via-rose-500 to-ccm-gold"
        )}
      />
    </Card>
  );
}

// ---------- Formatted RAG report renderer ----------

type ParsedReport = {
  scenarioId: string | null;
  riskLevel: string | null;
  sections: { title: string; body: string }[];
};

const KNOWN_SECTION_TITLES = [
  "Résumé",
  "Statistiques du document",
  "Analyse plagiat",
  "Analyse modération",
  "Recommandations",
  "Conclusion",
];

const SECTION_ICONS: Record<string, typeof FileText> = {
  "Résumé": FileText,
  "Statistiques du document": BarChart3,
  "Analyse plagiat": Search,
  "Analyse modération": ShieldAlert,
  "Recommandations": ListChecks,
  "Conclusion": Target,
};

function parseGeneratedReport(text: string): ParsedReport {
  const lines = text.split("\n");
  let scenarioId: string | null = null;
  let riskLevel: string | null = null;
  const sections: { title: string; body: string }[] = [];
  let current: { title: string; body: string } | null = null;

  for (const rawLine of lines) {
    const line = rawLine.replace(/\s+$/, "");
    const trimmed = line.trim();

    if (trimmed.startsWith("Rapport d'analyse du scénario")) {
      scenarioId = trimmed.replace("Rapport d'analyse du scénario", "").trim();
      continue;
    }
    if (trimmed.startsWith("Niveau de risque")) {
      const parts = trimmed.split(":");
      if (parts.length > 1) riskLevel = parts.slice(1).join(":").trim();
      continue;
    }
    if (KNOWN_SECTION_TITLES.includes(trimmed)) {
      if (current) sections.push(current);
      current = { title: trimmed, body: "" };
      continue;
    }
    if (current) {
      current.body = current.body ? `${current.body}\n${line}` : line;
    }
  }
  if (current) sections.push(current);
  return { scenarioId, riskLevel, sections };
}

function ReportBulletList({ body }: { body: string }) {
  const items = body
    .split("\n")
    .map((l) => l.replace(/^\s*[-•]\s+/, "").trim())
    .filter(Boolean);
  if (items.length === 0)
    return <p className="text-sm text-slate-500 italic">(aucun élément)</p>;
  return (
    <ul className="space-y-1.5">
      {items.map((item, i) => (
        <li
          key={i}
          className="flex gap-2 text-sm text-slate-700 leading-relaxed"
        >
          <span className="text-ccm-red shrink-0 mt-0.5">•</span>
          <span dir="auto">{item}</span>
        </li>
      ))}
    </ul>
  );
}

function ReportNumberedList({ body }: { body: string }) {
  const items = body
    .split("\n")
    .map((l) => l.replace(/^\s*[-•]\s+/, "").trim())
    .filter(Boolean);
  if (items.length === 0)
    return <p className="text-sm text-slate-500 italic">(aucun élément)</p>;
  return (
    <ol className="space-y-2">
      {items.map((item, i) => (
        <li
          key={i}
          className="flex gap-3 text-sm text-slate-700 leading-relaxed"
        >
          <span className="inline-flex shrink-0 h-5 w-5 items-center justify-center rounded-full bg-ccm-red/10 text-ccm-red font-mono text-[11px] font-semibold mt-0.5">
            {i + 1}
          </span>
          <span dir="auto">{item}</span>
        </li>
      ))}
    </ol>
  );
}

function ReportKeyValueList({ body }: { body: string }) {
  const items = body
    .split("\n")
    .map((l) => l.replace(/^\s*[-•]\s+/, "").trim())
    .filter(Boolean);
  if (items.length === 0)
    return <p className="text-sm text-slate-500 italic">(aucun élément)</p>;
  return (
    <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-sm">
      {items.map((item, i) => {
        const idx = item.indexOf(":");
        if (idx < 0) {
          return (
            <div key={i} className="col-span-full text-slate-700" dir="auto">
              {item}
            </div>
          );
        }
        const label = item.slice(0, idx).trim();
        const value = item.slice(idx + 1).trim();
        return (
          <div key={i} className="flex flex-col gap-0.5">
            <dt className="text-[11px] uppercase tracking-wide text-slate-500">
              {label}
            </dt>
            <dd
              className="font-mono text-slate-900 text-sm break-all"
              dir="auto"
            >
              {value}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

function ReportPlagiarismBlock({ body }: { body: string }) {
  // Body shape: a headline paragraph, then optional "Match N" blocks
  // followed by indented "    label : value" lines.
  const lines = body.split("\n");
  const blocks: string[][] = [];
  let head: string[] = [];
  let current: string[] | null = null;

  for (const rawLine of lines) {
    const trimmed = rawLine.trim();
    if (/^Match\s+\d+/i.test(trimmed)) {
      if (current) blocks.push(current);
      current = [trimmed];
    } else if (current) {
      current.push(rawLine);
    } else {
      head.push(rawLine);
    }
  }
  if (current) blocks.push(current);

  const headText = head.join("\n").trim();

  return (
    <div className="space-y-4">
      {headText && (
        <p
          className="text-sm text-slate-700 leading-relaxed whitespace-pre-wrap"
          dir="auto"
        >
          {headText}
        </p>
      )}
      {blocks.length > 0 && (
        <div className="space-y-3">
          {blocks.map((block, i) => {
            const title = block[0].trim();
            const fields = block
              .slice(1)
              .map((l) => l.trim())
              .filter((l) => l && l.includes(":"));
            return (
              <div
                key={i}
                className="rounded-md border border-slate-200 bg-slate-50/60 p-3"
              >
                <p className="font-semibold text-slate-800 text-sm mb-2 flex items-center gap-2">
                  <span className="inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded bg-ccm-red/10 text-ccm-red font-mono text-[11px] px-1">
                    {i + 1}
                  </span>
                  {title}
                </p>
                <dl className="grid grid-cols-1 sm:grid-cols-[10rem_1fr] gap-x-3 gap-y-1.5 text-xs">
                  {fields.map((field, j) => {
                    const idx = field.indexOf(":");
                    const label = field.slice(0, idx).trim();
                    const value = field.slice(idx + 1).trim();
                    const isExtract = /^extrait$/i.test(label);
                    return (
                      <Fragment key={j}>
                        <dt className="text-slate-500 font-medium">{label}</dt>
                        <dd
                          className={cn(
                            "text-slate-800 break-words",
                            isExtract
                              ? "italic bg-amber-50 border border-amber-100 rounded p-2 text-slate-700"
                              : "font-mono"
                          )}
                          dir="auto"
                        >
                          {value || "—"}
                        </dd>
                      </Fragment>
                    );
                  })}
                </dl>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ReportParagraph({ body }: { body: string }) {
  return (
    <p
      className="text-sm text-slate-700 leading-relaxed whitespace-pre-wrap"
      dir="auto"
    >
      {body.trim()}
    </p>
  );
}

function ReportSectionBlock({ title, body }: { title: string; body: string }) {
  const Icon = SECTION_ICONS[title] ?? FileText;
  let content: React.ReactNode;
  switch (title) {
    case "Statistiques du document":
      content = <ReportKeyValueList body={body} />;
      break;
    case "Recommandations":
      content = <ReportNumberedList body={body} />;
      break;
    case "Analyse plagiat":
      content = <ReportPlagiarismBlock body={body} />;
      break;
    case "Analyse modération":
      content = (
        <p
          className="text-sm text-slate-700 leading-relaxed whitespace-pre-wrap"
          dir="auto"
        >
          {body.trim()}
        </p>
      );
      break;
    default:
      content = <ReportParagraph body={body} />;
  }
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-ccm-ink uppercase tracking-wide mb-3">
        <Icon className="h-4 w-4 text-ccm-red" />
        {title}
      </h3>
      {content}
    </section>
  );
}

function FormattedReport({ text }: { text: string }) {
  const parsed = useMemo(() => parseGeneratedReport(text), [text]);
  const [copied, setCopied] = useState(false);
  const [showRaw, setShowRaw] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  const download = () => {
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `rapport_${parsed.scenarioId ?? "scenario"}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <CardTitle className="flex items-center gap-2">
            <FileText className="h-5 w-5 text-ccm-red" />
            Rapport généré complet
          </CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" onClick={copy}>
              {copied ? (
                <ClipboardCheck className="h-4 w-4 text-emerald-600" />
              ) : (
                <ClipboardCopy className="h-4 w-4" />
              )}
              {copied ? "Copié !" : "Copier"}
            </Button>
            <Button variant="outline" onClick={download}>
              <Download className="h-4 w-4" />
              .txt
            </Button>
            <Button
              variant="ghost"
              onClick={() => setShowRaw((v) => !v)}
            >
              {showRaw ? "Vue formatée" : "Voir texte brut"}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {showRaw ? (
          <pre
            className="text-xs whitespace-pre-wrap bg-slate-50 border border-slate-200 p-4 rounded text-slate-700 leading-relaxed max-h-[600px] overflow-auto"
            dir="auto"
          >
            {text}
          </pre>
        ) : (
          <div className="space-y-4">
            {/* Header card with scenario id and risk badge */}
            <div className="rounded-lg ccm-gradient p-4 text-white">
              <p className="text-[11px] uppercase tracking-wide text-white/70">
                Scénario analysé
              </p>
              <p className="font-mono text-sm break-all">
                {parsed.scenarioId ?? "—"}
              </p>
              {parsed.riskLevel && (
                <div className="mt-3">
                  <p className="text-[11px] uppercase tracking-wide text-white/70">
                    Niveau de risque
                  </p>
                  <Badge
                    className={cn(
                      "mt-1 text-sm px-3 py-1 border-0",
                      parsed.riskLevel.toUpperCase() === "HIGH" &&
                        "bg-red-600 text-white",
                      parsed.riskLevel.toUpperCase() === "MEDIUM" &&
                        "bg-amber-500 text-white",
                      parsed.riskLevel.toUpperCase() === "LOW" &&
                        "bg-emerald-600 text-white"
                    )}
                  >
                    {parsed.riskLevel}
                  </Badge>
                </div>
              )}
            </div>

            {parsed.sections.map((section) => (
              <ReportSectionBlock
                key={section.title}
                title={section.title}
                body={section.body}
              />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------- Advanced RAG section (on-demand, additive) ----------

/**
 * Picks the most useful remediation hint based on the LLM error string.
 * The error is in French (produced by ``_extract_provider_error_message``
 * backend-side) so the matching is text-based.
 */
function RagErrorHint({ error }: { error: string }) {
  const lower = error.toLowerCase();

  // OpenAI / Anthropic billing & key issues
  if (lower.includes("quota") || lower.includes("rate limit")) {
    return (
      <p className="text-xs mt-2 text-slate-600">
        <strong>Quota OpenAI dépassé.</strong> Vérifie ton solde et tes
        limites sur{" "}
        <a
          className="underline text-ccm-red"
          href="https://platform.openai.com/account/billing/overview"
          target="_blank"
          rel="noopener noreferrer"
        >
          platform.openai.com/account/billing
        </a>
        . Recharge le compte (min. 5 $) ou augmente la limite mensuelle.
        Tu peux aussi basculer temporairement sur Ollama via{" "}
        <code>SIA_RAG_LLM_PROVIDER=ollama</code> dans <code>.env</code>.
      </p>
    );
  }
  if (
    lower.includes("clé api invalide") ||
    lower.includes("invalid api key") ||
    lower.includes("incorrect api key") ||
    lower.includes("permissions") ||
    lower.includes("accès refusé")
  ) {
    return (
      <p className="text-xs mt-2 text-slate-600">
        <strong>Clé API invalide ou rejetée.</strong> Vérifie{" "}
        <code>SIA_RAG_LLM_API_KEY</code> dans <code>.env</code> (sans
        espaces, sans guillemets) puis redémarre uvicorn. Génère une nouvelle
        clé si nécessaire sur{" "}
        <a
          className="underline text-ccm-red"
          href="https://platform.openai.com/api-keys"
          target="_blank"
          rel="noopener noreferrer"
        >
          platform.openai.com/api-keys
        </a>
        .
      </p>
    );
  }
  if (lower.includes("endpoint") || lower.includes("modèle introuvable")) {
    return (
      <p className="text-xs mt-2 text-slate-600">
        <strong>Modèle introuvable.</strong> Vérifie que{" "}
        <code>SIA_RAG_LLM_MODEL</code> correspond à un modèle accessible avec
        ta clé (ex. <code>gpt-4o-mini</code>, <code>gpt-4o</code>).
      </p>
    );
  }
  if (
    lower.includes("timeout") ||
    lower.includes("connexion au llm impossible")
  ) {
    return (
      <p className="text-xs mt-2 text-slate-600">
        <strong>Le LLM n'a pas répondu à temps.</strong> Si tu utilises
        Ollama, vérifie qu'il tourne (<code>ollama serve</code>) et que le
        modèle est warmé. Augmente{" "}
        <code>SIA_RAG_LLM_TIMEOUT_SECONDS</code> (≥ 600 s pour un 7B/8B sur
        CPU). Si tu utilises OpenAI, retente — il s'agit probablement d'un
        pic de latence côté API.
      </p>
    );
  }
  if (lower.includes("prompt trop volumineux")) {
    return (
      <p className="text-xs mt-2 text-slate-600">
        <strong>Prompt trop volumineux.</strong> Réduis{" "}
        <code>SIA_RAG_MAX_PASSAGES</code> (essaie 3) ou{" "}
        <code>SIA_RAG_LLM_MAX_TOKENS</code> dans <code>.env</code>.
      </p>
    );
  }
  // Generic fallback
  return (
    <p className="text-xs mt-2 text-slate-600">
      Vérifie la configuration LLM dans <code>.env</code> (provider, clé API,
      timeout) puis relance <code>uvicorn</code>. Le diagnostic complet est
      disponible via <code>python scripts/diagnose_rag.py</code>.
    </p>
  );
}

function AdvancedRAGSection({
  analysis,
  scenarioId,
}: {
  analysis: Analysis;
  scenarioId: string | null;
}) {
  const [report, setReport] = useState<AdvancedReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Smooth animated progress in [0..100]. Drives the visible bar. We
  // never let it reach 100 % until the response actually arrives, so the
  // user never sees a "full" bar while still waiting.
  const [progress, setProgress] = useState(0);

  const canGenerate = Boolean(scenarioId);

  // Expected duration in seconds for a local llama3.2:3b on CPU.
  // Measured ~97 s in our benchmark, leave headroom so the bar advances
  // smoothly until 90 % and then asymptotes toward 95 %.
  const EXPECTED_DURATION_S = 110;

  useEffect(() => {
    if (!loading) return;
    setProgress(0);
    const startedAt = performance.now();
    const id = window.setInterval(() => {
      const elapsedMs = performance.now() - startedAt;
      const ratio = elapsedMs / (EXPECTED_DURATION_S * 1000);
      // Up to 90 %: linear with expected duration.
      // Above 90 %: very slow asymptote toward 95 % (never reaches it).
      const next =
        ratio < 0.9
          ? ratio * 100
          : 90 + (1 - Math.exp(-(ratio - 0.9) * 1.5)) * 5;
      setProgress(Math.min(95, Math.max(0, next)));
    }, 200);
    return () => window.clearInterval(id);
  }, [loading]);

  const run = async () => {
    if (!scenarioId) return;
    setLoading(true);
    setError(null);
    try {
      const r = await generateAdvancedReport(scenarioId, analysis);
      // Snap to 100 % so the bar feels "complete" before disappearing.
      setProgress(100);
      // Tiny delay lets the eye catch the full bar before the panel swaps.
      await new Promise((res) => setTimeout(res, 250));
      setReport(r);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="h-5 w-5 text-ccm-red" />
              Rapport explicatif IA (RAG avancé)
            </CardTitle>
            <p className="text-sm text-slate-500 mt-1">
              Génère un rapport explicatif structuré à partir des passages
              similaires retrouvés, en s'appuyant sur un LLM si configuré
              (sinon fallback déterministe).
            </p>
          </div>
          {!report && (
            <Button
              onClick={run}
              disabled={!canGenerate || loading}
            >
              {loading ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Génération…
                </>
              ) : (
                <>
                  <Sparkles className="h-4 w-4" />
                  Générer le rapport explicatif
                </>
              )}
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {!canGenerate && (
          <Alert variant="warning">
            Scénario sans identifiant — impossible de générer le rapport
            explicatif.
          </Alert>
        )}

        {error && <Alert variant="error">{error}</Alert>}

        {loading && (
          <div className="rounded-md border border-ccm-red/20 bg-ccm-red/5 p-4 space-y-3">
            <div className="flex items-start gap-3">
              <Loader2 className="h-5 w-5 animate-spin shrink-0 mt-0.5 text-ccm-red" />
              <div className="flex-1 space-y-2">
                <div className="flex items-baseline justify-between gap-3">
                  <p className="font-medium text-ccm-ink">
                    Génération du rapport en cours…
                  </p>
                  <span className="text-xs font-mono text-ccm-red tabular-nums">
                    {Math.round(progress)} %
                  </span>
                </div>

                {/* Progress bar (estimated, smoothed) */}
                <div
                  className="h-2 w-full rounded-full bg-slate-200 overflow-hidden"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round(progress)}
                >
                  <div
                    className="h-full ccm-gradient transition-[width] duration-200 ease-linear"
                    style={{ width: `${progress}%` }}
                  />
                </div>

                <p className="text-xs text-slate-600">
                  Le modèle local (Ollama llama3.2:3b sur CPU) traite ton
                  rapport. Compte environ <strong>90 à 150 secondes</strong>
                  {" "}selon la taille du contexte. La barre est une estimation,
                  pas une mesure exacte — ne ferme pas l'onglet.
                </p>
              </div>
            </div>
          </div>
        )}

        {!report && !loading && !error && canGenerate && (
          <Alert variant="info">
            Cliquez sur « Générer le rapport explicatif » pour produire une
            analyse rédigée des passages similaires (synthèse, analyse
            passage par passage, conséquences éditoriales, actions
            recommandées, conclusion).
            <span className="block mt-1 text-xs text-slate-500">
              Avec un modèle local (Ollama), la première génération peut
              prendre 1 à 3 minutes selon le matériel.
            </span>
          </Alert>
        )}

        {report && (
          <div className="space-y-4">
            {/* Surface the upstream LLM error FIRST so the user understands
                immediately why the deterministic fallback was used. */}
            {report.llm.used_fallback && report.llm.error && (
              <Alert variant="warning">
                <p className="font-semibold mb-1">
                  L'appel au LLM a échoué — rapport produit via le fallback
                  déterministe.
                </p>
                <p className="text-xs">
                  <span className="font-mono">{report.llm.error}</span>
                </p>
                <RagErrorHint error={report.llm.error} />
              </Alert>
            )}

            <div className="flex flex-wrap items-center gap-2 text-xs">
              <Badge
                className={cn(
                  report.llm.used_fallback
                    ? "bg-amber-100 text-amber-800 border-amber-200"
                    : "bg-emerald-100 text-emerald-700 border-emerald-200"
                )}
              >
                {report.llm.used_fallback
                  ? report.llm.error
                    ? "⚠ Modèle : repli automatique (LLM en erreur)"
                    : "Modèle : repli déterministe"
                  : `Modèle : ${report.llm.provider} / ${report.llm.model}`}
              </Badge>
              <Badge
                className="bg-ccm-red/10 text-ccm-red border-ccm-red/20"
                title={report.context.retrieval_reason || undefined}
              >
                {report.context.passages.length} passage(s) utilisé(s) comme
                contexte
                {report.context.passages.length === 0 &&
                report.context.retrieval_status &&
                report.context.retrieval_status !== "ok"
                  ? ` · ${translateRetrievalStatus(report.context.retrieval_status)}`
                  : ""}
              </Badge>
              <Badge className="bg-slate-100 text-slate-600 border-slate-200">
                Risque : {formatRiskLabel(report.context.risk_level)}
              </Badge>
              <Badge className="bg-slate-100 text-slate-600 border-slate-200">
                Score : {Math.round(report.context.similarity_score_pct)}%
              </Badge>
              <span className="text-slate-400">
                généré le{" "}
                {new Date(report.generated_at).toLocaleString("fr-FR")}
              </span>
              <Button
                variant="ghost"
                onClick={run}
                disabled={loading}
              >
                {loading ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Sparkles className="h-3.5 w-3.5" />
                )}
                Régénérer
              </Button>
            </div>

            {report.context.passages.length === 0 &&
              report.context.retrieval_reason && (
                <div
                  className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900"
                  role="status"
                >
                  <span className="font-semibold">
                    Pourquoi 0 passage utilisé ?
                  </span>{" "}
                  {report.context.retrieval_reason}
                </div>
              )}

            <RAGNarrative narrative={report.narrative} />


            <details className="rounded-md border border-slate-200 bg-white p-3">
              <summary className="cursor-pointer text-xs text-slate-600 font-medium">
                Voir les {report.context.passages.length} passage(s) utilisés
                comme contexte RAG
              </summary>
              <div className="mt-3 space-y-2 text-xs">
                {report.context.passages.map((p) => (
                  <div
                    key={p.rank}
                    className="rounded border border-slate-200 p-2 space-y-1"
                  >
                    <p className="font-mono text-slate-600">
                      #{p.rank} · {Math.round(p.score_pct)}% ·{" "}
                      {p.source_filename} · {p.current_position} ↔{" "}
                      {p.source_position}
                      {p.grouped_copies > 1 && ` · ×${p.grouped_copies}`}
                    </p>
                    {p.overlap && (
                      <p className="italic text-slate-700" dir="auto">
                        « {p.overlap} »
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </details>

          </div>
        )}
      </CardContent>
    </Card>
  );
}

function RecommendationsSection({ items }: { items: string[] }) {
  if (items.length === 0) {
    return (
      <Card>
        <CardContent className="pt-6">
          <Alert variant="info">Aucune recommandation disponible.</Alert>
        </CardContent>
      </Card>
    );
  }
  return (
    <Card className="relative overflow-hidden border-slate-200">
      {/* Subtle CCM glow in the corner */}
      <div className="pointer-events-none absolute -right-16 -top-16 h-40 w-40 rounded-full bg-ccm-red/10 blur-3xl" />
      <CardHeader className="relative">
        <CardTitle className="flex items-center gap-2.5">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-ccm-red-light via-ccm-red to-ccm-red-dark text-white shadow-md shadow-ccm-red/30 ring-1 ring-ccm-gold/30">
            <ListChecks className="h-4 w-4" />
          </span>
          <span>
            Recommandations
            <span className="ml-2 text-xs font-normal text-slate-500">
              {items.length} action{items.length > 1 ? "s" : ""} à entreprendre
            </span>
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="relative">
        <ol className="space-y-2.5">
          {items.map((r, i) => (
            <li
              key={i}
              className="group relative flex gap-3 rounded-lg border border-slate-200 bg-white p-3 transition-all hover:border-ccm-red/30 hover:shadow-[0_8px_24px_-12px_rgba(193,39,45,0.35)]"
            >
              {/* Numbered gradient badge */}
              <span className="relative inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-ccm-red-light via-ccm-red to-ccm-red-dark font-mono text-xs font-bold text-white shadow-sm ring-1 ring-ccm-gold/25 transition-transform group-hover:scale-105">
                {i + 1}
              </span>
              <p className="pt-0.5 text-sm leading-relaxed text-slate-700">
                {r}
              </p>
              {/* Left accent bar that lights up on hover */}
              <span className="pointer-events-none absolute inset-y-2 left-0 w-0.5 rounded-r-full bg-gradient-to-b from-ccm-red to-ccm-gold opacity-0 transition-opacity group-hover:opacity-100" />
            </li>
          ))}
        </ol>
      </CardContent>
      {/* Bottom accent line */}
      <div className="h-1 bg-gradient-to-r from-ccm-red via-ccm-red-light to-ccm-gold opacity-70" />
    </Card>
  );
}

// ---------- Moroccan constants (ثوابت الدولة المغربية) ----------

const MOROCCAN_CATEGORY_META: Record<
  string,
  { label: string; arabic: string; icon: typeof FileText }
> = {
  islam: {
    label: "Islam modéré",
    arabic: "الإسلام المعتدل",
    icon: Moon,
  },
  national_unity: {
    label: "Unité nationale",
    arabic: "الوحدة الوطنية",
    icon: Flag,
  },
  monarchy: {
    label: "Monarchie constitutionnelle",
    arabic: "الملكية الدستورية",
    icon: Crown,
  },
  democratic_choice: {
    label: "Choix démocratique",
    arabic: "الاختيار الديمقراطي",
    icon: Vote,
  },
};

/** Normalise any flavour of risk label (English / French / accents / casing)
 *  to the four French buckets the spec uses. */
function normalizeMoroccanRisk(raw: string | undefined): {
  bucket: "faible" | "moyen" | "élevé" | "très élevé";
  label: string;
  tone: "success" | "warning" | "danger" | "critical";
  badgeClass: string;
  ringClass: string;
  bgClass: string;
  borderClass: string;
  headline: string;
  intro: string;
} {
  const k = String(raw || "").toLowerCase().trim();
  if (
    k === "tres_eleve" ||
    k === "tres eleve" ||
    k === "très élevé" ||
    k === "tres-eleve"
  ) {
    return {
      bucket: "très élevé",
      label: "Très élevé",
      tone: "critical",
      badgeClass: "bg-red-700 text-white border-red-800",
      ringClass: "ring-red-300",
      bgClass: "bg-red-50",
      borderClass: "border-red-300",
      headline: "Alerte prioritaire",
      intro:
        "Plusieurs passages constituent un risque majeur de non-conformité aux constantes nationales. Revue manuelle indispensable avant toute validation.",
    };
  }
  if (k === "high" || k === "élevé" || k === "eleve") {
    return {
      bucket: "élevé",
      label: "Élevé",
      tone: "danger",
      badgeClass: "bg-rose-600 text-white border-rose-700",
      ringClass: "ring-rose-200",
      bgClass: "bg-rose-50",
      borderClass: "border-rose-200",
      headline: "Passage sensible",
      intro:
        "Au moins un passage attaque explicitement une constante nationale. Examen manuel requis avant validation.",
    };
  }
  if (k === "medium" || k === "moyen") {
    return {
      bucket: "moyen",
      label: "Moyen",
      tone: "warning",
      badgeClass: "bg-amber-500 text-white border-amber-600",
      ringClass: "ring-amber-200",
      bgClass: "bg-amber-50",
      borderClass: "border-amber-200",
      headline: "À vérifier",
      intro:
        "Formulation ambiguë ou ironique touchant une constante nationale. Vérification humaine recommandée selon le contexte.",
    };
  }
  return {
    bucket: "faible",
    label: "Faible",
    tone: "success",
    badgeClass: "bg-emerald-600 text-white border-emerald-700",
    ringClass: "ring-emerald-200",
    bgClass: "bg-emerald-50",
    borderClass: "border-emerald-200",
    headline: "Conformité",
    intro:
      "Aucune atteinte évidente aux constantes nationales marocaines n'a été détectée.",
  };
}

function severityBadgeClass(severity: string): string {
  const k = String(severity || "").toLowerCase().trim();
  if (k === "tres_eleve" || k === "tres eleve" || k === "très élevé")
    return "bg-red-700 text-white border-red-800";
  if (k === "high" || k === "élevé" || k === "eleve")
    return "bg-rose-600 text-white border-rose-700";
  if (k === "medium" || k === "moyen")
    return "bg-amber-500 text-white border-amber-600";
  return "bg-emerald-600 text-white border-emerald-700";
}

function severityHumanLabel(severity: string): string {
  return normalizeMoroccanRisk(severity).label;
}

function MoroccanConstantsSection({
  data,
}: {
  data: MoroccanConstants | undefined;
}) {
  // Fallback if the field is absent (older backend or pipeline failure).
  if (!data) {
    return (
      <Card>
        <CardContent className="pt-6">
          <p className="text-sm text-slate-500">
            Le contrôle des constantes nationales n'a pas été exécuté pour
            cette analyse (champ <code>moroccan_constants</code> absent).
          </p>
        </CardContent>
      </Card>
    );
  }

  const flags = Array.isArray(data.flags) ? data.flags : [];
  const categories = data.categories ?? {};
  const score = Number(data.score ?? 0);
  const theme = normalizeMoroccanRisk(data.risk_level);
  const scorePct = (score <= 1 ? score * 100 : score).toFixed(0);

  // Sort flags worst → best so the operator sees the prioritised alerts
  // at the top of the list.
  const severityOrder: Record<string, number> = {
    "très élevé": 4,
    tres_eleve: 4,
    élevé: 3,
    high: 3,
    eleve: 3,
    moyen: 2,
    medium: 2,
    faible: 1,
    low: 1,
  };
  const orderedFlags = [...flags].sort((a, b) => {
    const sa = severityOrder[String(a.severity || "").toLowerCase()] ?? 0;
    const sb = severityOrder[String(b.severity || "").toLowerCase()] ?? 0;
    return sb - sa;
  });

  return (
    <Card
      className={cn(
        "relative overflow-hidden border-2",
        theme.borderClass,
      )}
    >
      <div
        className={cn(
          "absolute inset-x-0 top-0 h-1.5",
          theme.tone === "critical" && "bg-red-700",
          theme.tone === "danger" && "bg-rose-600",
          theme.tone === "warning" && "bg-amber-500",
          theme.tone === "success" && "bg-emerald-600",
        )}
      />
      <CardContent className={cn("relative space-y-6 pt-6", theme.bgClass)}>
        {/* ----- Headline ----- */}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-[1fr_auto] md:items-start">
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <Landmark className="h-6 w-6 text-ccm-ink" />
              <div>
                <p className="text-xl font-bold text-ccm-ink">
                  Constantes nationales — conformité
                </p>
                <p
                  className="text-sm text-slate-500"
                  dir="rtl"
                  lang="ar"
                >
                  ثوابت الدولة المغربية
                </p>
              </div>
            </div>
            <p className="text-sm text-slate-700">{theme.intro}</p>
          </div>

          {/* Score + risk badge */}
          <div
            className={cn(
              "flex flex-col items-center justify-center rounded-lg border bg-white/70 px-5 py-3 shadow-sm ring-1",
              theme.ringClass,
              theme.borderClass,
            )}
          >
            <span className="text-[10px] uppercase tracking-widest text-slate-500">
              Score de risque
            </span>
            <span className="font-mono text-3xl font-bold tabular-nums text-ccm-ink">
              {scorePct}
              <span className="ml-0.5 text-base text-slate-400">/ 100</span>
            </span>
            <Badge className={cn("mt-2", theme.badgeClass)}>
              {theme.headline}
            </Badge>
          </div>
        </div>

        {/* ----- Per-category breakdown ----- */}
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
            Catégories analysées
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {(
              [
                "islam",
                "national_unity",
                "monarchy",
                "democratic_choice",
              ] as MoroccanCategoryKey[]
            ).map((key) => {
              const meta = MOROCCAN_CATEGORY_META[key];
              const cat = categories[key];
              const count = cat?.count ?? 0;
              const catTheme = normalizeMoroccanRisk(cat?.risk_level);
              const Icon = meta.icon;
              return (
                <div
                  key={key}
                  className={cn(
                    "flex items-start gap-3 rounded-lg border bg-white p-3 shadow-sm",
                    count > 0 ? catTheme.borderClass : "border-slate-200",
                  )}
                >
                  <span
                    className={cn(
                      "inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md",
                      count > 0
                        ? catTheme.bgClass + " " + catTheme.borderClass
                        : "bg-slate-100",
                    )}
                  >
                    <Icon
                      className={cn(
                        "h-4 w-4",
                        count > 0 ? "text-ccm-ink" : "text-slate-400",
                      )}
                    />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-ccm-ink">
                      {meta.label}
                    </p>
                    <p
                      className="text-[10px] text-slate-500"
                      dir="rtl"
                      lang="ar"
                    >
                      {meta.arabic}
                    </p>
                    <div className="mt-1 flex items-center justify-between gap-1">
                      <span className="font-mono text-xs text-slate-500">
                        {count} passage{count > 1 ? "s" : ""}
                      </span>
                      {count > 0 ? (
                        <Badge className={catTheme.badgeClass}>
                          {catTheme.label}
                        </Badge>
                      ) : (
                        <Badge className="border-slate-200 bg-slate-100 text-slate-500">
                          RAS
                        </Badge>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* ----- Flagged passages ----- */}
        {orderedFlags.length === 0 ? (
          <Alert variant="success">
            Aucune atteinte évidente aux constantes nationales marocaines n'a
            été détectée.
          </Alert>
        ) : (
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
              Passages signalés ({orderedFlags.length})
            </p>
            <ul className="space-y-3">
              {orderedFlags.map((flag, index) => (
                <MoroccanFlagRow key={index} flag={flag} />
              ))}
            </ul>
          </div>
        )}

        <MoroccanMentionsPanel data={data} />

        <p className="text-[11px] italic text-slate-500">
          Cette détection est déterministe et signale les passages pour
          examen manuel. Elle ne constitue pas une décision de censure.
        </p>
      </CardContent>
    </Card>
  );
}

function MoroccanMentionsPanel({
  data,
}: {
  data: MoroccanConstants;
}) {
  const mentions = Array.isArray(data.mentions) ? data.mentions : [];
  const mentionsTotal = data.mentions_total ?? mentions.length;
  const mentionsTruncated = Boolean(data.mentions_truncated);
  const mentionsByCategory = data.mentions_by_category ?? {};

  // Operator opt-in by category — keeps the panel calm when a long PDF
  // has hundreds of "roi" / "Maroc" mentions.
  const [activeCategory, setActiveCategory] = useState<
    "all" | MoroccanCategoryKey | string
  >("all");
  const [expanded, setExpanded] = useState(false);

  if (mentionsTotal === 0) {
    return null;
  }

  const filtered =
    activeCategory === "all"
      ? mentions
      : mentions.filter((m) => m.category === activeCategory);

  const visible = expanded ? filtered : filtered.slice(0, 25);
  const remaining = filtered.length - visible.length;

  // Build per-category counts, falling back to mentions if backend skipped.
  const computedCounts: Record<string, number> = { ...mentionsByCategory };
  if (Object.keys(computedCounts).length === 0) {
    for (const m of mentions) {
      const k = String(m.category || "");
      computedCounts[k] = (computedCounts[k] ?? 0) + 1;
    }
  }

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Mentions à examiner
          </p>
          <p className="mt-0.5 text-sm text-slate-700">
            Toutes les occurrences des constantes nationales, neutres ou
            signalées. À l'utilisateur de trancher.
          </p>
        </div>
        <Badge className="border-slate-200 bg-slate-100 text-slate-700">
          {mentionsTotal.toLocaleString("fr-FR")} mention
          {mentionsTotal > 1 ? "s" : ""} au total
        </Badge>
      </div>

      {/* Category filter chips */}
      <div className="mt-3 flex flex-wrap gap-2 text-xs">
        <button
          type="button"
          onClick={() => {
            setActiveCategory("all");
            setExpanded(false);
          }}
          className={cn(
            "rounded-full border px-3 py-1 transition-colors",
            activeCategory === "all"
              ? "border-ccm-red bg-ccm-red/10 text-ccm-red"
              : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
          )}
        >
          Toutes ({mentionsTotal})
        </button>
        {(
          [
            "monarchy",
            "islam",
            "national_unity",
            "democratic_choice",
          ] as MoroccanCategoryKey[]
        ).map((key) => {
          const meta = MOROCCAN_CATEGORY_META[key];
          const count = computedCounts[key] ?? 0;
          if (count === 0) return null;
          return (
            <button
              key={key}
              type="button"
              onClick={() => {
                setActiveCategory(key);
                setExpanded(false);
              }}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 transition-colors",
                activeCategory === key
                  ? "border-ccm-red bg-ccm-red/10 text-ccm-red"
                  : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
              )}
            >
              <meta.icon className="h-3 w-3" />
              {meta.label} ({count})
            </button>
          );
        })}
      </div>

      {mentionsTruncated && (
        <p className="mt-3 text-[11px] italic text-amber-700">
          Liste plafonnée à {mentions.length} mentions pour limiter la
          taille du document — {mentionsTotal - mentions.length} mention
          {mentionsTotal - mentions.length > 1 ? "s" : ""}{" "}
          supplémentaire(s) non affichée(s).
        </p>
      )}

      {/* Mentions list */}
      <ul className="mt-3 divide-y divide-slate-100 rounded-md border border-slate-100">
        {visible.map((mention, index) => (
          <MoroccanMentionRow key={index} mention={mention} />
        ))}
        {visible.length === 0 && (
          <li className="px-3 py-4 text-center text-xs text-slate-500">
            Aucune mention dans cette catégorie.
          </li>
        )}
      </ul>

      {remaining > 0 && !expanded && (
        <Button
          variant="ghost"
          className="mt-2 w-full"
          onClick={() => setExpanded(true)}
        >
          Afficher les {remaining} mention{remaining > 1 ? "s" : ""}{" "}
          supplémentaire{remaining > 1 ? "s" : ""}
        </Button>
      )}
      {expanded && filtered.length > 25 && (
        <Button
          variant="ghost"
          className="mt-2 w-full"
          onClick={() => setExpanded(false)}
        >
          Réduire la liste
        </Button>
      )}
    </div>
  );
}

function MoroccanMentionRow({ mention }: { mention: MoroccanMention }) {
  const catKey = String(mention.category || "") as MoroccanCategoryKey;
  const meta = MOROCCAN_CATEGORY_META[catKey] ?? {
    label: mention.category || "Catégorie inconnue",
    arabic: "",
    icon: FileText,
  };
  const Icon = meta.icon;
  const isFlagged = mention.flagged_severity != null;
  return (
    <li className="flex items-start gap-3 px-3 py-2.5 hover:bg-slate-50">
      <span
        className={cn(
          "inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md",
          isFlagged ? "bg-rose-100 text-rose-700" : "bg-slate-100 text-slate-500",
        )}
      >
        <Icon className="h-3.5 w-3.5" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="font-semibold text-ccm-ink">{meta.label}</span>
          {mention.subject && (
            <span className="font-mono text-[11px] text-slate-500">
              « {mention.subject} »
            </span>
          )}
          {mention.chunk_index !== null &&
            mention.chunk_index !== undefined && (
              <Badge className="border-slate-200 bg-slate-100 text-slate-600 text-[10px]">
                Segment #{mention.chunk_index}
              </Badge>
            )}
          {isFlagged ? (
            <Badge
              className={cn(
                "text-[10px]",
                severityBadgeClass(String(mention.flagged_severity)),
              )}
            >
              Signalée — {severityHumanLabel(String(mention.flagged_severity))}
            </Badge>
          ) : (
            <Badge className="border-emerald-200 bg-emerald-50 text-emerald-700 text-[10px]">
              Neutre
            </Badge>
          )}
        </div>
        {mention.evidence && (
          <p
            className="mt-1 text-xs text-slate-600 italic"
            dir="auto"
          >
            « {mention.evidence} »
          </p>
        )}
      </div>
    </li>
  );
}

function MoroccanFlagRow({ flag }: { flag: MoroccanFlag }) {
  const catKey = String(flag.category || "") as MoroccanCategoryKey;
  const meta = MOROCCAN_CATEGORY_META[catKey] ?? {
    label: flag.category || "Catégorie inconnue",
    arabic: "",
    icon: AlertTriangle,
  };
  const Icon = meta.icon;
  const sevTheme = normalizeMoroccanRisk(flag.severity);
  return (
    <li
      className={cn(
        "rounded-lg border bg-white p-3 shadow-sm",
        sevTheme.borderClass,
      )}
    >
      <div className="flex flex-wrap items-start gap-2">
        <span
          className={cn(
            "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md",
            sevTheme.bgClass,
          )}
        >
          <Icon className="h-4 w-4 text-ccm-ink" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-ccm-ink">
              {meta.label}
            </span>
            {meta.arabic && (
              <span
                className="text-[11px] text-slate-500"
                dir="rtl"
                lang="ar"
              >
                {meta.arabic}
              </span>
            )}
            <Badge className={severityBadgeClass(flag.severity)}>
              {severityHumanLabel(flag.severity)}
            </Badge>
            {flag.chunk_index !== null && flag.chunk_index !== undefined && (
              <Badge className="border-slate-200 bg-slate-100 text-slate-600">
                Chunk #{flag.chunk_index}
              </Badge>
            )}
          </div>
          {flag.evidence && (
            <blockquote
              className="mt-2 border-l-2 border-slate-300 pl-3 text-sm italic text-slate-700"
              dir="auto"
            >
              {flag.evidence}
            </blockquote>
          )}
          {flag.explanation && (
            <p className="mt-2 text-xs text-slate-600">{flag.explanation}</p>
          )}
        </div>
      </div>
    </li>
  );
}

function SectionHeader({
  id,
  icon: Icon,
  title,
  subtitle,
}: {
  id: string;
  icon: typeof FileText;
  title: string;
  subtitle?: string;
}) {
  return (
    <div
      id={id}
      className="scroll-mt-24 flex items-center gap-3 border-b border-slate-200 pb-2 pt-2"
    >
      <div className="flex h-9 w-9 items-center justify-center rounded-md bg-ccm-red/10 text-ccm-red">
        <Icon className="h-5 w-5" />
      </div>
      <div>
        <h2 className="text-lg font-semibold text-ccm-ink">{title}</h2>
        {subtitle && (
          <p className="text-xs text-slate-500">{subtitle}</p>
        )}
      </div>
    </div>
  );
}

const TOC_ITEMS: { id: string; label: string }[] = [
  { id: "synthese", label: "Synthèse" },
  { id: "plagiat", label: "Plagiat" },
  { id: "moderation", label: "Modération" },
  { id: "constantes-maroc", label: "Constantes Maroc" },
  { id: "rapport-ia", label: "Rapport IA" },
  { id: "recommandations", label: "Recommandations" },
  { id: "conclusion", label: "Conclusion" },
];

function TableOfContents({
  hiddenIds = [],
}: {
  hiddenIds?: string[];
}) {
  const items = TOC_ITEMS.filter((item) => !hiddenIds.includes(item.id));
  return (
    <nav
      aria-label="Sommaire du rapport"
      className="sticky top-2 z-10 -mx-1 mb-2 overflow-x-auto rounded-md border border-slate-200 bg-white/95 px-2 py-1.5 backdrop-blur"
    >
      <ul className="flex items-center gap-1 text-xs">
        {items.map((item) => (
          <li key={item.id}>
            <a
              href={`#${item.id}`}
              className="inline-block whitespace-nowrap rounded px-2 py-1 text-slate-600 hover:bg-slate-100 hover:text-ccm-ink"
            >
              {item.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function ConclusionSection({ text }: { text?: string }) {
  if (!text) return null;
  return (
    <Card className="relative overflow-hidden border-slate-200">
      {/* Soft CCM background tint */}
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-ccm-red/[0.04] via-transparent to-ccm-gold/[0.04]" />
      <div className="pointer-events-none absolute -left-16 -bottom-16 h-40 w-40 rounded-full bg-ccm-red/10 blur-3xl" />

      <CardHeader className="relative">
        <CardTitle className="flex items-center gap-2.5">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-ccm-ink to-ccm-red-dark text-ccm-gold shadow-md ring-1 ring-ccm-gold/30">
            <Info className="h-4 w-4" />
          </span>
          <span>
            Conclusion
            <span className="ml-2 text-xs font-normal text-slate-500">
              Verdict éditorial
            </span>
          </span>
        </CardTitle>
      </CardHeader>

      <CardContent className="relative">
        {/* Quote-styled body with a red border accent */}
        <blockquote className="relative rounded-lg border-l-4 border-ccm-red bg-white/70 py-3 pl-4 pr-3 backdrop-blur">
          <span
            aria-hidden
            className="absolute -left-0.5 -top-1 select-none font-serif text-4xl leading-none text-ccm-red/30"
          >
            “
          </span>
          <p className="text-sm leading-relaxed text-slate-700">{text}</p>
        </blockquote>
      </CardContent>

      {/* Bottom accent line */}
      <div className="h-1 bg-gradient-to-r from-ccm-gold via-ccm-red-light to-ccm-red opacity-70" />
    </Card>
  );
}

// ---------- Page ----------

export function ResultsPage() {
  const analysis = useAnalysisStore((s) => s.analysis);
  const scenarioId = useAnalysisStore((s) => s.scenarioId);
  const navigate = useNavigate();

  if (!analysis) {
    return (
      <div className="space-y-4">
        <h1 className="text-3xl font-bold text-ccm-ink">
          Résultats de l'analyse
        </h1>
        <Alert variant="info">
          Aucun résultat disponible. Lancez d'abord une analyse PDF.
        </Alert>
        <Button onClick={() => navigate("/")}>Retour à l'accueil</Button>
      </div>
    );
  }

  const plagiarism = analysis.plagiarism ?? {};
  const matches: PlagiarismMatch[] = Array.isArray(plagiarism.matches)
    ? plagiarism.matches
    : [];
  const rag = analysis.rag_report ?? {};
  const recommendations = Array.isArray(rag.recommendations)
    ? rag.recommendations
    : [];

  // Hide the "Recommandations et conclusion" section when it carries no
  // actionable content — i.e. only the boilerplate "no significant risk"
  // line plus the traceability instruction. In that case the box is
  // pure noise.
  const isTrivialDecisionSection = (() => {
    const meaningful = recommendations
      .map((r) => String(r).toLowerCase())
      .filter(
        (r) =>
          !r.includes("aucune action corrective") &&
          !r.includes("conserver une trace"),
      );
    const conclusionLower = String(rag.conclusion ?? "").toLowerCase();
    const conclusionTrivial =
      !conclusionLower.trim() ||
      conclusionLower.includes("ne présente pas de risque significatif");
    return meaningful.length === 0 && conclusionTrivial;
  })();

  const handleDownload = () => {
    downloadPdfReport({
      ...analysis,
      scenario_id: analysis.scenario_id ?? scenarioId ?? undefined,
    });
  };

  return (
    <div className="space-y-6">
      <HeaderSection
        analysis={analysis}
        scenarioId={scenarioId}
        onDownload={handleDownload}
      />

      <StrictMatchBanner match={analysis.strict_match} />

      {/* ---------- 1. Synthèse ---------- */}
      <section id="synthese" className="space-y-4 scroll-mt-4">
        <StatusCards analysis={analysis} />
        <SummarySection analysis={analysis} />
      </section>

      {/* ---------- 3. Plagiat ---------- */}
      <section id="plagiat" className="scroll-mt-4">
        <PlagiarismSection plagiarism={plagiarism} />
      </section>

      {/* ---------- 4. Modération ---------- */}
      <section id="moderation" className="scroll-mt-4">
        <ModerationSection analysis={analysis} />
      </section>

      {/* ---------- 5. Constantes nationales marocaines ---------- */}
      <section id="constantes-maroc" className="scroll-mt-4">
        <MoroccanConstantsSection data={analysis.moroccan_constants} />
      </section>

      {/* ---------- 6. Rapport généré par l'IA ---------- */}
      <section id="rapport-ia" className="scroll-mt-4 space-y-4">
        <AdvancedRAGSection
          analysis={analysis}
          scenarioId={scenarioId ?? analysis.scenario_id ?? null}
        />
        {rag.generated_report && (
          <Card className="relative overflow-hidden border-slate-200">
            <div className="pointer-events-none absolute -right-12 -top-12 h-32 w-32 rounded-full bg-ccm-red/10 blur-3xl" />
            <CardContent className="relative flex flex-col items-start gap-3 pt-6 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-center gap-3">
                <span className="inline-flex h-10 w-10 items-center justify-center rounded-lg bg-gradient-to-br from-ccm-ink to-ccm-red-dark text-ccm-gold ring-1 ring-ccm-gold/30">
                  <FileText className="h-5 w-5" />
                </span>
                <div>
                  <p className="text-sm font-semibold text-ccm-ink">
                    Rapport déterministe complet
                  </p>
                  <p className="text-xs text-slate-500">
                    Document PDF structuré, prêt à archiver ou à partager.
                  </p>
                </div>
              </div>
              <Button
                onClick={handleDownload}
                className="bg-gradient-to-br from-ccm-red-light via-ccm-red to-ccm-red-dark text-white shadow-md shadow-ccm-red/30 hover:from-ccm-red hover:to-ccm-red-dark"
              >
                <Download className="h-4 w-4" />
                Télécharger la version PDF
              </Button>
            </CardContent>
            <div className="h-1 bg-gradient-to-r from-ccm-red via-ccm-red-light to-ccm-gold opacity-70" />
          </Card>
        )}
      </section>

      {/* ---------- 7. Recommandations + 8. Conclusion ----------
           Two distinct sections placed AFTER the RAG report so the
           reader first absorbs the editorial analysis, then sees the
           actionable list and the final verdict. Hidden when the
           content is just the "no significant risk" boilerplate. */}
      {!isTrivialDecisionSection && (
        <>
          <section id="recommandations" className="scroll-mt-4">
            <RecommendationsSection items={recommendations} />
          </section>

          {rag.conclusion && (
            <section id="conclusion" className="scroll-mt-4">
              <ConclusionSection text={rag.conclusion} />
            </section>
          )}
        </>
      )}
    </div>
  );
}
