import type { Analysis } from "@/lib/types";
import { Card, CardContent } from "./ui/card";
import { formatRiskLabel, formatScore, riskColor } from "@/lib/utils";
import { Badge } from "./ui/badge";

interface Props {
  analysis: Analysis;
}

function metric(label: string, value: React.ReactNode) {
  return (
    <Card>
      <CardContent className="pt-6">
        <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
        <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
      </CardContent>
    </Card>
  );
}

export function StatusCards({ analysis }: Props) {
  const docStats = analysis.document_stats ?? {};
  const plagiarism = analysis.plagiarism ?? {};
  const profanity = analysis.profanity ?? {};
  const adult = analysis.adult_content ?? {};
  const rag = analysis.rag_report ?? {};

  const words = docStats.words_count ?? docStats.word_count ?? 0;
  const chunks = docStats.chunks_count ?? docStats.chunk_count ?? 0;
  const similarity = plagiarism.score ?? plagiarism.global_similarity_score ?? 0;
  const risk = String(rag.risk_level ?? "unknown");
  const profanityScore = profanity.profanity_score ?? 0;
  const adultScore = adult.adult_content_score ?? 0;

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {metric("Mots", words)}
        {metric("Segments", chunks)}
        {metric("Similarité", formatScore(similarity, "%"))}
        {metric(
          "Risque",
          <Badge className={riskColor(risk)}>{formatRiskLabel(risk)}</Badge>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3">
        {metric("Vulgarité", formatScore(profanityScore, "%"))}
        {metric("Contenu adulte", formatScore(adultScore, "%"))}
      </div>
    </div>
  );
}
