export interface DocumentStats {
  words_count?: number;
  word_count?: number;
  chunks_count?: number;
  chunk_count?: number;
  file_name?: string;
  original_filename?: string;
}

export interface PlagiarismMatch {
  similarity_score?: number;
  similarity?: number;
  score?: number;
  chunk_index?: number | string;
  page_number?: number | string | null;
  current_page_number?: number | string | null;
  source_page_number?: number | string | null;
  current_chunk_index?: number | string | null;
  source_chunk_index?: number | string | null;
  start_offset?: number | null;
  end_offset?: number | null;
  current_chunk_id?: string;
  source_chunk_id?: string;
  chunk_text?: string;
  matched_chunk_text?: string;
  matched_chunk_text_display?: string;
  matched_chunks?: number | string;
  matched_scenario_id?: string;
  matched_chunk_id?: string;
  filename?: string;
  original_filename?: string;
  stored_filename?: string;
  source?: string;
  duplicate?: boolean;
  match_type?: string;
  reason?: string;
  message?: string;
  snippet?: string;
  snippet_source?: string;
  overlap_text?: string | null;
  common_text?: string | null;
  grouped_copies?: number;
  match_quality_score?: number;
  boilerplate_ratio?: number;
  informative_word_count?: number;
  // Composite plagiarism scoring (cf. backend/utils/composite_scoring.py).
  // ``final_score`` est la note publique (cap anti faux-positif inclus).
  // ``risk`` est la classe calculée côté backend ; à privilégier sur tout
  // re-derivation depuis le pourcentage affiché.
  semantic_score?: number;
  lexical_score?: number;
  exact_overlap_score?: number;
  named_entity_overlap_score?: number;
  dialogue_overlap_score?: number;
  final_score?: number;
  display_score?: number;
  risk?: "low" | "medium" | "high" | "very_high" | string;
  is_false_positive?: boolean;
  debug_reason?: string | null;
}

export interface PlagiarismSource {
  source_scenario_id?: string | null;
  original_filename?: string | null;
  stored_filename?: string | null;
  best_score?: number;
  matches_count?: number;
  displayed_matches_count?: number;
  matches?: PlagiarismMatch[];
}

export interface DuplicateAnalysis {
  scenario_id?: string | null;
  original_filename?: string | null;
  stored_filename?: string | null;
  created_at?: string | null;
  file_hash?: string | null;
  text_hash?: string | null;
  source?: string | null;
}

export interface Plagiarism {
  score?: number;
  global_similarity_score?: number;
  matches?: PlagiarismMatch[];
  plagiarism_sources?: PlagiarismSource[];
  exact_duplicate?: boolean;
  duplicate_count?: number;
  duplicate_analyses?: DuplicateAnalysis[];
  total_matches?: number;
  displayed_matches?: number;
  total_sources?: number;
  displayed_sources?: number;
  is_truncated?: boolean;
  risk?: string;
  // MinHash (lexical fingerprinting) — exposé en parallèle du score
  // sémantique. ``best_source_score`` est le Jaccard MinHash maximal sur
  // l'ensemble des sources matchées : c'est l'indicateur de plagiat
  // textuel réel, beaucoup plus fiable que la similarité sémantique sur
  // des scénarios écrits dans le même style.
  minhash?: {
    engine?: string;
    global_similarity_score?: number;
    best_source_score?: number;
    score_percent?: number;
    matches_count?: number;
    sources_count?: number;
    plagiarism_detected?: boolean;
  };
}

export interface VulgarityMatch {
  word: string;
  language?: string;
  category?: string;
  snippet?: string;
  start?: number;
  end?: number;
  page_number?: number | string | null;
}

export interface NudityMatch {
  term?: string;
  word: string;
  language?: string;
  category?: string;
  snippet?: string;
  start?: number;
  end?: number;
  page_number?: number | string | null;
}

export interface Profanity {
  profanity_score?: number;
  detected_words?: string[];
  vulgarity_matches?: VulgarityMatch[];
  vulgarity_found_words?: string[];
  vulgarity_categories?: string[];
}

export interface AdultContent {
  adult_content_score?: number;
  risk_level?: string;
  detected_terms?: string[];
  nudity_matches?: NudityMatch[];
}

export interface RagReport {
  summary?: string;
  risk_level?: string;
  risk_justification?: string;
  recommendations?: string[];
  plagiarism_explanation?: string;
  moderation_explanation?: string;
  conclusion?: string;
  generated_report?: string;
  /**
   * Set when the rag_report.risk_level was raised by a downstream
   * pipeline (e.g. the Moroccan constants compliance check). The
   * narrative summary above is still the template's original wording —
   * it may mention a lower level than the floored badge.
   */
  risk_level_floored_by?: string;
}

export type StrictMatchVerdict =
  | "identical"
  | "near_identical"
  | "highly_similar"
  | "different";

export interface StrictMatchedAnalysis {
  scenario_id: string;
  original_filename?: string | null;
  stored_filename?: string | null;
  analyzed_at?: string | null;
  risk_level?: string | null;
  file_hash?: string | null;
  text_hash?: string | null;
  similarity_score: number;
  score_percent: number;
  match_type: string;
}

export interface StrictMatch {
  verdict: StrictMatchVerdict;
  score: number;
  score_percent: number;
  match_type: "file_hash" | "text_hash" | "global_jaccard" | "none";
  is_renewal_candidate: boolean;
  reason: string;
  candidates_compared: number;
  matched_analysis: StrictMatchedAnalysis | null;
  extras: StrictMatchedAnalysis[];
  status?: string;
}

export type MoroccanCategoryKey =
  | "islam"
  | "national_unity"
  | "monarchy"
  | "democratic_choice";

export interface MoroccanFlag {
  category: MoroccanCategoryKey | string;
  /** Risk vocab. May be the French scale or the legacy English one. */
  severity: string;
  chunk_index?: number | null;
  /** Numéro de page du PDF (attaché côté backend via chunk_metadata). */
  page_number?: number | null;
  evidence?: string;
  explanation?: string;
}

export interface MoroccanCategoryStats {
  count: number;
  risk_level: string;
  score: number;
}

export interface MoroccanMention {
  category: MoroccanCategoryKey | string;
  subject?: string;
  chunk_index?: number | null;
  /** Numéro de page du PDF (attaché côté backend via chunk_metadata). */
  page_number?: number | null;
  evidence?: string;
  /** Severity bucket when this mention is also flagged, ``null`` otherwise. */
  flagged_severity?: string | null;
}

export interface MoroccanConstants {
  score?: number;
  risk_level?: string;
  flags?: MoroccanFlag[];
  categories?: Partial<Record<MoroccanCategoryKey, MoroccanCategoryStats>> &
    Record<string, MoroccanCategoryStats>;
  mentions?: MoroccanMention[];
  mentions_total?: number;
  mentions_truncated?: boolean;
  mentions_by_category?: Partial<Record<MoroccanCategoryKey, number>> &
    Record<string, number>;
}

export interface Analysis {
  scenario_id?: string;
  analysis_timestamp?: string;
  document_stats?: DocumentStats;
  plagiarism?: Plagiarism;
  profanity?: Profanity;
  adult_content?: AdultContent;
  strict_match?: StrictMatch;
  rag_report?: RagReport;
  moroccan_constants?: MoroccanConstants;
  status?: string;
  warnings?: string[];
}

export interface AnalyzeResponse {
  success: boolean;
  scenario_id: string;
  analysis: Analysis;
}

export interface HistoryItem {
  scenario_id?: string;
  analysis_timestamp?: string;
  created_at?: string;
  filename?: string;
  similarity_score?: number;
  risk_level?: string;
  result?: Analysis;
  analysis?: Analysis;
  document_stats?: DocumentStats;
  plagiarism?: Plagiarism;
  profanity?: Profanity;
  adult_content?: AdultContent;
  moroccan_constants?: MoroccanConstants;
  rag_report?: RagReport;
}

// ---------- Admin : users & audit log ----------

export type UserRole = "admin" | "reviewer" | "viewer";

export interface User {
  user_id: string;
  username: string;
  role: UserRole | string;
  created_at?: string;
  last_login_at?: string | null;
  disabled?: boolean;
}

export interface AuditLogEvent {
  event_id: string;
  event_type: string;
  user_id?: string | null;
  username?: string | null;
  target_id?: string | null;
  ip?: string | null;
  details?: Record<string, unknown>;
  timestamp: string;
}

export interface Statistics {
  total_analyses?: number;
  total_scenarios?: number;
  average_score?: number;
  plagiarism_detected?: number;
  moderation_alerts?: number;
  average_similarity_score?: number;
  average_profanity_score?: number;
  average_adult_content_score?: number;
  risk_counts?: {
    low?: number;
    medium?: number;
    high?: number;
    very_high?: number;
  };
  top_similar_scenarios?: Array<Record<string, unknown>>;
  analyses_by_date?: Array<{ date: string; count: number }>;
  risky_analyses?: Array<Record<string, unknown>>;
  status?: string;
}
