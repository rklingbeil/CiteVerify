/** Mirrors backend data model */

export interface ExtractedCitation {
  citation_text: string;
  case_name: string;
  full_reference: string;
  quoted_text: string | null;
  characterization: string | null;
  context: string;
  position_start: number;
  position_end: number;
}

export interface LookupInfo {
  found: boolean;
  status: string;
  case_name: string;
  court: string;
  date_filed: string;
  url: string;
  source: string;
  has_opinion_text: boolean;
}

export interface VerificationInfo {
  status: "verified" | "warning" | "error" | "unverifiable";
  citation_exists: boolean;
  citation_format_correct: boolean;
  quote_accuracy: "exact" | "close" | "inaccurate" | null;
  quote_diff: string | null;
  actual_quote: string | null;
  characterization_accuracy: "accurate" | "misleading" | "unsupported" | null;
  characterization_explanation: string | null;
  confidence: number;
  reasoning: string | null;
  quote_status: string | null;
  characterization_status: string | null;
}

export interface CitationReport {
  extraction: ExtractedCitation;
  lookup: LookupInfo;
  verification: VerificationInfo;
}

export interface VerificationReport {
  id: string;
  filename: string;
  document_text: string;
  total_citations: number;
  verified: number;
  warnings: number;
  errors: number;
  unverifiable: number;
  citations: CitationReport[];
  extraction_warnings: string[];
  created_at: string;
}

export interface JobStatus {
  id: string;
  filename: string;
  status: "pending" | "running" | "completed" | "failed";
  progress: number;
  progress_message: string;
  report_id: string | null;
  error: string | null;
}

export interface UploadResponse {
  job_id: string;
  status: string;
  filename: string;
}
