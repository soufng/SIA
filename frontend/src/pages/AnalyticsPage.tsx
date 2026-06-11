import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fetchStatistics } from "@/lib/api";
import type { Statistics } from "@/lib/types";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, THead, Td, Th, Tr } from "@/components/ui/table";
import { formatRiskLabel, formatScore, riskColor } from "@/lib/utils";

function metric(label: string, value: React.ReactNode) {
  return (
    <Card>
      <CardContent className="pt-6">
        <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
        <p className="mt-1 text-2xl font-semibold">{value}</p>
      </CardContent>
    </Card>
  );
}

export function AnalyticsPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["statistics"],
    queryFn: fetchStatistics,
  });

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-bold text-slate-900">
          Tableau de bord statistiques
        </h1>
        <p className="text-slate-500 mt-1">
          Statistiques globales calculées depuis MongoDB.
        </p>
      </header>

      {isLoading && <Alert variant="info">Chargement...</Alert>}
      {error && <Alert variant="error">{(error as Error).message}</Alert>}
      {data && <AnalyticsContent stats={data} />}
    </div>
  );
}

function AnalyticsContent({ stats }: { stats: Statistics }) {
  const total = Number(stats.total_analyses ?? 0);
  if (!total) {
    return (
      <Alert variant="info">Aucune analyse disponible pour le moment.</Alert>
    );
  }

  const riskData = [
    { name: "FAIBLE", value: stats.risk_counts?.low ?? 0 },
    { name: "MOYEN", value: stats.risk_counts?.medium ?? 0 },
    { name: "ÉLEVÉ", value: stats.risk_counts?.high ?? 0 },
  ];

  const volume = Array.isArray(stats.analyses_by_date)
    ? stats.analyses_by_date
    : [];
  const topSimilar = Array.isArray(stats.top_similar_scenarios)
    ? stats.top_similar_scenarios
    : [];
  const risky = Array.isArray(stats.risky_analyses) ? stats.risky_analyses : [];

  return (
    <>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {metric("Analyses", total)}
        {metric(
          "Similarité moyenne",
          formatScore(stats.average_similarity_score, "%")
        )}
        {metric(
          "Vulgarité moyenne",
          formatScore(stats.average_profanity_score, "%")
        )}
        {metric(
          "Contenu adulte moyen",
          formatScore(stats.average_adult_content_score, "%")
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader>
            <CardTitle>Répartition du risque</CardTitle>
          </CardHeader>
          <CardContent className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={riskData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="value" fill="#C1272D" />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Analyses par date</CardTitle>
          </CardHeader>
          <CardContent className="h-72">
            {volume.length === 0 ? (
              <Alert variant="info">
                Pas encore assez de données pour afficher une évolution
                temporelle.
              </Alert>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={volume}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="date" />
                  <YAxis allowDecimals={false} />
                  <Tooltip />
                  <Line type="monotone" dataKey="count" stroke="#C1272D" />
                </LineChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
      </div>

      <div>
        <h2 className="text-lg font-semibold mb-3">
          Top 10 des scénarios les plus similaires
        </h2>
        {topSimilar.length === 0 ? (
          <Alert variant="info">Aucun scénario similaire à afficher.</Alert>
        ) : (
          <Table>
            <THead>
              <Tr>
                <Th>Scénario ID</Th>
                <Th>Date</Th>
                <Th>Score de similarité</Th>
                <Th>Risque</Th>
              </Tr>
            </THead>
            <TBody>
              {topSimilar.map((row, i) => {
                const r = row as Record<string, unknown>;
                const risk = String(r.risk_level ?? "unknown").toLowerCase();
                return (
                  <Tr key={i}>
                    <Td className="font-mono text-xs">
                      {String(r.scenario_id ?? "non disponible")}
                    </Td>
                    <Td>{String(r.analysis_timestamp ?? "non disponible")}</Td>
                    <Td>{formatScore(r.similarity_score, "%")}</Td>
                    <Td>
                      <Badge className={riskColor(risk)}>{formatRiskLabel(risk)}</Badge>
                    </Td>
                  </Tr>
                );
              })}
            </TBody>
          </Table>
        )}
      </div>

      <div>
        <h2 className="text-lg font-semibold mb-3">Analyses à risque</h2>
        {risky.length === 0 ? (
          <Alert variant="info">
            Aucune analyse à risque moyen ou élevé.
          </Alert>
        ) : (
          <Table>
            <THead>
              <Tr>
                <Th>Scénario ID</Th>
                <Th>Date</Th>
                <Th>Risque</Th>
                <Th>Similarité</Th>
                <Th>Vulgarité</Th>
                <Th>Contenu adulte</Th>
                <Th>Résumé RAG</Th>
              </Tr>
            </THead>
            <TBody>
              {risky.map((row, i) => {
                const r = row as Record<string, unknown>;
                const risk = String(r.risk_level ?? "unknown").toLowerCase();
                return (
                  <Tr key={i}>
                    <Td className="font-mono text-xs">
                      {String(r.scenario_id ?? "non disponible")}
                    </Td>
                    <Td>{String(r.analysis_timestamp ?? "non disponible")}</Td>
                    <Td>
                      <Badge className={riskColor(risk)}>{formatRiskLabel(risk)}</Badge>
                    </Td>
                    <Td>{formatScore(r.similarity_score, "%")}</Td>
                    <Td>{formatScore(r.profanity_score, "%")}</Td>
                    <Td>{formatScore(r.adult_content_score, "%")}</Td>
                    <Td className="max-w-xs truncate">
                      {String(r.summary ?? "")}
                    </Td>
                  </Tr>
                );
              })}
            </TBody>
          </Table>
        )}
      </div>
    </>
  );
}
