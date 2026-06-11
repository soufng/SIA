import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { UploadForm } from "@/components/UploadForm";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAnalysisStore } from "@/store/analysis";
import { ResultsPage } from "./ResultsPage";

const steps = [
  "Extraction du texte PDF",
  "Nettoyage et découpage en segments",
  "Plongements et recherche Qdrant",
  "Détection plagiat et modération",
  "Rapport RAG et synthèse finale",
];

export function UploadPage() {
  const analysis = useAnalysisStore((s) => s.analysis);
  const reset = useAnalysisStore((s) => s.reset);
  const location = useLocation();
  const resultsAnchorRef = useRef<HTMLDivElement>(null);
  const previousScenarioId = useRef<string | null | undefined>(undefined);

  // Clear any previously persisted analysis when arriving on this page or
  // when leaving / refreshing it. The store uses ``persist`` to keep state
  // across reloads — that's the right thing for /history but it makes the
  // analyse page feel stale ("I just refreshed, why do I still see the
  // previous PDF report?"). The HistoryPage opt-in via
  // ``navigate("/upload", { state: { keepResults: true } })`` is preserved
  // so opening a stored analysis from history still works.
  useEffect(() => {
    const keepResults = (location.state as { keepResults?: boolean } | null)
      ?.keepResults;
    if (!keepResults) {
      reset();
    }
    const handleBeforeUnload = () => reset();
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
      reset();
    };
    // We intentionally run this only on mount/unmount of /upload — re-
    // running on every location change would also wipe the result we
    // just received from the analyse mutation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // When a new analysis lands, smoothly scroll to the results block so the
  // operator immediately sees the report below the form.
  useEffect(() => {
    const currentId = analysis?.scenario_id ?? null;
    if (currentId && currentId !== previousScenarioId.current) {
      previousScenarioId.current = currentId;
      // Defer one frame so the results DOM is mounted before we scroll.
      requestAnimationFrame(() => {
        resultsAnchorRef.current?.scrollIntoView({
          behavior: "smooth",
          block: "start",
        });
      });
    }
    if (!currentId) {
      previousScenarioId.current = null;
    }
  }, [analysis?.scenario_id]);

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-3xl font-bold text-slate-900">
          Analyser un scénario
        </h1>
        <p className="mt-1 text-slate-500">
          Chargez un PDF puis lancez l'analyse. Le rapport complet s'affiche
          automatiquement ci-dessous dès que l'analyse est terminée.
        </p>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <UploadForm />
        <Card>
          <CardHeader>
            <CardTitle>Pipeline exécuté</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2 text-sm text-slate-700">
              {steps.map((s) => (
                <li key={s} className="flex items-start gap-2">
                  <span className="mt-1 h-1.5 w-1.5 rounded-full bg-ccm-red shrink-0" />
                  {s}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>

      {analysis && (
        <div
          ref={resultsAnchorRef}
          className="scroll-mt-24 border-t border-slate-200 pt-8"
        >
          <ResultsPage />
        </div>
      )}
    </div>
  );
}
