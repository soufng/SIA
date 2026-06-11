import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Analysis, AnalyzeResponse } from "@/lib/types";

interface AnalysisStore {
  response: AnalyzeResponse | null;
  analysis: Analysis | null;
  scenarioId: string | null;
  lastError: string | null;
  setResponse: (r: AnalyzeResponse) => void;
  setAnalysis: (a: Analysis, scenarioId?: string | null) => void;
  setError: (e: string | null) => void;
  reset: () => void;
}

export const useAnalysisStore = create<AnalysisStore>()(
  persist(
    (set) => ({
      response: null,
      analysis: null,
      scenarioId: null,
      lastError: null,
      setResponse: (r) =>
        set({
          response: r,
          analysis: r.analysis ?? null,
          scenarioId: r.scenario_id ?? r.analysis?.scenario_id ?? null,
          lastError: null,
        }),
      setAnalysis: (a, scenarioId) =>
        set({
          response: null,
          analysis: a,
          scenarioId: scenarioId ?? a.scenario_id ?? null,
          lastError: null,
        }),
      setError: (e) => set({ lastError: e }),
      reset: () =>
        set({
          response: null,
          analysis: null,
          scenarioId: null,
          lastError: null,
        }),
    }),
    { name: "spm.analysis" }
  )
);
