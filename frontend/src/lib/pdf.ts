import jsPDF from "jspdf";
import autoTable from "jspdf-autotable";
import type { Analysis } from "./types";

// ---------------------------------------------------------------------------
// Brand tokens — Tailwind-aligned palette so the PDF feels like an extension
// of the web UI rather than a separate artifact.
// ---------------------------------------------------------------------------

const COLORS = {
  ccmRed: [193, 39, 45] as [number, number, number],
  ccmRedDark: [142, 27, 34] as [number, number, number],
  ccmGold: [212, 175, 55] as [number, number, number],
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
  width: 595.28, // A4 in points
  height: 841.89,
  marginX: 48,
  marginTop: 80, // header band space
  marginBottom: 56,
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
// Drawing context + primitives
// ---------------------------------------------------------------------------

interface Ctx {
  doc: jsPDF;
  cursorY: number;
  logo: string | null;
  pageNumber: number;
  scenarioName: string;
  sectionLabel: string;
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

  // Brand lockup
  if (ctx.logo) {
    try {
      doc.addImage(ctx.logo, "PNG", PAGE.marginX, 22, 28, 28);
    } catch {
      /* logo unavailable */
    }
  }
  doc.setFont("helvetica", "bold");
  doc.setFontSize(10);
  doc.setTextColor(...COLORS.ink);
  doc.text("SIA · CCM", PAGE.marginX + 36, 34);
  doc.setFont("helvetica", "normal");
  doc.setFontSize(7.5);
  doc.setTextColor(...COLORS.slate500);
  doc.text("Rapport d'analyse de scénario", PAGE.marginX + 36, 45);

  // Right-aligned current section label
  if (ctx.sectionLabel) {
    doc.setFont("helvetica", "bold");
    doc.setFontSize(8);
    doc.setTextColor(...COLORS.ccmRed);
    doc.text(
      ctx.sectionLabel.toUpperCase(),
      PAGE.width - PAGE.marginX,
      34,
      { align: "right" }
    );
  }
  if (ctx.scenarioName) {
    doc.setFont("helvetica", "normal");
    doc.setFontSize(8);
    doc.setTextColor(...COLORS.slate500);
    const truncated =
      ctx.scenarioName.length > 56
        ? `${ctx.scenarioName.slice(0, 53)}…`
        : ctx.scenarioName;
    doc.text(truncated, PAGE.width - PAGE.marginX, 45, { align: "right" });
  }

  // Bottom rule
  doc.setDrawColor(...COLORS.slate200);
  doc.setLineWidth(0.5);
  doc.line(PAGE.marginX, 64, PAGE.width - PAGE.marginX, 64);
}

function drawFooter(ctx: Ctx): void {
  const { doc, pageNumber } = ctx;
  const y = PAGE.height - 32;
  doc.setDrawColor(...COLORS.slate200);
  doc.setLineWidth(0.5);
  doc.line(PAGE.marginX, y - 12, PAGE.width - PAGE.marginX, y - 12);
  doc.setFont("helvetica", "normal");
  doc.setFontSize(8);
  doc.setTextColor(...COLORS.slate400);
  doc.text(
    "SIA — Plateforme d'analyse de scénarios · Centre Cinématographique Marocain",
    PAGE.marginX,
    y
  );
  doc.text(`Page ${pageNumber}`, PAGE.width - PAGE.marginX, y, {
    align: "right",
  });
}

function sectionHeader(
  ctx: Ctx,
  _number: string,
  title: string,
  _subtitle?: string
): void {
  ensureSpace(ctx, 28);
  const { doc } = ctx;
  doc.setFont("helvetica", "bold");
  doc.setFontSize(16);
  doc.setTextColor(...COLORS.ink);
  doc.text(title, PAGE.marginX, ctx.cursorY);
  ctx.cursorY += 5;
  doc.setDrawColor(...COLORS.ccmRed);
  doc.setLineWidth(2);
  doc.line(PAGE.marginX, ctx.cursorY, PAGE.marginX + 28, ctx.cursorY);
  ctx.cursorY += 10;
}

function subTitle(ctx: Ctx, text: string): void {
  ensureSpace(ctx, 18);
  const { doc } = ctx;
  doc.setFont("helvetica", "bold");
  doc.setFontSize(10.5);
  doc.setTextColor(...COLORS.slate900);
  doc.text(text, PAGE.marginX, ctx.cursorY);
  ctx.cursorY += 11;
}

function paragraph(ctx: Ctx, text: string, opts: { italic?: boolean } = {}): void {
  const { doc } = ctx;
  doc.setFont("helvetica", opts.italic ? "italic" : "normal");
  doc.setFontSize(9.5);
  doc.setTextColor(...COLORS.slate700);
  const lines = doc.splitTextToSize(text, PAGE.width - PAGE.marginX * 2);
  for (const line of lines) {
    ensureSpace(ctx, 12);
    doc.text(line, PAGE.marginX, ctx.cursorY);
    ctx.cursorY += 12;
  }
  ctx.cursorY += 3;
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
  const width = textWidth + 16;
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
  doc.setFillColor(...COLORS.slate100);
  doc.roundedRect(x, y, width, height, height / 2, height / 2, "F");
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
  const height = 48;
  doc.setFillColor(255, 255, 255);
  doc.setDrawColor(...COLORS.slate200);
  doc.setLineWidth(0.5);
  doc.roundedRect(opts.x, opts.y, opts.width, height, 6, 6, "FD");
  doc.setFillColor(...opts.accent);
  doc.rect(opts.x, opts.y, 3, height, "F");
  doc.setFont("helvetica", "normal");
  doc.setFontSize(7.5);
  doc.setTextColor(...COLORS.slate500);
  doc.text(opts.label.toUpperCase(), opts.x + 10, opts.y + 16);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(17);
  doc.setTextColor(...COLORS.ink);
  doc.text(opts.value, opts.x + 10, opts.y + 38);
  return height;
}

// ---------------------------------------------------------------------------
// Cover page
// ---------------------------------------------------------------------------

function drawCoverPage(ctx: Ctx, analysis: Analysis): void {
  const { doc } = ctx;

  // Full-page gradient effect via two rects
  doc.setFillColor(...COLORS.ccmRedDark);
  doc.rect(0, 0, PAGE.width, PAGE.height, "F");
  doc.setFillColor(...COLORS.ccmRed);
  doc.rect(0, 0, PAGE.width, 380, "F");

  // Gold accent line
  doc.setFillColor(...COLORS.ccmGold);
  doc.rect(0, 378, PAGE.width, 2, "F");

  // Logo
  if (ctx.logo) {
    try {
      doc.addImage(ctx.logo, "PNG", PAGE.width / 2 - 36, 110, 72, 72);
    } catch {
      /* ignore */
    }
  }

  // Brand
  doc.setFont("helvetica", "bold");
  doc.setFontSize(11);
  doc.setTextColor(255, 255, 255);
  doc.text("SIA  ·  CCM", PAGE.width / 2, 210, { align: "center" });
  doc.setFont("helvetica", "normal");
  doc.setFontSize(8.5);
  doc.setTextColor(255, 220, 180);
  doc.text(
    "CENTRE CINÉMATOGRAPHIQUE MAROCAIN",
    PAGE.width / 2,
    226,
    { align: "center" }
  );

  // Main title
  doc.setFont("helvetica", "bold");
  doc.setFontSize(30);
  doc.setTextColor(255, 255, 255);
  doc.text("Rapport d'analyse", PAGE.width / 2, 290, { align: "center" });
  doc.setFont("helvetica", "normal");
  doc.setFontSize(14);
  doc.setTextColor(255, 220, 180);
  doc.text("de scénario", PAGE.width / 2, 314, { align: "center" });

  // Centered metadata card on the dark half
  const docStats = (analysis.document_stats ?? {}) as Record<string, unknown>;
  const filename = String(
    pick(docStats, "original_filename", "file_name") ??
      analysis.scenario_id ??
      "Scénario sans nom"
  );

  const cardX = 60;
  const cardY = 440;
  const cardW = PAGE.width - 120;
  doc.setFillColor(255, 255, 255);
  doc.roundedRect(cardX, cardY, cardW, 260, 10, 10, "F");

  // Document label
  doc.setFont("helvetica", "bold");
  doc.setFontSize(8);
  doc.setTextColor(...COLORS.ccmRed);
  doc.text("DOCUMENT ANALYSÉ", PAGE.width / 2, cardY + 28, { align: "center" });

  doc.setFont("helvetica", "bold");
  doc.setFontSize(15);
  doc.setTextColor(...COLORS.ink);
  const filenameLines = doc.splitTextToSize(filename, cardW - 40);
  let y = cardY + 50;
  for (const line of filenameLines.slice(0, 2)) {
    doc.text(line, PAGE.width / 2, y, { align: "center" });
    y += 20;
  }

  // Divider
  doc.setDrawColor(...COLORS.slate200);
  doc.setLineWidth(0.5);
  doc.line(cardX + 32, y + 14, cardX + cardW - 32, y + 14);
  y += 38;

  // Risk badge — simple, centred
  const rag = analysis.rag_report ?? {};
  const risk = String(rag.risk_level ?? "unknown");
  const colors = riskColor(risk);
  const label = formatRiskFr(risk);

  doc.setFont("helvetica", "bold");
  doc.setFontSize(8);
  doc.setTextColor(...COLORS.slate500);
  doc.text("NIVEAU DE RISQUE", PAGE.width / 2, y, { align: "center" });
  y += 18;

  doc.setFont("helvetica", "bold");
  doc.setFontSize(13);
  const badgeTextW = doc.getTextWidth(label);
  const badgeW = badgeTextW + 56;
  const badgeH = 36;
  const badgeX = (PAGE.width - badgeW) / 2;
  doc.setFillColor(...colors.bg);
  doc.roundedRect(badgeX, y, badgeW, badgeH, 18, 18, "F");
  doc.setTextColor(...colors.fg);
  doc.text(label, PAGE.width / 2, y + 23, { align: "center" });
  y += badgeH + 30;

  // Date + id
  doc.setFont("helvetica", "normal");
  doc.setFontSize(9);
  doc.setTextColor(...COLORS.slate500);
  doc.text(
    `Analyse effectuée le ${formatDateFr(analysis.analysis_timestamp)}`,
    PAGE.width / 2,
    y,
    { align: "center" }
  );
  if (analysis.scenario_id) {
    doc.text(
      `Identifiant : ${analysis.scenario_id}`,
      PAGE.width / 2,
      y + 14,
      { align: "center" }
    );
  }

  // Footer brand line
  doc.setFont("helvetica", "normal");
  doc.setFontSize(8);
  doc.setTextColor(255, 220, 180);
  doc.text(
    "Document confidentiel — usage interne CCM",
    PAGE.width / 2,
    PAGE.height - 40,
    { align: "center" }
  );
}

// ---------------------------------------------------------------------------
// Table of contents
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Section: Synthèse
// ---------------------------------------------------------------------------

function drawSummary(ctx: Ctx, analysis: Analysis): void {
  const docStats = (analysis.document_stats ?? {}) as Record<string, unknown>;
  const plagiarism = (analysis.plagiarism ?? {}) as Record<string, unknown>;
  const profanity = (analysis.profanity ?? {}) as Record<string, unknown>;
  const adult = (analysis.adult_content ?? {}) as Record<string, unknown>;
  const rag = (analysis.rag_report ?? {}) as Record<string, unknown>;

  sectionHeader(
    ctx,
    "01",
    "Synthèse exécutive",
    "Vue d'ensemble du niveau de risque et des indicateurs clés du document."
  );

  // Risk banner
  const risk = String(rag.risk_level ?? "unknown");
  const colors = riskColor(risk);
  const { doc } = ctx;
  ensureSpace(ctx, 44);
  doc.setFillColor(...colors.bg);
  doc.roundedRect(
    PAGE.marginX,
    ctx.cursorY,
    PAGE.width - PAGE.marginX * 2,
    38,
    6,
    6,
    "F"
  );
  doc.setFillColor(...colors.bar);
  doc.rect(PAGE.marginX, ctx.cursorY, 4, 38, "F");
  doc.setFont("helvetica", "bold");
  doc.setFontSize(7.5);
  doc.setTextColor(...colors.fg);
  doc.text("NIVEAU DE RISQUE GLOBAL", PAGE.marginX + 12, ctx.cursorY + 14);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(14);
  doc.text(formatRiskFr(risk), PAGE.marginX + 12, ctx.cursorY + 30);
  ctx.cursorY += 46;

  // Synthesis text (use conclusion as the headline narrative)
  const conclusion = String(
    rag.conclusion ?? rag.summary ?? "Aucune synthèse disponible."
  );
  paragraph(ctx, conclusion);

  // Metric cards
  subTitle(ctx, "Indicateurs clés");
  const simPct = toPercent(
    pick(plagiarism, "global_similarity_score", "score") ?? 0
  );
  const profPct = toPercent(pick(profanity, "profanity_score") ?? 0);
  const adultPct = toPercent(pick(adult, "adult_content_score") ?? 0);
  const words = toNumber(pick(docStats, "words_count", "word_count"));

  const cardW = (PAGE.width - PAGE.marginX * 2 - 10 * 3) / 4;
  const startX = PAGE.marginX;
  ensureSpace(ctx, 56);
  const y = ctx.cursorY;
  metricCard(ctx, {
    x: startX,
    y,
    width: cardW,
    label: "Similarité",
    value: `${simPct}%`,
    accent: riskColor(riskFromScore(simPct)).bar,
  });
  metricCard(ctx, {
    x: startX + cardW + 10,
    y,
    width: cardW,
    label: "Vulgarité",
    value: `${profPct}%`,
    accent: riskColor(riskFromScore(profPct)).bar,
  });
  metricCard(ctx, {
    x: startX + (cardW + 10) * 2,
    y,
    width: cardW,
    label: "Contenu adulte",
    value: `${adultPct}%`,
    accent: riskColor(riskFromScore(adultPct)).bar,
  });
  metricCard(ctx, {
    x: startX + (cardW + 10) * 3,
    y,
    width: cardW,
    label: "Mots analysés",
    value: words.toLocaleString("fr-FR"),
    accent: COLORS.slate400,
  });
  ctx.cursorY += 56;

  // Document metadata table
  subTitle(ctx, "Document analysé");
  const meta: Array<[string, string]> = [
    [
      "Nom du fichier",
      String(pick(docStats, "original_filename", "file_name") ?? "—"),
    ],
    ["Identifiant scénario", String(analysis.scenario_id ?? "—")],
    [
      "Date d'analyse",
      formatDateFr(analysis.analysis_timestamp),
    ],
    [
      "Nombre de mots",
      words ? words.toLocaleString("fr-FR") : "—",
    ],
    [
      "Nombre de chunks",
      String(
        pick(docStats, "chunks_count", "chunk_count") ?? "—"
      ),
    ],
  ];
  autoTable(doc, {
    startY: ctx.cursorY,
    head: [],
    body: meta,
    theme: "plain",
    styles: { fontSize: 9.5, cellPadding: 6, textColor: COLORS.slate700 },
    columnStyles: {
      0: { fillColor: COLORS.slate50, cellWidth: 170, fontStyle: "bold" },
      1: { cellWidth: "auto" },
    },
    margin: { left: PAGE.marginX, right: PAGE.marginX },
    didDrawCell: (data) => {
      if (data.section === "body") {
        doc.setDrawColor(...COLORS.slate200);
        doc.setLineWidth(0.3);
        doc.line(
          data.cell.x,
          data.cell.y + data.cell.height,
          data.cell.x + data.cell.width,
          data.cell.y + data.cell.height
        );
      }
    },
  });
  ctx.cursorY = ((doc as unknown as {
    lastAutoTable: { finalY: number };
  }).lastAutoTable.finalY ?? ctx.cursorY) + 16;
}

// ---------------------------------------------------------------------------
// Section: Plagiat
// ---------------------------------------------------------------------------

function drawPlagiarism(ctx: Ctx, analysis: Analysis): void {
  const plagiarism = (analysis.plagiarism ?? {}) as Record<string, unknown>;
  const rag = (analysis.rag_report ?? {}) as Record<string, unknown>;
  const { doc } = ctx;
  const matches = Array.isArray(plagiarism.matches)
    ? (plagiarism.matches as Array<Record<string, unknown>>)
    : [];
  const simPct = toPercent(
    pick(plagiarism, "global_similarity_score", "score") ?? 0
  );

  sectionHeader(
    ctx,
    "02",
    "Détection de plagiat",
    "Comparaison sémantique du document avec le corpus indexé."
  );

  // Headline score
  ensureSpace(ctx, 60);
  doc.setFont("helvetica", "normal");
  doc.setFontSize(9);
  doc.setTextColor(...COLORS.slate500);
  doc.text("SIMILARITÉ GLOBALE", PAGE.marginX, ctx.cursorY);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(28);
  doc.setTextColor(...COLORS.ink);
  doc.text(`${simPct}%`, PAGE.width - PAGE.marginX, ctx.cursorY + 4, {
    align: "right",
  });
  ctx.cursorY += 14;
  scoreBar(
    ctx,
    simPct,
    PAGE.marginX,
    ctx.cursorY,
    PAGE.width - PAGE.marginX * 2,
    8
  );
  ctx.cursorY += 26;

  // Stat row
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
    styles: { fontSize: 9.5, cellPadding: 7, textColor: COLORS.slate700 },
    columnStyles: {
      0: { fillColor: COLORS.slate50, cellWidth: 180, fontStyle: "bold" },
      1: { cellWidth: "auto" },
    },
    margin: { left: PAGE.marginX, right: PAGE.marginX },
  });
  ctx.cursorY = ((doc as unknown as {
    lastAutoTable: { finalY: number };
  }).lastAutoTable.finalY ?? ctx.cursorY) + 18;

  // Narrative explanation
  const explanation = String(rag.plagiarism_explanation ?? "").trim();
  if (explanation) {
    subTitle(ctx, "Analyse");
    paragraph(ctx, explanation);
  }

  // Sources breakdown
  if (sources.length > 0) {
    subTitle(ctx, "Sources identifiées");
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
        58,
        6,
        6,
        "FD"
      );
      doc.setFillColor(...riskColor(sourceRisk).bar);
      doc.rect(PAGE.marginX, cardY, 3, 58, "F");

      doc.setFont("helvetica", "bold");
      doc.setFontSize(10);
      doc.setTextColor(...COLORS.ink);
      const truncatedName =
        name.length > 70 ? `${name.slice(0, 67)}…` : name;
      doc.text(truncatedName, PAGE.marginX + 14, cardY + 18);

      doc.setFont("helvetica", "normal");
      doc.setFontSize(8);
      doc.setTextColor(...COLORS.slate500);
      doc.text(
        `${matchesCount} passage${matchesCount > 1 ? "s" : ""} similaire${matchesCount > 1 ? "s" : ""}`,
        PAGE.marginX + 14,
        cardY + 32
      );

      const barX = PAGE.marginX + 14;
      const barW = PAGE.width - PAGE.marginX * 2 - 28 - 90;
      scoreBar(ctx, best, barX, cardY + 44, barW, 5);

      doc.setFont("helvetica", "bold");
      doc.setFontSize(14);
      doc.setTextColor(...COLORS.ink);
      doc.text(
        `${best}%`,
        PAGE.width - PAGE.marginX - 14,
        cardY + 24,
        { align: "right" }
      );
      riskBadge(
        ctx,
        sourceRisk,
        PAGE.width - PAGE.marginX - 80,
        cardY + 36
      );

      ctx.cursorY += 68;
    });
  }
}

// ---------------------------------------------------------------------------
// Section: Modération
// ---------------------------------------------------------------------------

function drawModeration(ctx: Ctx, analysis: Analysis): void {
  const profanity = (analysis.profanity ?? {}) as Record<string, unknown>;
  const adult = (analysis.adult_content ?? {}) as Record<string, unknown>;
  const rag = (analysis.rag_report ?? {}) as Record<string, unknown>;
  const { doc } = ctx;

  sectionHeader(
    ctx,
    "03",
    "Modération de contenu",
    "Détection de vulgarité et de contenu adulte dans le scénario."
  );

  const profWords = Array.isArray(profanity.detected_words)
    ? (profanity.detected_words as string[])
    : [];
  const adultTerms = Array.isArray(adult.detected_terms)
    ? (adult.detected_terms as string[])
    : [];
  const profPct = toPercent(pick(profanity, "profanity_score") ?? 0);
  const adultPct = toPercent(pick(adult, "adult_content_score") ?? 0);

  ensureSpace(ctx, 102);
  const cardW = (PAGE.width - PAGE.marginX * 2 - 12) / 2;
  const cardH = 94;

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
    doc.roundedRect(x, cardY, cardW, cardH, 6, 6, "FD");
    doc.setFillColor(...color);
    doc.rect(x, cardY, 3, cardH, "F");

    doc.setFont("helvetica", "bold");
    doc.setFontSize(8.5);
    doc.setTextColor(...COLORS.ccmRed);
    doc.text(title.toUpperCase(), x + 12, cardY + 16);

    doc.setFont("helvetica", "bold");
    doc.setFontSize(22);
    doc.setTextColor(...COLORS.ink);
    doc.text(`${pct}%`, x + 12, cardY + 42);

    scoreBar(ctx, pct, x + 12, cardY + 50, cardW - 24, 5);

    doc.setFont("helvetica", "normal");
    doc.setFontSize(8);
    doc.setTextColor(...COLORS.slate500);
    doc.text(
      detected.length === 0
        ? "Aucun terme détecté."
        : `${detected.length} terme${detected.length > 1 ? "s" : ""} détecté${detected.length > 1 ? "s" : ""}`,
      x + 12,
      cardY + 66
    );

    if (detected.length > 0) {
      const preview = detected.slice(0, 5).join(", ");
      const lines = doc.splitTextToSize(preview, cardW - 24);
      doc.setTextColor(...COLORS.slate700);
      doc.setFontSize(8.5);
      doc.text(lines.slice(0, 1), x + 12, cardY + 80);
    }
  };

  drawModCard("Vulgarité", profPct, profWords, PAGE.marginX);
  drawModCard(
    "Contenu adulte",
    adultPct,
    adultTerms,
    PAGE.marginX + cardW + 12
  );
  ctx.cursorY += cardH + 10;

  // Narrative
  const moderation = String(rag.moderation_explanation ?? "").trim();
  if (moderation) {
    subTitle(ctx, "Analyse détaillée");
    paragraph(ctx, moderation);
  }
}

// ---------------------------------------------------------------------------
// Section: Constantes nationales
// ---------------------------------------------------------------------------

function drawMoroccanConstants(ctx: Ctx, analysis: Analysis): boolean {
  const mc = (
    (analysis as unknown as Record<string, unknown>).moroccan_constants ??
    {}
  ) as Record<string, unknown>;
  if (!mc || Object.keys(mc).length === 0) return false;

  const riskLevel = String(mc.risk_level ?? "low");
  const findings = Array.isArray(mc.findings)
    ? (mc.findings as Array<Record<string, unknown>>)
    : [];

  sectionHeader(
    ctx,
    "04",
    "Constantes nationales",
    "Conformité aux références culturelles et institutionnelles du Maroc."
  );

  // Compliance banner
  const colors = riskColor(riskLevel);
  const { doc } = ctx;
  ensureSpace(ctx, 50);
  doc.setFillColor(...colors.bg);
  doc.roundedRect(
    PAGE.marginX,
    ctx.cursorY,
    PAGE.width - PAGE.marginX * 2,
    44,
    6,
    6,
    "F"
  );
  doc.setFillColor(...colors.bar);
  doc.rect(PAGE.marginX, ctx.cursorY, 4, 44, "F");
  doc.setFont("helvetica", "bold");
  doc.setFontSize(8);
  doc.setTextColor(...colors.fg);
  doc.text(
    "NIVEAU DE CONFORMITÉ",
    PAGE.marginX + 14,
    ctx.cursorY + 17
  );
  doc.setFont("helvetica", "bold");
  doc.setFontSize(14);
  doc.text(
    formatRiskFr(riskLevel),
    PAGE.marginX + 14,
    ctx.cursorY + 35
  );
  ctx.cursorY += 58;

  if (findings.length === 0) {
    paragraph(ctx, "Aucun signal de non-conformité détecté.", { italic: true });
    return true;
  }

  subTitle(ctx, "Points relevés");
  findings.slice(0, 10).forEach((finding, i) => {
    ensureSpace(ctx, 46);
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
    ctx.cursorY += 16;
    if (detail) paragraph(ctx, detail);
  });
  return true;
}

// ---------------------------------------------------------------------------
// Section: Recommandations
// ---------------------------------------------------------------------------

function drawRecommendations(ctx: Ctx, analysis: Analysis, number: string): void {
  const rag = (analysis.rag_report ?? {}) as Record<string, unknown>;
  const recs = Array.isArray(rag.recommendations)
    ? (rag.recommendations as string[])
    : [];
  if (recs.length === 0) return;

  sectionHeader(
    ctx,
    number,
    "Recommandations",
    "Actions suggérées pour finaliser la validation du scénario."
  );

  const { doc } = ctx;
  recs.forEach((rec, idx) => {
    ensureSpace(ctx, 42);
    const cardY = ctx.cursorY;
    const cardH = 36;

    doc.setFillColor(...COLORS.slate50);
    doc.roundedRect(
      PAGE.marginX,
      cardY,
      PAGE.width - PAGE.marginX * 2,
      cardH,
      6,
      6,
      "F"
    );
    // Number badge
    doc.setFillColor(...COLORS.ccmRed);
    doc.circle(PAGE.marginX + 18, cardY + cardH / 2, 11, "F");
    doc.setFont("helvetica", "bold");
    doc.setFontSize(10);
    doc.setTextColor(255, 255, 255);
    doc.text(
      String(idx + 1),
      PAGE.marginX + 18,
      cardY + cardH / 2 + 4,
      { align: "center" }
    );

    // Recommendation text
    doc.setFont("helvetica", "normal");
    doc.setFontSize(9.5);
    doc.setTextColor(...COLORS.slate700);
    const textX = PAGE.marginX + 38;
    const textW = PAGE.width - PAGE.marginX * 2 - 50;
    const lines = doc.splitTextToSize(rec, textW);
    const limited = lines.slice(0, 2);
    const verticalOffset =
      limited.length === 1 ? cardH / 2 + 3 : cardH / 2 - 3;
    limited.forEach((line: string, lineIdx: number) => {
      doc.text(line, textX, cardY + verticalOffset + lineIdx * 12);
    });

    // Expand card if text wraps to 2 lines
    const realH = Math.max(cardH, limited.length * 14 + 18);
    if (realH > cardH) {
      // Re-draw with proper height
      doc.setFillColor(...COLORS.slate50);
      doc.roundedRect(
        PAGE.marginX,
        cardY,
        PAGE.width - PAGE.marginX * 2,
        realH,
        6,
        6,
        "F"
      );
      doc.setFillColor(...COLORS.ccmRed);
      doc.circle(PAGE.marginX + 18, cardY + realH / 2, 11, "F");
      doc.setFont("helvetica", "bold");
      doc.setFontSize(10);
      doc.setTextColor(255, 255, 255);
      doc.text(
        String(idx + 1),
        PAGE.marginX + 18,
        cardY + realH / 2 + 4,
        { align: "center" }
      );
      doc.setFont("helvetica", "normal");
      doc.setFontSize(9.5);
      doc.setTextColor(...COLORS.slate700);
      limited.forEach((line: string, lineIdx: number) => {
        doc.text(
          line,
          textX,
          cardY + 14 + lineIdx * 12 + (limited.length === 1 ? 6 : 0)
        );
      });
    }

    ctx.cursorY += realH + 10;
  });
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
  const ctx: Ctx = {
    doc,
    cursorY: PAGE.marginTop,
    logo,
    pageNumber: 1,
    scenarioName,
    sectionLabel: "",
  };

  // 1. Cover
  drawCoverPage(ctx, analysis);

  const hasMoroccan =
    !!(analysis as unknown as Record<string, unknown>).moroccan_constants &&
    Object.keys(
      ((analysis as unknown as Record<string, unknown>)
        .moroccan_constants ?? {}) as Record<string, unknown>
    ).length > 0;
  const rag = (analysis.rag_report ?? {}) as Record<string, unknown>;
  const recs = Array.isArray(rag.recommendations)
    ? (rag.recommendations as string[])
    : [];
  const hasRecs = recs.length > 0;

  // Start content on a fresh page after the cover; sections flow naturally.
  newPage(ctx);

  drawSummary(ctx, analysis);
  ctx.cursorY += 8;
  drawPlagiarism(ctx, analysis);
  ctx.cursorY += 8;
  drawModeration(ctx, analysis);
  if (hasMoroccan) {
    ctx.cursorY += 8;
    drawMoroccanConstants(ctx, analysis);
  }
  if (hasRecs) {
    ctx.cursorY += 8;
    drawRecommendations(ctx, analysis, hasMoroccan ? "05" : "04");
  }

  return doc;
}

export async function downloadPdfReport(analysis: Analysis): Promise<void> {
  const doc = await generatePdfReport(analysis);
  const id = analysis.scenario_id || "scenario";
  doc.save(`rapport_${id}.pdf`);
}
