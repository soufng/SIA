import jsPDF from "jspdf";
import autoTable from "jspdf-autotable";
import type { Analysis } from "./types";
import { formatScore } from "./utils";

function pick<T>(obj: Record<string, T> | undefined, ...keys: string[]): T | undefined {
  if (!obj) return undefined;
  for (const k of keys) {
    if (obj[k] !== undefined && obj[k] !== null) return obj[k];
  }
  return undefined;
}

function truncate(value: unknown, max = 900): string {
  const s = value == null || value === "" ? "Texte non disponible." : String(value);
  return s.length <= max ? s : `${s.slice(0, max).trimEnd()}...`;
}

export function generatePdfReport(analysis: Analysis): jsPDF {
  const doc = new jsPDF({ unit: "pt", format: "a4" });
  const pageWidth = doc.internal.pageSize.getWidth();

  // Cover
  doc.setFontSize(22);
  doc.setTextColor("#1F2937");
  doc.text("Rapport d'analyse de scenario", pageWidth / 2, 200, {
    align: "center",
  });
  doc.setFontSize(11);
  doc.setTextColor("#4B5563");
  doc.text(
    `Scenario ID: ${analysis.scenario_id ?? "n/a"}`,
    pageWidth / 2,
    240,
    { align: "center" }
  );
  doc.text(
    `Date d'analyse: ${analysis.analysis_timestamp ?? "n/a"}`,
    pageWidth / 2,
    258,
    { align: "center" }
  );

  doc.addPage();

  const docStats = analysis.document_stats ?? {};
  const plagiarism = analysis.plagiarism ?? {};
  const profanity = analysis.profanity ?? {};
  const adult = analysis.adult_content ?? {};
  const rag = analysis.rag_report ?? {};

  let cursorY = 50;
  const section = (title: string) => {
    doc.setFontSize(14);
    doc.setTextColor("#111827");
    doc.text(title, 40, cursorY);
    cursorY += 8;
  };
  const kvTable = (rows: Array<[string, string]>) => {
    autoTable(doc, {
      startY: cursorY + 6,
      head: [],
      body: rows,
      theme: "grid",
      styles: { fontSize: 9, cellPadding: 6, textColor: "#1F2937" },
      columnStyles: {
        0: { fillColor: [243, 244, 246], cellWidth: 160, fontStyle: "bold" },
        1: { cellWidth: "auto" },
      },
      margin: { left: 40, right: 40 },
    });
    cursorY = (doc as unknown as { lastAutoTable: { finalY: number } })
      .lastAutoTable.finalY + 14;
  };
  const paragraph = (title: string, text: unknown) => {
    doc.setFontSize(10);
    doc.setTextColor("#111827");
    doc.setFont("helvetica", "bold");
    doc.text(title, 40, cursorY);
    cursorY += 14;
    doc.setFont("helvetica", "normal");
    doc.setTextColor("#1F2937");
    const content = text == null || text === "" ? "Non disponible." : String(text);
    const lines = doc.splitTextToSize(content, pageWidth - 80);
    doc.text(lines, 40, cursorY);
    cursorY += lines.length * 12 + 8;
    if (cursorY > 760) {
      doc.addPage();
      cursorY = 50;
    }
  };

  section("1. Statistiques du document");
  kvTable([
    ["Nombre de mots", String(pick(docStats as Record<string, unknown>, "words_count", "word_count") ?? 0)],
    ["Nombre de chunks", String(pick(docStats as Record<string, unknown>, "chunks_count", "chunk_count") ?? 0)],
  ]);

  section("2. Detection de plagiat");
  const matches = Array.isArray(plagiarism.matches) ? plagiarism.matches : [];
  kvTable([
    ["Score de similarite", formatScore(plagiarism.global_similarity_score ?? plagiarism.score ?? 0, "%")],
    ["Niveau de risque", String(rag.risk_level ?? "unknown")],
    ["Correspondances trouvees", String(matches.length)],
  ]);

  section("3. Vulgarite");
  kvTable([
    ["Score", formatScore(profanity.profanity_score ?? 0, "%")],
    [
      "Mots detectes",
      profanity.detected_words?.length ? profanity.detected_words.join(", ") : "Aucun",
    ],
  ]);

  section("4. Contenu adulte");
  kvTable([
    ["Score", formatScore(adult.adult_content_score ?? 0, "%")],
    ["Niveau de risque", String(adult.risk_level ?? "low")],
    [
      "Termes detectes",
      adult.detected_terms?.length ? adult.detected_terms.join(", ") : "Aucun",
    ],
  ]);

  section("5. Rapport RAG");
  paragraph("Resume", rag.summary);
  paragraph("Explication du plagiat", rag.plagiarism_explanation);
  paragraph("Explication de la moderation", rag.moderation_explanation);
  paragraph(
    "Recommandations",
    (rag.recommendations ?? []).map((r) => `- ${r}`).join("\n")
  );

  section("6. Passages similaires");
  if (matches.length === 0) {
    paragraph("", "Aucun passage similaire detecte.");
  } else {
    matches.forEach((m, i) => {
      if (cursorY > 700) {
        doc.addPage();
        cursorY = 50;
      }
      section(`Match ${i + 1}`);
      kvTable([
        ["Score", formatScore(m.similarity_score ?? m.similarity ?? m.score ?? 0, "%")],
        ["Scenario correspondant", String(m.matched_scenario_id ?? m.filename ?? "inconnu")],
      ]);
      paragraph("Extrait du chunk analyse", truncate(m.chunk_text));
      paragraph("Extrait du chunk similaire", truncate(m.matched_chunk_text));
    });
  }

  return doc;
}

export function downloadPdfReport(analysis: Analysis): void {
  const doc = generatePdfReport(analysis);
  const id = analysis.scenario_id || "scenario";
  doc.save(`rapport_${id}.pdf`);
}
