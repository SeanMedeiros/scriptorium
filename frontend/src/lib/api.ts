// Typed client for the SCRIPTORIUM REST API.
// The access token lives only in memory; the refresh token is an HttpOnly cookie
// the browser sends to /api/v1/auth/*. A 401 triggers one silent refresh + retry.

export type ApiErrorBody = { code: string; message: string; details?: unknown; request_id?: string };

export class ApiError extends Error {
  constructor(public status: number, public body: ApiErrorBody) {
    super(body.message);
  }
  get code() {
    return this.body.code;
  }
}

type TokenListener = (token: string | null) => void;

let accessToken: string | null = null;
let refreshing: Promise<string | null> | null = null;
const listeners = new Set<TokenListener>();

export function setAccessToken(token: string | null) {
  accessToken = token;
  listeners.forEach((l) => l(token));
}
export function getAccessToken() {
  return accessToken;
}
export function onTokenChange(l: TokenListener) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

const BASE = "/api/v1";

async function parseError(res: Response): Promise<ApiError> {
  let body: ApiErrorBody = { code: "http_error", message: res.statusText || "Request failed" };
  try {
    const json = await res.json();
    if (json?.error) body = json.error;
  } catch {
    /* non-JSON error */
  }
  return new ApiError(res.status, body);
}

export async function refreshSession(fetchImpl: typeof fetch = fetch): Promise<string | null> {
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const res = await fetchImpl(`${BASE}/auth/refresh`, { method: "POST", credentials: "same-origin" });
        if (!res.ok) {
          setAccessToken(null);
          return null;
        }
        const data = (await res.json()) as { access_token: string };
        setAccessToken(data.access_token);
        return data.access_token;
      } catch {
        setAccessToken(null);
        return null;
      } finally {
        refreshing = null;
      }
    })();
  }
  return refreshing;
}

export async function api<T = unknown>(
  path: string,
  init: RequestInit & { json?: unknown; retry?: boolean; fetchImpl?: typeof fetch } = {},
): Promise<T> {
  const { json, retry = true, fetchImpl = fetch, ...rest } = init;
  const headers = new Headers(rest.headers);
  if (json !== undefined) headers.set("Content-Type", "application/json");
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const res = await fetchImpl(`${BASE}${path}`, {
    ...rest,
    headers,
    credentials: "same-origin",
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (res.status === 401 && retry && !path.startsWith("/auth/")) {
    const token = await refreshSession(fetchImpl);
    if (token) return api<T>(path, { ...init, retry: false });
  }
  if (!res.ok) throw await parseError(res);
  if (res.status === 204 || res.status === 202) {
    const text = await res.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }
  return (await res.json()) as T;
}

// ---------- types mirrored from the OpenAPI schema ----------
export type InstitutionRole = "institution_admin" | "member";
export type ProjectRole = "principal_investigator" | "researcher" | "transcription_editor" | "reviewer" | "read_only";

export interface Me {
  id: string;
  email: string;
  full_name: string;
  orcid: string | null;
  preferred_locale: string;
  is_platform_admin: boolean;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
  institutions: { institution_id: string; institution_name: string; role: InstitutionRole }[];
}

export interface Project {
  id: string;
  title: string;
  slug: string;
  description: string | null;
  institution_id: string | null;
  institution_name: string | null;
  owner_id: string;
  owner_name: string | null;
  languages: string[];
  period_start: number | null;
  period_end: number | null;
  funding: string | null;
  is_archived: boolean;
  created_at: string;
  updated_at: string;
  collection_count: number;
  member_count: number;
  permissions: string[];
}

export interface Collection {
  id: string;
  project_id: string;
  title: string;
  description: string | null;
  archival_reference: string | null;
  holding_institution: string | null;
  dc_metadata: Record<string, string | string[]>;
  created_at: string;
  updated_at: string;
}

export interface Institution {
  id: string;
  name: string;
  slug: string;
  country: string | null;
  ror_id: string | null;
  website: string | null;
  created_at: string;
}

export interface AuditEntry {
  id: number;
  created_at: string;
  actor_id: string | null;
  actor_email: string | null;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  institution_id: string | null;
  project_id: string | null;
  ip_address: string | null;
  details: Record<string, unknown>;
}

export interface Dashboard {
  projects: number;
  collections: number;
  researchers: number;
  institutions: number;
  documents: number;
  pages: number;
  processed_pages: number;
  recognised_lines: number;
  jobs: Record<string, number>;
  validated_models: number;
  storage_bytes: number;
  line_status: { unreviewed: number; corrected: number; reviewed: number };
  quality?: { ground_truth_pages: number; evaluation_results: number; methodology_version: string; by_model: EvalGroup[] };
  recent_activity: AuditEntry[];
  unavailable: Record<string, string>;
}

export interface Paged<T> {
  total: number;
  limit: number;
  offset: number;
  items: T[];
}

export interface SessionInfo {
  id: string;
  family_id: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string;
  ip_address: string | null;
  user_agent: string | null;
  current: boolean;
}

export interface Member {
  user_id: string;
  email: string;
  full_name: string;
  role: string;
}

// ---------- Phase 2: documents, pages, models, jobs, outputs ----------
export interface Asset {
  id: string;
  kind: "original" | "page_image" | "derived";
  sha256: string;
  size_bytes: number;
  mime_type: string;
  original_filename: string | null;
  width: number | null;
  height: number | null;
  dpi: number | null;
  parent_asset_id: string | null;
  derivation: Record<string, unknown>;
  created_at: string;
  url: string | null;
}

export type JobStatus = "queued" | "processing" | "completed" | "failed" | "cancelled";

export interface PageInfo {
  id: string;
  document_id: string;
  sequence: number;
  label: string | null;
  source_page_number: number | null;
  image_asset_id: string;
  image: Asset | null;
  latest_job_status: JobStatus | null;
  recognition_count: number;
}

export interface DocumentInfo {
  id: string;
  collection_id: string;
  title: string;
  description: string | null;
  document_type: string | null;
  script_type: "manuscript" | "print" | "mixed";
  languages: string[];
  date_text: string | null;
  date_start: number | null;
  date_end: number | null;
  archival_reference: string | null;
  provenance: string | null;
  notes: string | null;
  dc_metadata: Record<string, string | string[]>;
  ingest_status: "pending" | "ready" | "failed";
  ingest_error: { code: string; message: string } | null;
  source_asset_id: string | null;
  created_at: string;
  updated_at: string;
  page_count: number;
  source: Asset | null;
  project_id: string;
  project_title: string;
  collection_title: string;
  pages?: PageInfo[];
}

export interface ModelVersionInfo {
  id: string;
  model_id: string;
  model_slug: string;
  model_name: string;
  task: "recognition" | "segmentation";
  source: string;
  version: string;
  doi: string | null;
  license_id: string | null;
  status: string;
  sha256: string | null;
  source_checksum: string | null;
  size_bytes: number | null;
  unicode_normalization: string | null;
  kraken_version_validated: string | null;
  installed_at: string | null;
  validated_at: string | null;
  is_pinned: boolean;
  source_metadata?: Record<string, unknown>;
  file_metadata?: Record<string, unknown>;
  install_log?: { ts: string; message: string; [k: string]: unknown }[];
}

export interface JobInfo {
  id: string;
  batch_id: string | null;
  project_id: string;
  document_id: string;
  page_id: string;
  job_type: "segment" | "recognize" | "segment_recognize";
  status: JobStatus;
  progress: number;
  stage: string | null;
  submitted_at: string;
  started_at: string | null;
  completed_at: string | null;
  attempts: number;
  retry_of_id: string | null;
  worker: string | null;
  error: { code: string; message: string; stage?: string } | null;
  output_segmentation_id: string | null;
  output_recognition_id: string | null;
  document_title: string | null;
  page_sequence: number | null;
  recognition_model: string | null;
  config: Record<string, unknown>;
  log?: { ts: string; level: string; message: string; [k: string]: unknown }[];
}

export interface JobList extends Paged<JobInfo> {
  counts: Record<JobStatus, number>;
}

export interface LineGeom {
  id: string;
  region_id: string | null;
  order_index: number;
  baseline: [number, number][] | null;
  boundary: [number, number][] | null;
  bbox: [number, number, number, number];
}

export interface SegmentationInfo {
  id: string;
  page_id: string;
  job_id: string | null;
  source: string;
  model_version_id: string | null;
  input_asset_id: string;
  text_direction: string;
  config: { preprocessing?: { operations: { op: string; [k: string]: unknown }[] }; [k: string]: unknown };
  software: Record<string, string>;
  duration_ms: number | null;
  created_at: string;
  input_asset: Asset | null;
  regions: { id: string; region_type: string; order_index: number; boundary: [number, number][] }[];
  lines: LineGeom[];
}

export interface RecognitionInfo {
  id: string;
  segmentation_id: string;
  page_id: string;
  job_id: string | null;
  model_version_id: string;
  input_asset_id: string;
  software: Record<string, string>;
  duration_ms: number | null;
  line_count: number;
  char_count: number;
  created_at: string;
  model: string | null;
  unicode_normalization: string | null;
  confidence_note: string;
  lines: { line_id: string; order_index: number; text: string; mean_confidence: number | null }[];
}

/** Multipart upload (FormData) — the JSON helper cannot be used for files. */
export async function uploadFiles<T>(path: string, form: FormData): Promise<T> {
  const doFetch = () =>
    fetch(`/api/v1${path}`, {
      method: "POST",
      body: form,
      credentials: "same-origin",
      headers: getAccessToken() ? { Authorization: `Bearer ${getAccessToken()}` } : {},
    });
  let res = await doFetch();
  if (res.status === 401 && (await refreshSession())) res = await doFetch();
  if (!res.ok) {
    let body: ApiErrorBody = { code: "http_error", message: res.statusText };
    try {
      const j = await res.json();
      if (j?.error) body = j.error;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, body);
  }
  return (await res.json()) as T;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}

// ---------- Phase 5: downloads, evaluation, comparison, exports, reports ----------
export interface Download {
  filename: string;
  blob: Blob;
  validation: { valid: boolean; schema: string | null; method: string; errors: string[] } | null;
}

/** Authenticated file download (refreshes the session once on 401). Errors raise ApiError. */
export async function download(path: string, retry = true): Promise<Download> {
  const headers = new Headers();
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const res = await fetch(`${BASE}${path}`, { headers, credentials: "same-origin" });
  if (res.status === 401 && retry) {
    if (await refreshSession()) return download(path, false);
  }
  if (!res.ok) throw await parseError(res);
  const cd = res.headers.get("Content-Disposition") ?? "";
  const filename = /filename="([^"]+)"/.exec(cd)?.[1] ?? "download";
  const v = res.headers.get("X-Scriptorium-Validation");
  return { filename, blob: await res.blob(), validation: v ? JSON.parse(v) : null };
}

export function saveBlob(d: { blob: Blob; filename: string }) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(d.blob);
  a.download = d.filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

export interface Methodology {
  version: string;
  unicode: "NFC" | "NFD" | "NFKC" | "none";
  casefold: boolean;
  remove_punctuation: boolean;
  remove_combining_marks: boolean;
  exclude_gap_lines: boolean;
}

export interface GroundTruthPage {
  id: string;
  page_id: string;
  page_sequence: number;
  document_id: string;
  document_title: string;
  segmentation_id: string;
  split: "train" | "validation" | "test";
  source: string;
  source_details: Record<string, unknown>;
  unicode_form: string | null;
  note: string | null;
  line_count: number;
  char_count: number;
  created_at: string;
  withdrawn: boolean;
}

export interface EvalDataset {
  id: string;
  project_id: string;
  name: string;
  description: string | null;
  created_at: string;
  splits: Record<string, number>;
  pages: number;
  lines: number;
  ground_truth?: GroundTruthPage[];
}

export interface Aggregate {
  pages: number;
  ref_chars: number;
  char_errors: number;
  ref_words: number;
  word_errors: number;
  cer: number | null;
  wer: number | null;
  char_accuracy: number | null;
  word_accuracy: number | null;
}

export interface EvalRun {
  id: string;
  dataset_id: string;
  model_version_id: string;
  model: string;
  split: string;
  methodology: Methodology;
  page_count: number;
  batch_id: string;
  jobs: Record<string, number>;
  scored_pages: number;
  status: "running" | "completed";
  aggregate: Aggregate | null;
  created_at: string;
}

export interface EvalRow {
  result_id: string;
  run_id: string | null;
  recognition_id: string;
  gt_page_id: string;
  split: string;
  dataset: string;
  model: string;
  document: string;
  page: string;
  page_id: string;
  collection: string;
  language: string | null;
  period: string | null;
  script_type: string | null;
  document_type: string | null;
  alignment: "line" | "page";
  methodology: Methodology;
  ref_chars: number;
  char_errors: number;
  ref_words: number;
  word_errors: number;
  cer: number | null;
  wer: number | null;
  lines_matched: number;
  lines_missing: number;
  created_at: string;
  note: string | null;
}

export interface EvalGroup extends Aggregate {
  key: string;
  models: string[];
  mixed_methodology: boolean;
  alignments: string[];
}

export interface EvalResults {
  group_by: string;
  groups: EvalGroup[];
  rows: EvalRow[];
  methodology_version: string;
}

export interface LineScoreDetail {
  line_id: string;
  ref: string;
  hyp: string | null;
  ref_chars: number;
  char_errors: number;
  ref_words: number;
  word_errors: number;
}

export interface ComparisonModel {
  model_version_id: string;
  model: string;
  slug: string;
  version: string;
  doi: string | null;
  license: string | null;
  unicode_normalization: string | null;
  job_id: string | null;
  job_status: string | null;
  job_error: { code: string; message: string } | null;
  progress: number | null;
  recognition_id: string | null;
  duration_ms: number | null;
  kraken: string | null;
  line_count: number | null;
  char_count: number | null;
  score: (Aggregate & { alignment: string; lines_matched: number; lines_missing: number; methodology: Methodology }) | null;
}

export interface ComparisonInfo {
  id: string;
  page_id: string;
  page_sequence: number;
  document_id: string;
  document_title: string;
  project_id: string;
  segmentation_id: string;
  segmentation_source: string;
  created_at: string;
  models: ComparisonModel[];
  lines: {
    key: string;
    order_index: number;
    bbox: number[];
    outputs: Record<string, { text: string; line_recognition_id: string; mean_confidence: number | null } | null>;
  }[];
  pairwise: { a: string; b: string; char_distance: number; normalised: number; lines_differing: number }[];
  ground_truth: { gt_page_id: string; aligned: boolean; note: string | null; lines: Record<string, string> | null } | null;
  note: string;
}

export interface ExportJob {
  id: string;
  project_id: string;
  scope: "document" | "collection" | "training-data";
  scope_id: string;
  formats: string[];
  options: { layer: string; include_uncorrected: boolean; title?: string };
  status: "queued" | "processing" | "completed" | "failed";
  size_bytes: number | null;
  sha256: string | null;
  summary: {
    files?: number;
    documents?: number;
    pages?: number;
    uncorrected_pages?: number;
    skipped_pages?: { document: string; page: number; reason: string }[];
    validation?: { checked: number; invalid: string[] };
  };
  error: { code: string; message: string } | null;
  created_at: string;
  completed_at: string | null;
}

export interface ProjectReport {
  project: { id: string; title: string };
  counts: Record<string, number>;
  jobs: Record<string, number>;
  lines: { unreviewed: number; corrected: number; reviewed: number; uncertain: number; illegible: number; total: number; reviewed_share: number | null };
  models: { model: string; model_version_id: string; doi: string | null; recognitions: number; pages: number }[];
  contributors: { name: string; email: string; revisions: number; lines_changed: number; last_activity: string | null }[];
  evaluation: { methodology_version: string; results: number; by_model: EvalGroup[]; by_period: EvalGroup[]; by_language: EvalGroup[] };
}
