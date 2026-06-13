import jsPDF from "jspdf";
import autoTable from "jspdf-autotable";
import type { Analysis } from "./types";

// ---------------------------------------------------------------------------
// Brand tokens — kept close to the app's Tailwind palette so the report
// feels like an extension of the UI rather than a separate artifact.
// ---------------------------------------------------------------------------

const COLORS = {
  ccmRed: [193, 39, 45] as [number, number, number],
  ink: [17, 24, 39] as [number, number, number],
  slate900: [15, 23, 42] as [number, number, number],
  slate700: [51, 65, 85] as [number, number, number],
  slate500: [100, 116, 139] as [number, number, number],
  slate400: [148, 163, 184] as [number, number, number],
  slate300: [203, 213, 225] as [number, number, number],
  slate200: [226, 232, 240] as [number, number, number],
  slate100: [241, 245, 249] as [number, number, number],
  slate50: [248, 250, 252] as [number, number, number],
  redDark: [185, 28, 28] as [number, number, number],
  red: [239, 68, 68] as [number, number, number],
  redLight: [254, 226, 226] as [number, number, number],
  amber: [245, 158, 11] as [number, number, number],
  amberLight: [254, 243, 199] as [number, number, number],
  emerald: [16, 185, 129] as [number, number, number],
  emeraldLight: [220, 252, 231] as [number, number, number],
};

const PAGE = {
  width: 595.28, // A4 width in pt
  height: 841.89,
  marginX: 40,
  marginTop: 80, // leave room for the header band
  marginBottom: 60,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function pick<T>(obj: Record<string, T> | undefined, ...keys: string[]): T | undefined {
  if (!obj) return undefined;
  for (const k of keys) {
    if (obj[k] !== undefined && obj[k] !== null) return obj[k];
  }
  return undefined;
}

function toNumber(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function toPercent(value: unknown): number {
  const n = toNumber(value);
  const scaled = n <= 1 ? n * 100 : n;
  return Math.max(0, Math.min(100, Math.round(scaled)));
}

function riskFromScore(pct: number): "low" | "medium" | "high" | "very_high" {
  if (pct >= 75) return "very_high";
  if (pct >= 55) return "high";
  if (pct >= 30) return "medium";
  return "low";
}

function formatRiskFr(value: unknown): string {
  const k = String(value || "").toLowerCase().trim();
  if (
    k === "very_high" ||
    k === "veryhigh" ||
    k === "tres_eleve" ||
    k === "tres eleve" ||
    k === "très élevé"
  )
    return "TRÈS ÉLEVÉ";
  if (k === "high" || k === "eleve" || k === "élevé") return "ÉLEVÉ";
  if (k === "medium" || k === "moyen") return "MOYEN";
  if (k === "low" || k === "faible") return "FAIBLE";
  return "INCONNU";
}

function riskColor(level: string): {
  bg: [number, number, number];
  fg: [number, number, number];
  bar: [number, number, number];
} {
  const k = level.toLowerCase();
  if (k === "very_high" || k === "tres_eleve" || k === "très élevé")
    return { bg: [254, 202, 202], fg: COLORS.redDark, bar: COLORS.redDark };
  if (k === "high" || k === "eleve" || k === "élevé")
    return { bg: COLORS.redLight, fg: COLORS.redDark, bar: COLORS.red };
  if (k === "medium" || k === "moyen")
    return { bg: COLORS.amberLight, fg: [146, 64, 14], bar: COLORS.amber };
  if (k === "low" || k === "faible")
    return { bg: COLORS.emeraldLight, fg: [4, 120, 87], bar: COLORS.emerald };
  return { bg: COLORS.slate100, fg: COLORS.slate700, bar: COLORS.slate400 };
}

function formatDateFr(value: unknown): string {
  if (!value) return "—";
  const text = String(value);
  const d = new Date(text);
  if (Number.isNaN(d.getTime())) return text;
  return d.toLocaleString("fr-FR", {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// Load the CCM logo from /public and return a data URL ready for jsPDF.
async function loadLogoDataUrl(path: string): Promise<string | null> {
  try {
    const response = await fetch(path, { cache: "force-cache" });
    if (!response.ok) return null;
    const blob = await response.blob();
    return await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onloadend = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(blob);
    });
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Layout primitives drawn natively in jsPDF
// ---------------------------------------------------------------------------

interface Ctx {
  doc: jsPDF;
  cursorY: number;
  logo: string | null;
  pageNumber: number;
  scenarioName: string;
}

function newPage(ctx: Ctx, options: { withHeader?: boolean } = {}): void {
  ctx.doc.addPage();
  ctx.pageNumber += 1;
  ctx.cursorY = PAGE.marginTop;
  if (options.withHeader !== false) drawHeader(ctx);
  drawFooter(ctx);
}

function ensureSpace(ctx: Ctx, needed: number): void {
  if (ctx.cursorY + needed > PAGE.height - PAGE.marginBottom) {
    newPage(ctx);
  }
}

function drawHeader(ctx: Ctx): void {
  const { doc } = ctx;
  // Top accent band
  doc.setFillColor(...COLORS.ccmRed);
  doc.rect(0, 0, PAGE.width, 4, "F");

  // Logo + lockup text
  if (ctx.logo) {
    try {
      doc.addImage(ctx.logo, "PNG", PAGE.marginX, 18, 32, 32);
    } catch {
      /* swallow — logo unavailable, header still works */
    }
  }
  doc.setFont("helvetica", "bold");
  doc.setFontSize(11);
  doc.setTextColor(...COLORS.ink);
  doc.text("SIA / CCM", PAGE.marginX + 42, 32);
  doc.setFont("helvetica", "normal");
  doc.setFontSize(8);
  doc.setTextColor(...COLORS.slate500);
  doc.text(
    "Centre Cinématographique Marocain",
    PAGE.marginX + 42,
    44
  );

  // Right-aligned scenario name
  if (ctx.scenarioName) {
    doc.setFont("helvetica", "normal");
    doc.setFontSize(9);
    doc.setTextColor(...COLORS.slate500);
    const truncated =
      ctx.scenarioName.length > 60
        ? `${ctx.scenarioName.slice(0, 57)}…`
        : ctx.scenarioName;
    doc.text(truncated, PAGE.width - PAGE.marginX, 36, { align: "right" });
  }

  // Bottom rule
  doc.setDrawColor(...COLORS.slate200);
  doc.setLineWidth(0.5);
  doc.line(PAGE.marginX, 60, PAGE.width - PAGE.marginX, 60);
}

function drawFooter(ctx: Ctx): void {
  const { doc, pageNumber } = ctx;
  const y = PAGE.height - 30;
  doc.setDrawColor(...COLORS.slate200);
  doc.setLineWidth(0.5);
  doc.line(PAGE.marginX, y - 12, PAGE.width - PAGE.marginX, y - 12);
  doc.setFont("helvetica", "normal");
  doc.setFontSize(8);
  doc.setTextColor(...COLORS.slate400);
  doc.text(
    "SIA · Plateforme d'analyse de scénarios · Centre Cinématographique Marocain",
    PAGE.marginX,
    y
  );
  doc.text(`Page ${pageNumber}`, PAGE.width - PAGE.marginX, y, {
    align: "right",
  });
}

function sectionTitle(ctx: Ctx, title: string): void {
  ensureSpace(ctx, 32);
  const { doc } = ctx;
  // Coloured accent square
  doc.setFillColor(...COLORS.ccmRed);
  doc.rect(PAGE.marginX, ctx.cursorY - 9, 3, 13, "F");
  doc.setFont("helvetica", "bold");
  doc.setFontSize(13);
  doc.setTextColor(...COLORS.ink);
  doc.text(title, PAGE.marginX + 10, ctx.cursorY);
  ctx.cursorY += 16;
}

function paragraph(ctx: Ctx, text: string, opts: { italic?: boolean } = {}): void {
  const { doc } = ctx;
  doc.setFont("helvetica", opts.italic ? "italic" : "normal");
  doc.setFontSize(10);
  doc.setTextColor(...COLORS.slate700);
  const lines = doc.splitTextToSize(text, PAGE.width - PAGE.marginX * 2);
  for (const line of lines) {
    ensureSpace(ctx, 14);
    doc.text(line, PAGE.marginX, ctx.cursorY);
    ctx.cursorY += 13;
  }
  ctx.cursorY += 4;
}

function riskBadge(
  ctx: Ctx,
  level: string,
  x: number,
  y: number
): { width: number; height: number } {
  const label = formatRiskFr(level);
  const colors = riskColor(level);
  const { doc } = ctx;
  doc.setFont("helvetica", "bold");
  doc.setFontSize(8);
  const textWidth = doc.getTextWidth(label);
  const width = textWidth + 14;
  const height = 16;
  doc.setFillColor(...colors.bg);
  doc.roundedRect(x, y, width, height, 8, 8, "F");
  doc.setTextColor(...colors.fg);
  doc.text(label, x + width / 2, y + 11, { align: "center" });
  return { width, height };
}

function scoreBar(
  ctx: Ctx,
  pct: number,
  x: number,
  y: number,
  width: number,
  height = 6
): void {
  const { doc } = ctx;
  const clamped = Math.max(0, Math.min(100, pct));
  const color = riskColor(riskFromScore(clamped)).bar;
  // Track
  doc.setFillColor(...COLORS.slate100);
  doc.roundedRect(x, y, width, height, height / 2, height / 2, "F");
  // Fill
  if (clamped > 0) {
    doc.setFillColor(...color);
    doc.roundedRect(
      x,
      y,
      (width * clamped) / 100,
      height,
      height / 2,
      height / 2,
      "F"
    );
  }
}

function metricCard(
  ctx: Ctx,
  opts: {
    x: number;
    y: number;
    width: number;
    label: string;
    value: string;
    accent: [number, number, number];
  }
): number {
  const { doc } = ctx;
  const height = 52;
  // Card background
  doc.setFillColor(255, 255, 255);
  doc.setDrawColor(...COLORS.slate200);
  doc.setLineWidth(0.5);
  doc.roundedRect(opts.x, opts.y, opts.width, height, 4, 4, "FD");
  // Left accent stripe
  doc.setFillColor(...opts.accent);
  doc.rect(opts.x, opts.y, 3, height, "F");
  // Label
  doc.setFont("helvetica", "normal");
  doc.setFontSize(8);
  doc.setTextColor(...COLORS.slate500);
  doc.text(opts.label.toUpperCase(), opts.x + 12, opts.y + 16);
  // Value
  doc.setFont("helvetica", "bold");
  doc.setFontSize(20);
  doc.setTextColor(...COLORS.ink);
  doc.text(opts.value, opts.x + 12, opts.y + 40);
  return height;
}

// ---------------------------------------------------------------------------
// Sections
// ---------------------------------------------------------------------------

function drawCoverPage(ctx: Ctx, analysis: Analysis): void {
  const { doc } = ctx;
  // Coloured top half
  doc.setFillColor(...COLORS.ccmRed);
  doc.rect(0, 0, PAGE.width, 280, "F");

  // Logo centered
  if (ctx.logo) {
    try {
      doc.addImage(ctx.logo, "PNG", PAGE.width / 2 - 36, 60, 72, 72);
    } catch {
      /* ignore */
    }
  }

  // Title
  doc.setFont("helvetica", "bold");
  doc.setFontSize(11);
  doc.setTextColor(255, 255, 255);
  doc.text("SIA / CCM", PAGE.width / 2, 160, { align: "center" });
  doc.setFont("helvetica", "normal");
  doc.setFontSize(9);
  doc.text(
    "CENTRE CINÉMATOGRAPHIQUE MAROCAIN",
    PAGE.width / 2,
    176,
    { align: "center" }
  );

  doc.setFont("helvetica", "bold");
  doc.setFontSize(26);
  doc.text("Rapport d'analyse", PAGE.width / 2, 222, { align: "center" });
  doc.setFont("helvetica", "normal");
  doc.setFontSize(14);
  doc.text("de scénario", PAGE.width / 2, 244, { align: "center" });

  // White area with metadata + risk highlight
  const docStats = (analysis.document_stats ?? {}) as Record<string, unknown>;
  const filename =
    String(
      pick(docStats, "original_filename", "file_name") ??
        analysis.scenario_id ??
        "Scénario sans nom"
    );

  // Filename card
  doc.setFont("helvetica", "normal");
  doc.setFontSize(9);
  doc.setTextColor(...COLORS.slate500);
  doc.text("DOCUMENT ANALYSÉ", PAGE.width / 2, 320, { align: "center" });

  doc.setFont("helvetica", "bold");
  doc.setFontSize(16);
  doc.setTextColor(...COLORS.ink);
  const filenameLines = doc.splitTextToSize(filename, PAGE.width - 120);
  let y = 340;
  for (const line of filenameLines.slice(0, 2)) {
    doc.text(line, PAGE.width / 2, y, { align: "center" });
    y += 20;
  }

  // Headline risk badge
  const rag = analysis.rag_report ?? {};
  const risk = String(rag.risk_level ?? "unknown");
  const colors = riskColor(risk);
  const label = formatRiskFr(risk);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(11);
  const labelWidth = doc.getTextWidth(label) + 60;
  const badgeX = (PAGE.width - labelWidth) / 2;
  doc.setFillColor(...colors.bg);
  doc.roundedRect(badgeX, y + 30, labelWidth, 38, 19, 19, "F");
  doc.setTextColor(...colors.fg);
  doc.text("RISQUE  ·  ", badgeX + 24, y + 54);
  const prefixWidth = doc.getTextWidth("RISQUE  ·  ");
  doc.text(label, badgeX + 24 + prefixWidth, y + 54);

  // Date + scenario id at the bottom
  doc.setFont("helvetica", "normal");
  doc.setFontSize(9);
  doc.setTextColor(...COLORS.slate500);
  doc.text(
    `Analyse effectuée le ${formatDateFr(analysis.analysis_timestamp)}`,
    PAGE.width / 2,
    PAGE.height - 90,
    { align: "center" }
  );
  if (analysis.scenario_id) {
    doc.text(
      `Identifiant scénario : ${analysis.scenario_id}`,
      PAGE.width / 2,
      PAGE.height - 76,
      { align: "center" }
    );
  }

  // Bottom accent
  doc.setFillColor(...COLORS.ccmRed);
  doc.rect(0, PAGE.height - 8, PAGE.width, 8, "F");
}

function drawSummary(ctx: Ctx, analysis: Analysis): void {
  const docStats = (analysis.document_stats ?? {}) as Record<string, unknown>;
  const plagiarism = (analysis.plagiarism ?? {}) as Record<string, unknown>;
  const profanity = (analysis.profanity ?? {}) as Record<string, unknown>;
  const adult = (analysis.adult_content ?? {}) as Record<string, unknown>;
  const rag = (analysis.rag_report ?? {}) as Record<string, unknown>;

  sectionTitle(ctx, "Synthèse");
  const conclusion = String(
    rag.conclusion ?? rag.summary ?? "Aucune synthèse disponible."
  );
  paragraph(ctx, conclusion);

  ctx.cursorY += 6;

  const simPct = toPercent(
    pick(plagiarism, "global_similarity_score", "score") ?? 0
  );
  const profPct = toPercent(pick(profanity, "profanity_score") ?? 0);
  const adultPct = toPercent(pick(adult, "adult_content_score") ?? 0);
  const words = toNumber(pick(docStats, "words_count", "word_count"));

  const cardW = (PAGE.width - PAGE.marginX * 2 - 12 * 3) / 4;
  const startX = PAGE.marginX;
  const y = ctx.cursorY;
  ensureSpace(ctx, 64);
  metricCard(ctx, {
    x: startX,
    y,
    width: cardW,
    label: "Similarité",
    value: `${simPct}%`,
    accent: riskColor(riskFromScore(simPct)).bar,
  });
  metricCard(ctx, {
    x: startX + cardW + 12,
    y,
    width: cardW,
    label: "Vulgarité",
    value: `${profPct}%`,
    accent: riskColor(riskFromScore(profPct)).bar,
  });
  metricCard(ctx, {
    x: startX + (cardW + 12) * 2,
    y,
    width: cardW,
    label: "Contenu adulte",
    value: `${adultPct}%`,
    accent: riskColor(riskFromScore(adultPct)).bar,
  });
  metricCard(ctx, {
    x: startX + (cardW + 12) * 3,
    y,
    width: cardW,
    label: "Mots",
    value: words.toLocaleString("fr-FR"),
    accent: COLORS.slate400,
  });
  ctx.cursorY += 64;
}

function drawPlagiarism(ctx: Ctx, analysis: Analysis): void {
  const plagiarism = (analysis.plagiarism ?? {}) as Record<string, unknown>;
  const { doc } = ctx;
  const matches = Array.isArray(plagiarism.matches)
    ? (plagiarism.matches as Array<Record<string, unknown>>)
    : [];
  const simPct = toPercent(
    pick(plagiarism, "global_similarity_score", "score") ?? 0
  );

  sectionTitle(ctx, "Détection de plagiat");

  // Headline row with score bar + badge
  ensureSpace(ctx, 32);
  doc.setFont("helvetica", "normal");
  doc.setFontSize(9);
  doc.setTextColor(...COLORS.slate500);
  doc.text("Similarité globale", PAGE.marginX, ctx.cursorY);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(16);
  doc.setTextColor(...COLORS.ink);
  doc.text(`${simPct}%`, PAGE.width - PAGE.marginX, ctx.cursorY, {
    align: "right",
  });
  ctx.cursorY += 8;
  scoreBar(
    ctx,
    simPct,
    PAGE.marginX,
    ctx.cursorY,
    PAGE.width - PAGE.marginX * 2,
    8
  );
  ctx.cursorY += 22;

  const exactDup = Boolean(plagiarism.exact_duplicate || plagiarism.duplicate);
  const totalMatches = toNumber(plagiarism.total_matches ?? matches.length);
  const sources = Array.isArray(plagiarism.plagiarism_sources ?? plagiarism.sources)
    ? ((plagiarism.plagiarism_sources ?? plagiarism.sources) as Array<
        Record<string, unknown>
      >)
    : [];

  autoTable(doc, {
    startY: ctx.cursorY,
    head: [],
    body: [
      ["Doublon exact", exactDup ? "Confirmé" : "Non détecté"],
      ["Passages similaires", String(totalMatches)],
      ["Sources distinctes", String(sources.length)],
    ],
    theme: "grid",
    styles: { fontSize: 9, cellPadding: 6, textColor: COLORS.slate700 },
    columnStyles: {
      0: { fillColor: COLORS.slate50, cellWidth: 160, fontStyle: "bold" },
      1: { cellWidth: "auto" },
    },
    margin: { left: PAGE.marginX, right: PAGE.marginX },
  });
  ctx.cursorY = ((doc as unknown as {
    lastAutoTable: { finalY: number };
  }).lastAutoTable.finalY ?? ctx.cursorY) + 16;

  // Per-source breakdown
  if (sources.length > 0) {
    sectionTitle(ctx, "Sources de plagiat");
    sources.slice(0, 5).forEach((source, idx) => {
      ensureSpace(ctx, 70);
      const name = String(
        source.original_filename ??
          source.stored_filename ??
          source.source_scenario_id ??
          `Source ${idx + 1}`
      );
      const best = toPercent(
        pick(source, "best_score_percent", "best_score") ?? 0
      );
      const matchesCount = toNumber(
        pick(source, "matches_count", "displayed_matches_count") ?? 0
      );
      const sourceRisk = riskFromScore(best);

      const cardY = ctx.cursorY;
      doc.setFillColor(255, 255, 255);
      doc.setDrawColor(...COLORS.slate200);
      doc.setLineWidth(0.5);
      doc.roundedRect(
        PAGE.marginX,
        cardY,
        PAGE.width - PAGE.marginX * 2,
        56,
        4,
        4,
        "FD"
      );
      // Risk-coloured side accent
      doc.setFillColor(...riskColor(sourceRisk).bar);
      doc.rect(PAGE.marginX, cardY, 3, 56, "F");

      // Filename
      doc.setFont("helvetica", "bold");
      doc.setFontSize(10);
      doc.setTextColor(...COLORS.ink);
      const truncatedName =
        name.length > 75 ? `${name.slice(0, 72)}…` : name;
      doc.text(truncatedName, PAGE.marginX + 12, cardY + 18);

      // Subtitle
      doc.setFont("helvetica", "normal");
      doc.setFontSize(8);
      doc.setTextColor(...COLORS.slate500);
      doc.text(
        `${matchesCount} passage${matchesCount > 1 ? "s" : ""} similaire${matchesCount > 1 ? "s" : ""}`,
        PAGE.marginX + 12,
        cardY + 32
      );

      // Score + bar
      const barX = PAGE.marginX + 12;
      const barW = PAGE.width - PAGE.marginX * 2 - 24 - 90;
      scoreBar(ctx, best, barX, cardY + 42, barW, 5);

      // Score and badge on the right
      doc.setFont("helvetica", "bold");
      doc.setFontSize(13);
      doc.setTextColor(...COLORS.ink);
      doc.text(
        `${best}%`,
        PAGE.width - PAGE.marginX - 12,
        cardY + 22,
        { align: "right" }
      );
      riskBadge(
        ctx,
        sourceRisk,
        PAGE.width - PAGE.marginX - 80,
        cardY + 34
      );

      ctx.cursorY += 64;
    });
  }
}

function drawModeration(ctx: Ctx, analysis: Analysis): void {
  const profanity = (analysis.profanity ?? {}) as Record<string, unknown>;
  const adult = (analysis.adult_content ?? {}) as Record<string, unknown>;
  const { doc } = ctx;

  sectionTitle(ctx, "Modération de contenu");

  const profWords = Array.isArray(profanity.detected_words)
    ? (profanity.detected_words as string[])
    : [];
  const adultTerms = Array.isArray(adult.detected_terms)
    ? (adult.detected_terms as string[])
    : [];
  const profPct = toPercent(pick(profanity, "profanity_score") ?? 0);
  const adultPct = toPercent(pick(adult, "adult_content_score") ?? 0);

  // Two side-by-side moderation cards
  ensureSpace(ctx, 130);
  const cardW = (PAGE.width - PAGE.marginX * 2 - 12) / 2;

  const drawModCard = (
    title: string,
    pct: number,
    detected: string[],
    x: number
  ) => {
    const cardY = ctx.cursorY;
    const color = riskColor(riskFromScore(pct)).bar;
    doc.setFillColor(255, 255, 255);
    doc.setDrawColor(...COLORS.slate200);
    doc.setLineWidth(0.5);
    doc.roundedRect(x, cardY, cardW, 120, 4, 4, "FD");
    doc.setFillColor(...color);
    doc.rect(x, cardY, 3, 120, "F");

    doc.setFont("helvetica", "bold");
    doc.setFontSize(10);
    doc.setTextColor(...COLORS.ink);
    doc.text(title.toUpperCase(), x + 12, cardY + 18);

    doc.setFont("helvetica", "bold");
    doc.setFontSize(26);
    doc.setTextColor(...COLORS.ink);
    doc.text(`${pct}%`, x + 12, cardY + 50);

    scoreBar(ctx, pct, x + 12, cardY + 58, cardW - 24, 5);

    doc.setFont("helvetica", "normal");
    doc.setFontSize(8);
    doc.setTextColor(...COLORS.slate500);
    doc.text(
      detected.length === 0
        ? "Aucun terme détecté."
        : `${detected.length} terme${detected.length > 1 ? "s" : ""} détecté${detected.length > 1 ? "s" : ""}`,
      x + 12,
      cardY + 78
    );

    if (detected.length > 0) {
      const preview = detected.slice(0, 4).join(", ");
      const lines = doc.splitTextToSize(preview, cardW - 24);
      doc.setTextColor(...COLORS.slate700);
      doc.text(lines.slice(0, 2), x + 12, cardY + 92);
    }
  };

  drawModCard("Vulgarité", profPct, profWords, PAGE.marginX);
  drawModCard(
    "Contenu adulte",
    adultPct,
    adultTerms,
    PAGE.marginX + cardW + 12
  );
  ctx.cursorY += 132;
}

function drawMoroccanConstants(ctx: Ctx, analysis: Analysis): void {
  const mc = (
    (analysis as unknown as Record<string, unknown>).moroccan_constants ??
    {}
  ) as Record<string, unknown>;
  if (!mc || Object.keys(mc).length === 0) return;

  const riskLevel = String(mc.risk_level ?? "low");
  const findings = Array.isArray(mc.findings)
    ? (mc.findings as Array<Record<string, unknown>>)
    : [];

  sectionTitle(ctx, "Constantes nationales du Maroc");

  ensureSpace(ctx, 32);
  const { doc } = ctx;
  doc.setFont("helvetica", "normal");
  doc.setFontSize(9);
  doc.setTextColor(...COLORS.slate500);
  doc.text("Niveau de conformité", PAGE.marginX, ctx.cursorY);
  riskBadge(ctx, riskLevel, PAGE.marginX + 130, ctx.cursorY - 12);
  ctx.cursorY += 16;

  if (findings.length === 0) {
    paragraph(ctx, "Aucun signal de non-conformité détecté.", { italic: true });
    return;
  }

  findings.slice(0, 10).forEach((finding, i) => {
    ensureSpace(ctx, 40);
    const label = String(
      finding.label ?? finding.title ?? `Constante ${i + 1}`
    );
    const severity = String(
      finding.severity ?? finding.risk_level ?? "low"
    );
    const detail = String(finding.detail ?? finding.message ?? "");

    doc.setFont("helvetica", "bold");
    doc.setFontSize(10);
    doc.setTextColor(...COLORS.ink);
    doc.text(`• ${label}`, PAGE.marginX, ctx.cursorY);
    riskBadge(
      ctx,
      severity,
      PAGE.width - PAGE.marginX - 80,
      ctx.cursorY - 11
    );
    ctx.cursorY += 14;
    if (detail) paragraph(ctx, detail);
  });
}

function drawNarrative(ctx: Ctx, analysis: Analysis): void {
  const rag = (analysis.rag_report ?? {}) as Record<string, unknown>;
  if (!rag) return;

  sectionTitle(ctx, "Rapport d'analyse détaillé");

  const sections: Array<[string, unknown]> = [
    ["Synthèse", rag.summary],
    ["Explication du plagiat", rag.plagiarism_explanation],
    ["Explication de la modération", rag.moderation_explanation],
    ["Conclusion", rag.conclusion],
  ];
  for (const [title, text] of sections) {
    const content = String(text ?? "").trim();
    if (!content) continue;
    ensureSpace(ctx, 24);
    ctx.doc.setFont("helvetica", "bold");
    ctx.doc.setFontSize(10);
    ctx.doc.setTextColor(...COLORS.ink);
    ctx.doc.text(title, PAGE.marginX, ctx.cursorY);
    ctx.cursorY += 14;
    paragraph(ctx, content);
  }

  const recs = Array.isArray(rag.recommendations)
    ? (rag.recommendations as string[])
    : [];
  if (recs.length > 0) {
    ensureSpace(ctx, 20);
    ctx.doc.setFont("helvetica", "bold");
    ctx.doc.setFontSize(10);
    ctx.doc.setTextColor(...COLORS.ink);
    ctx.doc.text("Recommandations", PAGE.marginX, ctx.cursorY);
    ctx.cursorY += 14;
    recs.forEach((rec) => paragraph(ctx, `• ${rec}`));
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export async function generatePdfReport(analysis: Analysis): Promise<jsPDF> {
  const doc = new jsPDF({ unit: "pt", format: "a4" });
  const logo = await loadLogoDataUrl("/ccm-logo-mark.png");
  const docStats = (analysis.document_stats ?? {}) as Record<string, unknown>;
  const scenarioName = String(
    pick(docStats, "original_filename", "file_name") ??
      analysis.scenario_id ??
      "Scénario"
  );
  const ctx: Ctx = { doc, cursorY: PAGE.marginTop, logo, pageNumber: 1, scenarioName };

  drawCoverPage(ctx, analysis);

  // Body — first content page
  newPage(ctx);

  drawSummary(ctx, analysis);
  ctx.cursorY += 8;
  drawPlagiarism(ctx, analysis);
  ctx.cursorY += 8;
  drawModeration(ctx, analysis);
  ctx.cursorY += 8;
  drawMoroccanConstants(ctx, analysis);
  ctx.cursorY += 8;
  drawNarrative(ctx, analysis);

  return doc;
}

export async function downloadPdfReport(analysis: Analysis): Promise<void> {
  const doc = await generatePdfReport(analysis);
  const id = analysis.scenario_id || "scenario";
  doc.save(`rapport_${id}.pdf`);
}
