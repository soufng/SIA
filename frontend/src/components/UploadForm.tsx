import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import {
  BarChart3,
  CheckCircle2,
  Circle,
  Database,
  FileText,
  Landmark,
  Layers,
  Loader2,
  Search,
  ShieldAlert,
  Upload as UploadIcon,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Button } from "./ui/button";
import { Alert } from "./ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { cn } from "@/lib/utils";
import {
  analyzePdfAsync,
  fetchJobState,
  type AnalyzeJobAck,
  type AnalyzeJobState,
} from "@/lib/api";
import { useAnalysisStore } from "@/store/analysis";

// Délai entre deux requêtes de polling (ms). Court mais raisonnable pour
// que la barre de progression soit fluide sans noyer le backend.
const POLL_INTERVAL_MS = 1200;

// Plafond avant qu'on déclare le job perdu côté UI (ms). Le worker peut
// quand même finir en arrière-plan et apparaître dans l'historique.
const POLL_TIMEOUT_MS = 5 * 60 * 1000;

// Étapes affichées tant que le backend n'a pas pris la main. Une fois
// que le job est ``running``, c'est le ``stage`` retourné par l'API qui
// gouverne ce qu'on affiche.
const FALLBACK_STAGE_LABEL = "Préparation de l'analyse";

// Pipeline UI map. The backend only reports 4 coarse stages (start /
// pipeline / persist / done) and pegs everything between them at 40–90%.
// We surface the actual internal stages of ``analyze_scenario`` here so
// the operator sees which step is running. The ``range`` is in
// ``progress_pct`` units — once the bar crosses a step's upper bound,
// that step is marked done.
interface PipelineStep {
  key: string;
  label: string;
  icon: LucideIcon;
  range: [number, number];
}

const PIPELINE_STEPS: PipelineStep[] = [
  {
    key: "upload",
    label: "Réception et préparation du fichier",
    icon: UploadIcon,
    range: [0, 10],
  },
  {
    key: "extract",
    label: "Extraction et nettoyage du PDF",
    icon: FileText,
    range: [10, 30],
  },
  {
    key: "chunk",
    label: "Segmentation et embeddings (e5-base)",
    icon: Layers,
    range: [30, 50],
  },
  {
    key: "plagiarism",
    label: "Détection de plagiat (Qdrant)",
    icon: Search,
    range: [50, 65],
  },
  {
    key: "moderation",
    label: "Modération multilingue (FR / AR / Darija)",
    icon: ShieldAlert,
    range: [65, 78],
  },
  {
    key: "constants",
    label: "Vérification des constantes marocaines",
    icon: Landmark,
    range: [78, 85],
  },
  {
    key: "rag",
    label: "Synthèse RAG et rapport éditorial",
    icon: BarChart3,
    range: [85, 95],
  },
  {
    key: "persist",
    label: "Enregistrement de l'analyse",
    icon: Database,
    range: [95, 100],
  },
];

function getStepStatus(
  step: PipelineStep,
  progressPct: number,
): "done" | "active" | "pending" {
  if (progressPct >= step.range[1]) return "done";
  if (progressPct >= step.range[0]) return "active";
  return "pending";
}

export function UploadForm() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const setResponse = useAnalysisStore((s) => s.setResponse);
  const setError = useAnalysisStore((s) => s.setError);
  const lastError = useAnalysisStore((s) => s.lastError);
  const navigate = useNavigate();

  const [progressPct, setProgressPct] = useState(0);
  const [stageLabel, setStageLabel] = useState(FALLBACK_STAGE_LABEL);
  const [jobId, setJobId] = useState<string | null>(null);
  const [isPolling, setIsPolling] = useState(false);

  const pollingStartedAtRef = useRef<number | null>(null);
  const pollingTimerRef = useRef<number | null>(null);

  const stopPolling = () => {
    if (pollingTimerRef.current !== null) {
      window.clearTimeout(pollingTimerRef.current);
      pollingTimerRef.current = null;
    }
    pollingStartedAtRef.current = null;
    setIsPolling(false);
  };

  useEffect(() => () => stopPolling(), []);

  const pollOnce = async (currentJobId: string) => {
    let state: AnalyzeJobState;
    try {
      state = await fetchJobState(currentJobId);
    } catch (err) {
      // Réseau qui hoquète : on attend le tick suivant plutôt que
      // d'abandonner immédiatement.
      pollingTimerRef.current = window.setTimeout(
        () => pollOnce(currentJobId),
        POLL_INTERVAL_MS,
      );
      return;
    }

    setProgressPct(Math.max(0, Math.min(100, state.progress_pct ?? 0)));
    if (state.stage) setStageLabel(state.stage);

    if (state.status === "completed") {
      stopPolling();
      setProgressPct(100);
      if (state.analysis) {
        setResponse({
          success: true,
          scenario_id:
            state.result_scenario_id ?? state.scenario_id ?? "",
          analysis: state.analysis,
        });
        // Analyse terminée : on bascule l'utilisateur vers la page de
        // résultats. L'upload se fait depuis l'accueil ; les rapports
        // s'affichent sur /results.
        navigate("/results", { state: { keepResults: true } });
      } else {
        setError(
          "Analyse marquée comme terminée mais aucune donnée n'est " +
            "disponible côté serveur. Réessayez ou consultez l'historique.",
        );
      }
      return;
    }
    if (state.status === "failed") {
      stopPolling();
      setProgressPct(0);
      setError(state.error || "L'analyse a échoué côté serveur.");
      return;
    }

    // Garde-fou : un job qui ne progresse pas depuis trop longtemps doit
    // libérer l'UI plutôt que de la maintenir bloquée.
    const startedAt = pollingStartedAtRef.current;
    if (startedAt !== null && Date.now() - startedAt > POLL_TIMEOUT_MS) {
      stopPolling();
      setError(
        "L'analyse prend plus de temps que prévu. " +
          "Elle continue côté serveur — vérifiez l'historique plus tard.",
      );
      return;
    }

    pollingTimerRef.current = window.setTimeout(
      () => pollOnce(currentJobId),
      POLL_INTERVAL_MS,
    );
  };

  const mutation = useMutation({
    mutationFn: (f: File) => analyzePdfAsync(f),
    onMutate: () => {
      stopPolling();
      setProgressPct(2);
      setStageLabel(FALLBACK_STAGE_LABEL);
      setJobId(null);
    },
    onSuccess: (ack: AnalyzeJobAck) => {
      setJobId(ack.job_id);
      setStageLabel(ack.stage || FALLBACK_STAGE_LABEL);
      setProgressPct(Math.max(2, ack.progress_pct ?? 5));
      setIsPolling(true);
      pollingStartedAtRef.current = Date.now();
      pollingTimerRef.current = window.setTimeout(
        () => pollOnce(ack.job_id),
        POLL_INTERVAL_MS,
      );
    },
    onError: (e: Error) => {
      setError(e.message);
      setProgressPct(0);
    },
  });

  const isBusy = mutation.isPending || isPolling;
  const showProgress = isBusy || (progressPct > 0 && progressPct < 100);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Analyser un scénario</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            const f = e.dataTransfer.files?.[0];
            if (f && f.name.toLowerCase().endsWith(".pdf")) setFile(f);
          }}
          className="cursor-pointer rounded-md border-2 border-dashed border-slate-300 bg-slate-50 px-4 py-10 text-center hover:bg-slate-100 transition-colors"
        >
          <UploadIcon className="mx-auto h-8 w-8 text-slate-400" />
          <p className="mt-2 text-sm text-slate-700">
            Cliquez ou glissez un PDF ici
          </p>
          <p className="text-xs text-slate-500">
            Le serveur analysera le PDF avec le pipeline complet.
          </p>
          <input
            ref={inputRef}
            type="file"
            accept="application/pdf"
            hidden
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </div>

        {file && (
          <p className="text-xs text-slate-600">
            {file.name} - {(file.size / (1024 * 1024)).toFixed(2)} Mo
          </p>
        )}

        <Button
          className="w-full"
          disabled={!file || isBusy}
          onClick={() => file && mutation.mutate(file)}
        >
          {isBusy ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Analyse en cours...
            </>
          ) : (
            "Lancer l'analyse"
          )}
        </Button>

        {showProgress && (
          <div
            className="space-y-2"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(progressPct)}
            aria-label="Progression de l'analyse"
          >
            <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200">
              <div
                className="h-full bg-ccm-red transition-[width] duration-300 ease-out"
                style={{ width: `${Math.min(100, Math.max(2, progressPct))}%` }}
              />
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-700">{stageLabel}</span>
              <span className="font-mono tabular-nums text-slate-500">
                {Math.round(Math.min(100, progressPct))}%
              </span>
            </div>
            <p className="text-[11px] text-slate-400">
              {jobId
                ? `Job ${jobId.slice(0, 8)} — l'analyse se déroule côté serveur. La progression vient du backend.`
                : "Envoi du fichier en cours..."}
            </p>

            {/* Pipeline stepper — surfaces each internal step that is
                currently running underneath the progress bar. */}
            <ol className="mt-3 space-y-1.5 rounded-md border border-slate-200 bg-slate-50/60 p-3">
              {PIPELINE_STEPS.map((step) => {
                const status = getStepStatus(step, progressPct);
                const Icon = step.icon;
                return (
                  <li
                    key={step.key}
                    className={cn(
                      "flex items-center gap-2.5 text-[12px] transition-colors",
                      status === "done" && "text-emerald-700",
                      status === "active" && "text-ccm-red font-medium",
                      status === "pending" && "text-slate-400",
                    )}
                  >
                    <span
                      className={cn(
                        "inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full transition-colors",
                        status === "done" && "bg-emerald-100 text-emerald-700",
                        status === "active" && "bg-ccm-red/10 text-ccm-red",
                        status === "pending" && "bg-slate-200 text-slate-400",
                      )}
                    >
                      {status === "done" ? (
                        <CheckCircle2 className="h-3.5 w-3.5" />
                      ) : status === "active" ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Circle className="h-3.5 w-3.5" />
                      )}
                    </span>
                    <Icon
                      className={cn(
                        "h-3.5 w-3.5 shrink-0 transition-opacity",
                        status === "pending" && "opacity-60",
                      )}
                    />
                    <span className="truncate">{step.label}</span>
                    {status === "active" && (
                      <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-ccm-red/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-ccm-red">
                        en cours
                      </span>
                    )}
                  </li>
                );
              })}
            </ol>
          </div>
        )}

        {progressPct >= 100 && !isBusy && (
          <Alert variant="success">
            Analyse terminée — le rapport complet s'affiche ci-dessous.
          </Alert>
        )}
        {lastError && <Alert variant="error">{lastError}</Alert>}
      </CardContent>
    </Card>
  );
}
