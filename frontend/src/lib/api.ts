import axios, { AxiosError } from "axios";
import type {
  Analysis,
  AuditLogEvent,
  HistoryItem,
  Statistics,
  User,
  UserRole,
} from "./types";

// En dev (vite serve), on laisse baseURL vide pour que les requetes partent
// vers la meme origine que la page (http://localhost:5173 ou 127.0.0.1:5173).
// Le proxy Vite (vite.config.ts) les forwarde alors vers FastAPI en
// same-origin -> pas de CORS, pas d'ERR_NETWORK lie au preflight.
// En prod (vite build), import.meta.env.DEV est false et VITE_API_BASE_URL
// (positionne au build) prend le relais.
export const DEFAULT_BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
  (import.meta.env.DEV ? "" : "http://127.0.0.1:8000");

export function getBaseUrl(): string {
  return localStorage.getItem("sia.apiBaseUrl") || DEFAULT_BASE_URL;
}

export function setBaseUrl(url: string): void {
  localStorage.setItem("sia.apiBaseUrl", url);
}

const AUTH_TOKEN_KEY = "sia.authToken";

export function getAuthToken(): string | null {
  try {
    return localStorage.getItem(AUTH_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setAuthToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(AUTH_TOKEN_KEY, token);
    else localStorage.removeItem(AUTH_TOKEN_KEY);
  } catch {
    /* localStorage unavailable — silently noop */
  }
}

function client() {
  const instance = axios.create({
    baseURL: getBaseUrl(),
    timeout: 600_000,
  });
  // Inject bearer token on every outgoing request when present.
  instance.interceptors.request.use((config) => {
    const token = getAuthToken();
    if (token) {
      config.headers = config.headers ?? {};
      (config.headers as Record<string, string>)[
        "Authorization"
      ] = `Bearer ${token}`;
    }
    return config;
  });
  // Auto-logout on 401: clear the token and let the UI's ProtectedRoute
  // bounce the user back to /login.
  instance.interceptors.response.use(
    (resp) => resp,
    (error: AxiosError) => {
      if (error.response?.status === 401) {
        setAuthToken(null);
        // Notify the rest of the app (Zustand store listens to this event).
        window.dispatchEvent(new CustomEvent("sia:unauthorized"));
      }
      return Promise.reject(error);
    }
  );
  return instance;
}

// ---------- Auth endpoints ----------

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in_minutes: number;
  username: string;
  user_id: string;
  role: UserRole | string;
  otp_used?: boolean;
}

export interface MeResponse {
  username: string;
  authenticated: boolean;
  issued_at: number;
  expires_at: number;
  auth_enabled: boolean;
}

export interface OTPSetupResponse {
  enabled: boolean;
  issuer: string;
  account: string;
  secret: string;
  provisioning_uri: string;
}

/** Signals the backend wants a 6-digit OTP code to complete the login. */
export class OTPRequiredError extends Error {
  readonly requiresOTP = true;
  readonly provisioningUri: string | null;
  readonly issuer: string | null;
  readonly account: string | null;
  readonly secret: string | null;
  constructor(
    message: string,
    detail?: {
      provisioning_uri?: unknown;
      issuer?: unknown;
      account?: unknown;
      secret?: unknown;
    },
  ) {
    super(message);
    this.name = "OTPRequiredError";
    this.provisioningUri =
      typeof detail?.provisioning_uri === "string"
        ? detail.provisioning_uri
        : null;
    this.issuer = typeof detail?.issuer === "string" ? detail.issuer : null;
    this.account =
      typeof detail?.account === "string" ? detail.account : null;
    this.secret =
      typeof detail?.secret === "string" ? detail.secret : null;
  }
}

export async function login(
  username: string,
  password: string,
  otpCode?: string,
): Promise<LoginResponse> {
  try {
    // Login must NOT carry the previous token (avoids a 401-trap loop).
    const bare = axios.create({ baseURL: getBaseUrl(), timeout: 15_000 });
    const payload: Record<string, string> = { username, password };
    if (otpCode) payload.otp_code = otpCode;
    const { data } = await bare.post<LoginResponse>(
      "/api/v1/auth/login",
      payload,
    );
    return data;
  } catch (e) {
    // Detect the structured "OTP required" 401 from the backend.
    if (e instanceof AxiosError && e.response?.status === 401) {
      const detail = (e.response.data as { detail?: unknown })?.detail;
      if (
        detail &&
        typeof detail === "object" &&
        (detail as Record<string, unknown>).requires_otp === true
      ) {
        const d = detail as Record<string, unknown>;
        throw new OTPRequiredError(
          (d.message as string) ?? "Code OTP requis",
          {
            provisioning_uri: d.provisioning_uri,
            issuer: d.issuer,
            account: d.account,
            secret: d.secret,
          },
        );
      }
    }
    throw new Error(extractError(e));
  }
}

export async function fetchMe(): Promise<MeResponse> {
  try {
    const { data } = await client().get<MeResponse>("/api/v1/auth/me");
    return data;
  } catch (e) {
    throw new Error(extractError(e));
  }
}

export async function fetchOTPSetup(): Promise<OTPSetupResponse> {
  try {
    const { data } = await client().get<OTPSetupResponse>(
      "/api/v1/auth/otp/setup",
    );
    return data;
  } catch (e) {
    throw new Error(extractError(e));
  }
}

function extractError(error: unknown): string {
  if (error instanceof AxiosError) {
    if (error.code === "ERR_NETWORK") {
      // Plusieurs causes possibles pour un ERR_NETWORK : on ajoute des
      // pistes pour que l'operateur sache ou regarder en premier.
      const baseUrl = getBaseUrl();
      const isHttps = window.location.protocol === "https:";
      const targetHttp = baseUrl.startsWith("http://");
      const hints = [
        `1) Le backend FastAPI tourne-t-il sur ${baseUrl} ?`,
        "2) Verifier l'onglet Reseau du navigateur (CORS, status reel).",
      ];
      if (isHttps && targetHttp) {
        hints.push(
          "3) La page est en HTTPS mais le backend en HTTP : Mixed " +
            "Content bloque par le navigateur.",
        );
      }
      return (
        `Impossible de joindre le backend FastAPI sur ${baseUrl}. ` +
        hints.join(" ")
      );
    }
    if (error.code === "ECONNABORTED") {
      return `Le backend n'a pas repondu a temps (timeout) sur ${getBaseUrl()}.`;
    }
    const data = error.response?.data as
      | { detail?: string | Array<{ msg?: string }>; error?: string }
      | undefined;
    if (typeof data?.detail === "string") {
      return data.error ? `${data.detail}: ${data.error}` : data.detail;
    }
    if (Array.isArray(data?.detail)) {
      return data!.detail.map((d) => d?.msg ?? JSON.stringify(d)).join("; ");
    }
    return error.message;
  }
  return error instanceof Error ? error.message : String(error);
}

export async function checkHealth(): Promise<{ message?: string; status?: string }> {
  try {
    const { data } = await client().get("/api/v1/health");
    return data;
  } catch (e) {
    throw new Error(extractError(e));
  }
}

// ---------- Asynchronous analyse (job + polling) ----------

export interface AnalyzeJobAck {
  success: boolean;
  job_id: string;
  scenario_id: string;
  status: "queued" | "running" | "completed" | "failed";
  stage: string;
  progress_pct: number;
}

export interface AnalyzeJobState extends AnalyzeJobAck {
  result_scenario_id: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  original_filename?: string;
  /** Embedded when ``status === "completed"`` so the client only does one
   *  round-trip to render the report. */
  analysis?: Analysis | null;
}

/** POST /uploads/analyze/async — accepts the file, enqueues the analyse,
 *  returns a job acknowledgement immediately (HTTP 202). */
export async function analyzePdfAsync(file: File): Promise<AnalyzeJobAck> {
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    throw new Error("Le fichier doit etre un PDF.");
  }
  const form = new FormData();
  form.append("file", file, file.name);
  try {
    // On NE fixe PAS Content-Type ici : axios le pose automatiquement avec
    // le boundary correct quand le body est un FormData.
    const { data } = await client().post<AnalyzeJobAck>(
      "/api/v1/uploads/analyze/async",
      form,
    );
    return data;
  } catch (e) {
    throw new Error(extractError(e));
  }
}

/** GET /uploads/jobs/{id} — current state of the analyse job. */
export async function fetchJobState(jobId: string): Promise<AnalyzeJobState> {
  try {
    const { data } = await client().get<AnalyzeJobState>(
      `/api/v1/uploads/jobs/${encodeURIComponent(jobId)}`
    );
    return data;
  } catch (e) {
    throw new Error(extractError(e));
  }
}

export async function fetchHistory(limit = 20): Promise<HistoryItem[]> {
  try {
    const { data } = await client().get("/api/v1/analysis/history", {
      params: { limit },
    });
    if (Array.isArray(data)) return data;
    for (const key of ["items", "history", "analyses", "results"] as const) {
      const value = (data as Record<string, unknown>)[key];
      if (Array.isArray(value)) return value as HistoryItem[];
    }
    return [];
  } catch (e) {
    throw new Error(extractError(e));
  }
}

const DEFAULT_STATS: Statistics = {
  total_analyses: 0,
  total_scenarios: 0,
  average_score: 0,
  plagiarism_detected: 0,
  moderation_alerts: 0,
  average_similarity_score: 0,
  average_profanity_score: 0,
  average_adult_content_score: 0,
  risk_counts: { low: 0, medium: 0, high: 0 },
  top_similar_scenarios: [],
  analyses_by_date: [],
  risky_analyses: [],
  status: "endpoint_unavailable",
};

export async function fetchStatistics(): Promise<Statistics> {
  try {
    const { data } = await client().get<Statistics>(
      "/api/v1/analysis/statistics"
    );
    return data ?? DEFAULT_STATS;
  } catch {
    return { ...DEFAULT_STATS, status: "backend_unreachable" };
  }
}

export interface AdvancedReportPassage {
  rank: number;
  source_filename: string;
  source_scenario_id: string;
  score_pct: number;
  current_position: string;
  source_position: string;
  current_excerpt: string;
  source_excerpt: string;
  overlap?: string | null;
  grouped_copies: number;
}

export interface AdvancedReport {
  scenario_id: string;
  generated_at: string;
  narrative: string;
  prompt: string;
  context: {
    scenario_id: string;
    risk_level: string;
    similarity_score_pct: number;
    total_matches: number;
    total_sources: number;
    passages: AdvancedReportPassage[];
    document_summary: Record<string, unknown>;
    moderation_summary: Record<string, unknown>;
    moroccan_constants_summary?: Record<string, unknown>;
    retrieval_status?: string;
    retrieval_reason?: string;
    retrieval_diagnostics?: Record<string, unknown>;
  };
  llm: {
    provider: string;
    model: string;
    used_fallback: boolean;
    error?: string | null;
  };
}

export async function generateAdvancedReport(
  scenarioId: string,
  analysis?: unknown,
): Promise<AdvancedReport> {
  try {
    const { data } = await client().post<AdvancedReport>(
      `/api/v1/analysis/${encodeURIComponent(scenarioId)}/advanced-report`,
      analysis ? { analysis } : {},
      // Local LLMs (Ollama / Mistral / Llama 8B) can take 60-180s on CPU,
      // and longer on cold start. Give the backend plenty of headroom
      // (≥ SIA_RAG_LLM_TIMEOUT_SECONDS server-side, default 180s).
      { timeout: 600_000 }
    );
    return data;
  } catch (e) {
    throw new Error(extractError(e));
  }
}

// ---------- Admin : users ----------

export async function fetchUsers(): Promise<User[]> {
  try {
    const { data } = await client().get<User[]>("/api/v1/users");
    return Array.isArray(data) ? data : [];
  } catch (e) {
    throw new Error(extractError(e));
  }
}

export interface CreateUserInput {
  username: string;
  password: string;
  role: UserRole;
}

export async function createUser(input: CreateUserInput): Promise<User> {
  try {
    const { data } = await client().post<User>("/api/v1/users", input);
    return data;
  } catch (e) {
    throw new Error(extractError(e));
  }
}

export async function updateUserRole(
  userId: string,
  role: UserRole,
): Promise<User> {
  try {
    const { data } = await client().patch<User>(
      `/api/v1/users/${encodeURIComponent(userId)}/role`,
      { role },
    );
    return data;
  } catch (e) {
    throw new Error(extractError(e));
  }
}

export async function resetUserPassword(
  userId: string,
  password: string,
): Promise<void> {
  try {
    await client().post(
      `/api/v1/users/${encodeURIComponent(userId)}/password`,
      { password },
    );
  } catch (e) {
    throw new Error(extractError(e));
  }
}

export async function deleteUser(userId: string): Promise<void> {
  try {
    await client().delete(`/api/v1/users/${encodeURIComponent(userId)}`);
  } catch (e) {
    throw new Error(extractError(e));
  }
}

// ---------- Admin : audit log ----------

export interface FetchAuditLogParams {
  limit?: number;
  user_id?: string;
  event_type?: string;
  since?: string;
}

export async function fetchAuditLog(
  params: FetchAuditLogParams = {},
): Promise<AuditLogEvent[]> {
  try {
    const { data } = await client().get<AuditLogEvent[]>(
      "/api/v1/audit-log",
      { params },
    );
    return Array.isArray(data) ? data : [];
  } catch (e) {
    throw new Error(extractError(e));
  }
}
