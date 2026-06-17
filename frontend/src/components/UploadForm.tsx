import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  CheckCircle2,
  Clock,
  Loader2,
  Upload as UploadIcon,
  XCircle,
} from "lucide-react";
import { Button } from "./ui/button";
import { Alert } from "./ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import {
  analyzeMultiplePdfsAsync,
  analyzePdfAsync,
  fetchJobState,
  type AnalyzeJobState,
} from "@/lib/api";
import { useAnalysisStore } from "@/store/analysis";

// Délai entre deux requêtes de polling (ms). Court mais raisonnable pour
// que la barre de progression soit fluide sans noyer le backend.
const POLL_INTERVAL_MS = 1200;

// Limite UI : on plafonne la sélection à 5 PDF par analyse pour éviter
// de saturer la pipeline (chaque PDF lance un job complet en parallèle).
const MAX_FILES_PER_ANALYSIS = 5;

// Plafond avant qu'on déclare le job perdu côté UI (ms). Le worker peut
// quand même finir en arrière-plan et apparaître dans l'historique.
// Le compteur est (re)démarré quand le job passe en ``running`` — donc
// un job qui patiente en file n'est pas pénalisé par son attente.
const POLL_TIMEOUT_MS = 10 * 60 * 1000;

// Étape affichée tant que le backend n'a pas pris la main.
const FALLBACK_STAGE_LABEL = "Préparation de l'analyse";

// Lissage de la barre de progression : le backend ne reporte que des
// paliers grossiers (start / pipeline / persist / done), donc la valeur
// réelle peut rester à 40 % pendant 30-60 s. Pour éviter l'impression
// d'un freeze, on fait avancer la barre côté client de manière
// asymptotique : vite quand on est loin du plafond, plus lentement
// quand on s'en approche — la barre ne se fige donc jamais visuellement,
// et elle est toujours rattrapée si le backend remonte plus.
const SMOOTHING_INTERVAL_MS = 250;
const CREEP_APPROACH_RATIO = 0.02; // par tick : 2 % de la distance restante
const CREEP_MIN_PER_TICK = 0.05; // mouvement minimal — discret quand on s'approche du cap
const CREEP_HARD_CAP = 99; // tant que le backend n'a pas dit "completed", on continue d'avancer
// Au-delà de ce seuil on ralentit fortement la progression pour éviter
// que la barre n'atteigne visuellement 99 % alors que le backend n'est
// pas encore prêt à dire "completed".
const CREEP_SLOW_ZONE = 90;
const CREEP_SLOW_RATIO = 0.004; // ~0,4 % de la distance restante par tick

type JobUiStatus = "uploading" | "queued" | "running" | "completed" | "failed";

interface JobTracker {
  // Identifiant local stable utilisé comme clé React.
  uid: string;
  file: File;
  jobId: string | null;
  scenarioId: string | null;
  // Valeur réelle remontée par le backend.
  progressPct: number;
  // Valeur lissée affichée à l'utilisateur (creep entre deux updates).
  displayPct: number;
  stageLabel: string;
  status: JobUiStatus;
  error: string | null;
  // Stocke l'analyse complète une fois le job ``completed`` pour pouvoir
  // ouvrir le rapport sans refaire un round-trip.
  analysis: AnalyzeJobState["analysis"] | null;
}

export function UploadForm() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [jobs, setJobs] = useState<JobTracker[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const setResponse = useAnalysisStore((s) => s.setResponse);
  const setError = useAnalysisStore((s) => s.setError);
  const navigate = useNavigate();

  // Stocke les timers de polling par uid pour pouvoir les annuler à l'unmount
  // ou quand un job se termine.
  const pollTimersRef = useRef<Map<string, number>>(new Map());
  const pollStartedAtRef = useRef<Map<string, number>>(new Map());

  const clearTimerFor = (uid: string) => {
    const t = pollTimersRef.current.get(uid);
    if (t !== undefined) {
      window.clearTimeout(t);
      pollTimersRef.current.delete(uid);
    }
    pollStartedAtRef.current.delete(uid);
  };

  const clearAllTimers = () => {
    for (const t of pollTimersRef.current.values()) window.clearTimeout(t);
    pollTimersRef.current.clear();
    pollStartedAtRef.current.clear();
  };

  useEffect(() => () => clearAllTimers(), []);

  // Creep client : lisse la barre entre deux updates backend pour éviter
  // l'impression de freeze à 40 %.
  useEffect(() => {
    const id = window.setInterval(() => {
      setJobs((prev) => {
        let changed = false;
        const next = prev.map((j) => {
          if (j.status === "completed") {
            if (j.displayPct >= 100) return j;
            changed = true;
            return { ...j, displayPct: 100 };
          }
          if (j.status === "failed") return j;
          // FastAPI ``BackgroundTasks`` exécute les jobs séquentiellement :
          // les jobs encore ``queued`` n'ont pas démarré côté serveur — on
          // ne fait pas creep leur barre pour ne pas mentir à l'utilisateur.
          if (j.status === "queued" || j.status === "uploading") return j;
          // Progression asymptotique vers CREEP_HARD_CAP. La barre ralentit
          // au lieu de se figer brusquement, donc l'utilisateur perçoit
          // toujours un mouvement même si le backend ne remonte pas
          // d'update pendant plusieurs secondes.
          if (j.displayPct >= CREEP_HARD_CAP) return j;
          const remaining = CREEP_HARD_CAP - j.displayPct;
          // Deux régimes : avancée nette tant qu'on est sous la
          // ``slow zone``, puis micro-mouvements continus au-delà pour
          // que la barre ne paraisse jamais figée même sur un scénario
          // long. L'incrément reste plafonné par la distance restante.
          const inSlowZone = j.displayPct >= CREEP_SLOW_ZONE;
          const ratio = inSlowZone ? CREEP_SLOW_RATIO : CREEP_APPROACH_RATIO;
          const minTick = inSlowZone ? 0.02 : CREEP_MIN_PER_TICK;
          const increment = Math.min(
            remaining,
            Math.max(minTick, remaining * ratio),
          );
          changed = true;
          return {
            ...j,
            displayPct: Math.min(CREEP_HARD_CAP, j.displayPct + increment),
          };
        });
        return changed ? next : prev;
      });
    }, SMOOTHING_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, []);

  const addFiles = (incoming: FileList | File[] | null) => {
    if (!incoming) return;
    const pdfs = Array.from(incoming).filter((f) =>
      f.name.toLowerCase().endsWith(".pdf"),
    );
    if (pdfs.length === 0) return;
    let truncated = false;
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`));
      const merged = [...prev];
      for (const f of pdfs) {
        const key = `${f.name}:${f.size}`;
        if (!seen.has(key)) {
          if (merged.length >= MAX_FILES_PER_ANALYSIS) {
            truncated = true;
            break;
          }
          seen.add(key);
          merged.push(f);
        }
      }
      return merged;
    });
    if (truncated) {
      setSubmitError(
        `Limite atteinte : maximum ${MAX_FILES_PER_ANALYSIS} fichiers PDF par analyse.`,
      );
    }
  };

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const updateJob = (uid: string, patch: Partial<JobTracker>) => {
    setJobs((prev) =>
      prev.map((j) => (j.uid === uid ? { ...j, ...patch } : j)),
    );
  };

  const pollOnce = async (uid: string, jobId: string) => {
    let state: AnalyzeJobState;
    try {
      state = await fetchJobState(jobId);
    } catch {
      // Hoquet réseau : on retente au prochain tick au lieu d'abandonner.
      const t = window.setTimeout(
        () => pollOnce(uid, jobId),
        POLL_INTERVAL_MS,
      );
      pollTimersRef.current.set(uid, t);
      return;
    }

    const progress = Math.max(0, Math.min(100, state.progress_pct ?? 0));
    const stage = state.stage || FALLBACK_STAGE_LABEL;

    if (state.status === "completed") {
      clearTimerFor(uid);
      updateJob(uid, {
        progressPct: 100,
        displayPct: 100,
        stageLabel: "Analyse terminée",
        status: "completed",
        scenarioId: state.result_scenario_id ?? state.scenario_id ?? null,
        analysis: state.analysis ?? null,
      });
      return;
    }
    if (state.status === "failed") {
      clearTimerFor(uid);
      updateJob(uid, {
        status: "failed",
        error: state.error || "L'analyse a échoué côté serveur.",
      });
      return;
    }

    // Garde-fou anti-hang.
    const startedAt = pollStartedAtRef.current.get(uid) ?? Date.now();
    if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
      clearTimerFor(uid);
      updateJob(uid, {
        status: "failed",
        error:
          "L'analyse prend plus de temps que prévu. Elle continue côté " +
          "serveur — vérifiez l'historique plus tard.",
      });
      return;
    }

    // Si le job vient de passer de ``queued`` à ``running``, on
    // redémarre le compteur de timeout : sinon, un job qui attend
    // longtemps en file (FastAPI ``BackgroundTasks`` étant séquentiel)
    // serait tué au bout de POLL_TIMEOUT_MS alors que son analyse réelle
    // n'a même pas commencé.
    setJobs((prev) =>
      prev.map((j) => {
        if (j.uid !== uid) return j;
        const nextStatus =
          state.status === "running" ? "running" : "queued";
        if (j.status !== "running" && nextStatus === "running") {
          pollStartedAtRef.current.set(uid, Date.now());
        }
        return {
          ...j,
          progressPct: progress,
          displayPct: Math.max(j.displayPct, progress),
          stageLabel: stage,
          status: nextStatus,
        };
      }),
    );

    const t = window.setTimeout(
      () => pollOnce(uid, jobId),
      POLL_INTERVAL_MS,
    );
    pollTimersRef.current.set(uid, t);
  };

  const startPolling = (uid: string, jobId: string) => {
    pollStartedAtRef.current.set(uid, Date.now());
    const t = window.setTimeout(
      () => pollOnce(uid, jobId),
      POLL_INTERVAL_MS,
    );
    pollTimersRef.current.set(uid, t);
  };

  const handleSubmit = async () => {
    if (files.length === 0) return;
    setSubmitError(null);
    setIsSubmitting(true);
    clearAllTimers();

    // Initialise un tracker par fichier — l'ordre est conservé.
    const initial: JobTracker[] = files.map((f, i) => ({
      uid: `${Date.now()}-${i}-${f.name}`,
      file: f,
      jobId: null,
      scenarioId: null,
      progressPct: 2,
      displayPct: 2,
      stageLabel: FALLBACK_STAGE_LABEL,
      status: "uploading",
      error: null,
      analysis: null,
    }));
    setJobs(initial);

    try {
      if (files.length === 1) {
        // 1 fichier : on garde l'endpoint mono et l'auto-navigation vers
        // la page de résultats à la fin.
        const ack = await analyzePdfAsync(files[0]);
        const uid = initial[0].uid;
        const ackPct = Math.max(2, ack.progress_pct ?? 5);
        updateJob(uid, {
          jobId: ack.job_id,
          progressPct: ackPct,
          displayPct: ackPct,
          stageLabel: ack.stage || FALLBACK_STAGE_LABEL,
          status: "queued",
        });
        startPolling(uid, ack.job_id);
      } else {
        // N fichiers : un seul appel batch côté serveur, mais N jobs en
        // parallèle ensuite — chaque tracker poll son propre job.
        const batch = await analyzeMultiplePdfsAsync(files);
        const updated: JobTracker[] = initial.map((t, i) => {
          const ack = batch.jobs[i];
          const ackPct = Math.max(2, ack.progress_pct ?? 5);
          return {
            ...t,
            jobId: ack.job_id,
            scenarioId: ack.scenario_id,
            progressPct: ackPct,
            displayPct: ackPct,
            stageLabel: ack.stage || FALLBACK_STAGE_LABEL,
            status: "queued",
          };
        });
        setJobs(updated);
        for (const t of updated) {
          if (t.jobId) startPolling(t.uid, t.jobId);
        }
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setSubmitError(msg);
      setError(msg);
      setJobs([]);
    } finally {
      setIsSubmitting(false);
    }
  };

  // Si on a démarré pour 1 seul fichier, auto-navigate vers /results dès
  // que le job est ``completed`` — comportement historique préservé.
  useEffect(() => {
    if (jobs.length !== 1) return;
    const j = jobs[0];
    if (j.status === "completed" && j.analysis) {
      setResponse({
        success: true,
        scenario_id: j.scenarioId ?? "",
        analysis: j.analysis,
      });
      navigate("/results", { state: { keepResults: true } });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobs]);

  const openReport = (job: JobTracker) => {
    if (!job.analysis) return;
    setResponse({
      success: true,
      scenario_id: job.scenarioId ?? "",
      analysis: job.analysis,
    });
    navigate("/results", { state: { keepResults: true } });
  };

  const anyRunning = jobs.some(
    (j) =>
      j.status === "uploading" ||
      j.status === "queued" ||
      j.status === "running",
  );
  const isBusy = isSubmitting || anyRunning;
  const allDone = jobs.length > 0 && !anyRunning;
  const completedCount = jobs.filter((j) => j.status === "completed").length;
  const failedCount = jobs.filter((j) => j.status === "failed").length;

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
            addFiles(e.dataTransfer.files);
          }}
          className="cursor-pointer rounded-md border-2 border-dashed border-slate-300 bg-slate-50 px-4 py-10 text-center hover:bg-slate-100 transition-colors"
        >
          <UploadIcon className="mx-auto h-8 w-8 text-slate-400" />
          <p className="mt-2 text-sm text-slate-700">
            Cliquez ou glissez un ou plusieurs PDF ici
          </p>
          <p className="text-xs text-slate-500">
            Chaque PDF est analysé séparément par la pipeline complète.
          </p>
          <p className="mt-1 text-[11px] font-medium text-ccm-red">
            Max 5 fichiers PDF par analyse.
          </p>
          <input
            ref={inputRef}
            type="file"
            accept="application/pdf"
            multiple
            hidden
            onChange={(e) => {
              addFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </div>

        {files.length > 0 && jobs.length === 0 && (
          <ul className="space-y-1 rounded-md border border-slate-200 bg-slate-50/60 p-2">
            {files.map((f, idx) => (
              <li
                key={`${f.name}-${idx}`}
                className="flex items-center justify-between gap-2 text-xs text-slate-700"
              >
                <span className="truncate">
                  {idx + 1}. {f.name} —{" "}
                  <span className="text-slate-500">
                    {(f.size / (1024 * 1024)).toFixed(2)} Mo
                  </span>
                </span>
                <button
                  type="button"
                  className="shrink-0 text-slate-400 hover:text-ccm-red disabled:opacity-50"
                  onClick={(e) => {
                    e.stopPropagation();
                    removeFile(idx);
                  }}
                  disabled={isBusy}
                  aria-label={`Retirer ${f.name}`}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}

        <Button
          className="w-full"
          disabled={files.length === 0 || isBusy}
          onClick={handleSubmit}
        >
          {isBusy ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Analyse en cours...
            </>
          ) : files.length > 1 ? (
            `Lancer l'analyse de ${files.length} fichiers`
          ) : (
            "Lancer l'analyse"
          )}
        </Button>

        {submitError && <Alert variant="error">{submitError}</Alert>}

        {/* === Mode multi-fichiers : N barres en parallèle === */}
        {jobs.length > 1 && (
          <div className="space-y-3">
            <div className="flex items-center justify-between text-xs text-slate-600">
              <span>
                {completedCount} / {jobs.length} terminés
                {failedCount > 0 && (
                  <span className="ml-2 text-red-600">
                    · {failedCount} en échec
                  </span>
                )}
              </span>
              {allDone && (
                <Button
                  variant="outline"
                  onClick={() => {
                    clearAllTimers();
                    setJobs([]);
                    setFiles([]);
                  }}
                >
                  Nouvelle analyse
                </Button>
              )}
            </div>
            <ul className="space-y-2">
              {(() => {
                // Position dans la file d'attente : on numérote uniquement
                // les jobs encore en ``queued``, dans l'ordre du tableau.
                const queuePos = new Map<string, number>();
                let pos = 0;
                for (const j of jobs) {
                  if (j.status === "queued") {
                    pos += 1;
                    queuePos.set(j.uid, pos);
                  }
                }
                return jobs.map((j) => {
                  const waitPos = queuePos.get(j.uid) ?? 0;
                  return (
                <li
                  key={j.uid}
                  className="rounded-md border border-slate-200 bg-white p-3 space-y-2"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-slate-800">
                        {j.file.name}
                      </p>
                      <p className="text-[11px] text-slate-500">
                        {(j.file.size / (1024 * 1024)).toFixed(2)} Mo
                        {j.jobId && ` · Job ${j.jobId.slice(0, 8)}`}
                      </p>
                    </div>
                    <div className="shrink-0">
                      {j.status === "completed" ? (
                        <Button
                          onClick={() => openReport(j)}
                          disabled={!j.analysis}
                        >
                          <CheckCircle2 className="h-4 w-4" />
                          Voir le rapport
                        </Button>
                      ) : j.status === "failed" ? (
                        <span className="inline-flex items-center gap-1 text-xs font-medium text-red-600">
                          <XCircle className="h-4 w-4" />
                          Échec
                        </span>
                      ) : j.status === "queued" ? (
                        <span className="inline-flex items-center gap-1 text-xs font-medium text-slate-500">
                          <Clock className="h-4 w-4" />
                          {waitPos === 1
                            ? "Prochain"
                            : `${waitPos}ᵉ en attente`}
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-xs font-medium text-ccm-red">
                          <Loader2 className="h-4 w-4 animate-spin" />
                          {Math.round(j.displayPct)}%
                        </span>
                      )}
                    </div>
                  </div>
                  {j.status === "running" || j.status === "uploading" ? (
                    <>
                      <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
                        <div
                          className="h-full bg-ccm-red transition-[width] duration-500 ease-out"
                          style={{
                            width: `${Math.min(100, Math.max(2, j.displayPct))}%`,
                          }}
                        />
                      </div>
                      <p className="text-[11px] text-slate-600 truncate">
                        {j.stageLabel}
                      </p>
                    </>
                  ) : j.status === "queued" ? (
                    <>
                      <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-200" />
                      <p className="text-[11px] text-slate-500 truncate">
                        {waitPos === 1
                          ? "Démarre dès la fin de l'analyse en cours."
                          : `Démarre après ${waitPos - 1} autre${waitPos - 1 > 1 ? "s" : ""} analyse${waitPos - 1 > 1 ? "s" : ""} en attente.`}
                      </p>
                    </>
                  ) : null}
                  {j.status === "failed" && j.error && (
                    <p className="text-[11px] text-red-600">{j.error}</p>
                  )}
                </li>
                );
                });
              })()}
            </ul>
          </div>
        )}

        {/* === Mode mono-fichier : barre + stepper détaillé (historique) === */}
        {jobs.length === 1 && (
          <SingleJobProgress job={jobs[0]} />
        )}
      </CardContent>
    </Card>
  );
}

function SingleJobProgress({ job }: { job: JobTracker }) {
  if (job.status === "failed") {
    return <Alert variant="error">{job.error || "Analyse échouée."}</Alert>;
  }
  return (
    <div
      className="space-y-2"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(job.displayPct)}
      aria-label="Progression de l'analyse"
    >
      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200">
        <div
          className="h-full bg-ccm-red transition-[width] duration-500 ease-out"
          style={{ width: `${Math.min(100, Math.max(2, job.displayPct))}%` }}
        />
      </div>
      <div className="flex items-center justify-between text-xs">
        <span className="text-slate-700">{job.stageLabel}</span>
        <span className="font-mono tabular-nums text-slate-500">
          {Math.round(Math.min(100, job.displayPct))}%
        </span>
      </div>
      <p className="text-[11px] text-slate-400">
        {job.jobId
          ? `Job ${job.jobId.slice(0, 8)} — l'analyse se déroule côté serveur. La progression vient du backend.`
          : "Envoi du fichier en cours..."}
      </p>
    </div>
  );
}
