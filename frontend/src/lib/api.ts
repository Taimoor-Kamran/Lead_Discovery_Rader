/**
 * The one client every request goes through.
 *
 * - attaches the in-memory access token;
 * - on a 401, calls `POST /auth/refresh` **once** (the refresh cookie rides along with
 *   `credentials: "include"`) and retries the request with the new token;
 * - when the refresh fails too, clears the session and sends the browser to `/login`.
 *
 * Every path and body is typed from `api-types.ts`, generated from the backend's OpenAPI
 * document by `make api-types`. `make check` fails when that file is out of date.
 */

import type { components } from "./api-types";
import { clearAccessToken, getAccessToken, setAccessToken } from "./session";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

type Schemas = components["schemas"];
export type Me = Schemas["UserRead"];
export type Role = Schemas["Role"];
export type TokenResponse = Schemas["TokenResponse"];
export type Health = { status: "ok" | "degraded"; db: boolean; redis: boolean };
export type QueueItem = Schemas["QueueItem"];
export type QueueOpportunity = Schemas["QueueOpportunity"];
export type QueuePage = Schemas["Page_QueueItem_"];
export type ReviewDetail = Schemas["ReviewDetail"];
export type ReviewOpportunity = Schemas["ReviewOpportunity"];
export type ReviewDecisionRead = Schemas["ReviewDecisionRead"];
export type ReviewRequest = Schemas["ReviewRequest"];
export type DecidedOpportunity = Schemas["DecidedOpportunity"];
export type BatchReviewRequest = Schemas["BatchReviewRequest"];
export type BatchReviewResult = Schemas["BatchReviewResult"];
export type UndoResult = Schemas["UndoResult"];
export type Decision = Schemas["Decision"];
export type ReviewStatus = Schemas["ReviewStatus"];
export type LeadRead = Schemas["LeadRead"];
export type LeadPage = Schemas["Page_LeadRead_"];
export type LeadDetail = Schemas["LeadDetail"];
export type MatchCandidate = Schemas["MatchCandidateDetail"];
export type MatchCandidatePage = Schemas["Page_MatchCandidateDetail_"];
export type SuppressionRead = Schemas["SuppressionRead"];
export type SuppressionCreate = Schemas["SuppressionCreate"];
export type SuppressionPage = Schemas["Page_SuppressionRead_"];
export type UserPage = Schemas["Page_UserRead_"];
export type AISummary = Schemas["AISummaryRead"];
export type SourceRecord = Schemas["SourceRecordRead"];
export type LinkedProfiles = Schemas["LinkedProfilesRead"];
export type LinkedProfile = Schemas["LinkedProfileRead"];
export type CrmStatus = Schemas["CrmStatusRead"];
export type CrmLead = Schemas["CrmLeadRead"];
export type CrmLeadPage = Schemas["Page_CrmLeadRead_"];
export type CrmLeadStatus = Schemas["CrmLeadStatus"];
export type CrmLeadBlock = Schemas["CrmLeadStatusRead"];
export type CrmSyncAttempt = Schemas["CrmSyncAttemptRead"];
export type SyncAllResult = Schemas["SyncAllResult"];
export type UserRead = Schemas["UserRead"];
export type UserCreate = Schemas["UserCreate"];
export type IndustryOption = Schemas["IndustryOption"];
export type CostEstimate = Schemas["CostEstimate"];
export type EstimateRequest = Schemas["EstimateRequest"];
export type SearchJobCreate = Schemas["SearchJobCreate"];
export type SearchJobRead = Schemas["SearchJobRead"];
export type SearchJobListItem = Schemas["SearchJobListItem"];
export type SearchJobPage = Schemas["Page_SearchJobListItem_"];
export type JobRunRead = Schemas["JobRunRead"];
export type PipelineRead = Schemas["PipelineRead"];
export type PipelineStage = Schemas["PipelineStage"];
export type SourceRead = Schemas["SourceRead"];
export type HealthReport = Schemas["HealthReport"];
export type AlertRead = Schemas["AlertRead"];
export type ErrorEnvelope = {
  error: { code: string; message: string; request_id: string; details?: Record<string, unknown> };
};

export class ApiError extends Error {
  readonly code: string;
  readonly requestId: string;
  readonly status: number;
  readonly details: Record<string, unknown>;

  constructor(
    status: number,
    code: string,
    message: string,
    requestId: string,
    details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.details = details;
  }
}

/** What happens when the session cannot be recovered. Replaceable so tests can observe it. */
let onSessionLost: () => void = () => {
  if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    window.location.assign("/login");
  }
};

export function setSessionLostHandler(handler: () => void): void {
  onSessionLost = handler;
}

type Query = Record<string, string | number | boolean | null | undefined>;

export function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const encoded = params.toString();
  return encoded ? `${path}?${encoded}` : path;
}

async function send(path: string, init: RequestInit, token: string | null): Promise<Response> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init.headers as Record<string, string> | undefined) ?? {}),
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetch(`${API_BASE_URL}${path}`, { ...init, credentials: "include", headers });
}

async function parse<T>(response: Response): Promise<T> {
  if (!response.ok) throw await toApiError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function toApiError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as Partial<ErrorEnvelope>;
    const error = body.error;
    if (error) {
      return new ApiError(
        response.status,
        error.code,
        error.message,
        error.request_id,
        (error.details ?? {}) as Record<string, unknown>,
      );
    }
  } catch {
    // Fall through to the generic message below.
  }
  return new ApiError(response.status, "http_error", response.statusText, "");
}

const AUTH_PATHS = ["/auth/login", "/auth/refresh", "/auth/logout"];

let refreshing: Promise<string | null> | null = null;

/** One refresh at a time: concurrent 401s share the same call. */
export async function refreshAccessToken(): Promise<string | null> {
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const response = await send("/auth/refresh", { method: "POST" }, null);
        if (!response.ok) return null;
        const body = (await response.json()) as TokenResponse;
        setAccessToken(body.access_token);
        return body.access_token;
      } catch {
        return null;
      } finally {
        refreshing = null;
      }
    })();
  }
  return refreshing;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const first = await send(path, init, getAccessToken());
  const isAuthPath = AUTH_PATHS.some((p) => path.startsWith(p));
  if (first.status !== 401 || isAuthPath) return parse<T>(first);

  const token = await refreshAccessToken();
  if (!token) {
    clearAccessToken();
    onSessionLost();
    throw await toApiError(first);
  }
  return parse<T>(await send(path, init, token));
}

function get<T>(path: string, query?: Query): Promise<T> {
  return request<T>(withQuery(path, query));
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
}

function patch<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
}

// --- auth ------------------------------------------------------------------------------

export function getHealth(): Promise<Health> {
  return get<Health>("/health");
}

export async function login(email: string, password: string): Promise<TokenResponse> {
  const body = await post<TokenResponse>("/auth/login", { email, password });
  setAccessToken(body.access_token);
  return body;
}

export function getMe(): Promise<Me> {
  return get<Me>("/auth/me");
}

export async function logout(): Promise<void> {
  try {
    await post<void>("/auth/logout");
  } finally {
    clearAccessToken();
  }
}

/** Replace your own password. The API retires every other session and hands back a new one. */
export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<TokenResponse> {
  const body = await post<TokenResponse>("/auth/change-password", {
    current_password: currentPassword,
    new_password: newPassword,
  });
  setAccessToken(body.access_token);
  return body;
}

// --- users (admin) -----------------------------------------------------------------------

export function getUsers(cursor?: string): Promise<UserPage> {
  return get<UserPage>("/users", { limit: 200, cursor });
}

export function createUser(body: UserCreate): Promise<UserRead> {
  return post<UserRead>("/users", body);
}

export function updateUser(
  userId: string,
  body: { role?: Role; is_active?: boolean },
): Promise<UserRead> {
  return patch<UserRead>(`/users/${userId}`, body);
}

export function unlockUser(userId: string): Promise<UserRead> {
  return post<UserRead>(`/users/${userId}/unlock`);
}

export function resetUserPassword(userId: string, password: string): Promise<UserRead> {
  return post<UserRead>(`/users/${userId}/reset-password`, { password });
}

// --- searches ------------------------------------------------------------------------------

export function getIndustries(): Promise<IndustryOption[]> {
  return get<IndustryOption[]>("/search-jobs/industries");
}

export function getSources(): Promise<SourceRead[]> {
  return get<SourceRead[]>("/sources");
}

export function estimateSearch(body: EstimateRequest): Promise<CostEstimate> {
  return post<CostEstimate>("/search-jobs/estimate", body);
}

export function getSearchJobs(cursor?: string): Promise<SearchJobPage> {
  return get<SearchJobPage>("/search-jobs", { limit: 100, cursor });
}

export function getSearchJob(searchJobId: string): Promise<SearchJobRead> {
  return get<SearchJobRead>(`/search-jobs/${searchJobId}`);
}

export function createSearchJob(body: SearchJobCreate): Promise<SearchJobRead> {
  return post<SearchJobRead>("/search-jobs", body);
}

export function getSearchJobEstimate(searchJobId: string): Promise<CostEstimate> {
  return get<CostEstimate>(`/search-jobs/${searchJobId}/estimate`);
}

export function runSearchJob(searchJobId: string): Promise<JobRunRead> {
  return post<JobRunRead>(`/search-jobs/${searchJobId}/run`);
}

export function getSearchPipeline(searchJobId: string, runId?: string): Promise<PipelineRead> {
  return get<PipelineRead>(`/search-jobs/${searchJobId}/pipeline`, { run_id: runId });
}

// --- monitoring (admin, tech admin) ----------------------------------------------------------

export function getAdminHealth(): Promise<HealthReport> {
  return get<HealthReport>("/admin/health");
}

export function getAlerts(): Promise<AlertRead[]> {
  return get<AlertRead[]>("/admin/alerts");
}

export function acknowledgeAlert(alertId: string): Promise<AlertRead> {
  return post<AlertRead>(`/admin/alerts/${alertId}/acknowledge`);
}

// --- review ----------------------------------------------------------------------------

export type QueueQuery = {
  status?: "pending" | "needs_enrichment";
  service?: string;
  city?: string;
  state?: string;
  industry?: string;
  min_score?: number;
  include_weak?: boolean;
  q?: string;
  /** "First found within N days", measured on the earliest discovered_at of the business. */
  discovered_within_days?: number;
  limit?: number;
  cursor?: string;
};

/** The windows the "Found within" control offers. 24 hours and "1 day" are the same window. */
export const RECENCY_OPTIONS: readonly { value: string; label: string }[] = [
  { value: "", label: "Any time" },
  { value: "1", label: "24 hours" },
  { value: "3", label: "3 days" },
  { value: "7", label: "7 days" },
  { value: "30", label: "30 days" },
];

export function getReviewQueue(query: QueueQuery = {}): Promise<QueuePage> {
  return get<QueuePage>("/review-queue", query);
}

export function getReviewDetail(businessId: string): Promise<ReviewDetail> {
  return get<ReviewDetail>(`/review-queue/${businessId}`);
}

export function reviewOpportunity(
  opportunityId: string,
  body: ReviewRequest,
): Promise<DecidedOpportunity> {
  return post<DecidedOpportunity>(`/opportunities/${opportunityId}/review`, body);
}

export function reviewBatch(body: BatchReviewRequest): Promise<BatchReviewResult> {
  return post<BatchReviewResult>("/opportunities/review-batch", body);
}

export function undoDecision(decisionId: string): Promise<UndoResult> {
  return post<UndoResult>(`/review-decisions/${decisionId}/undo`);
}

export type LeadQuery = {
  service?: string;
  assigned_to?: string;
  city?: string;
  /** "First found within N days", measured on the earliest discovered_at of the business. */
  discovered_within_days?: number;
  limit?: number;
  cursor?: string;
};

export function getLeads(query: LeadQuery = {}): Promise<LeadPage> {
  return get<LeadPage>("/leads", query);
}

export function getLeadDetail(opportunityId: string): Promise<LeadDetail> {
  return get<LeadDetail>(`/leads/${opportunityId}`);
}

export function getSalesReps(): Promise<UserPage> {
  return get<UserPage>("/users", { role: "sales_rep", limit: 200 });
}

// --- crm -------------------------------------------------------------------------------

export function getCrmStatus(): Promise<CrmStatus> {
  return get<CrmStatus>("/crm/status");
}

export function getCrmLeads(status?: CrmLeadStatus, cursor?: string): Promise<CrmLeadPage> {
  return get<CrmLeadPage>("/crm/leads", { status, limit: 100, cursor });
}

export function getCrmLeadAttempts(crmLeadId: string): Promise<CrmSyncAttempt[]> {
  return get<CrmSyncAttempt[]>(`/crm/leads/${crmLeadId}/attempts`);
}

export function retryCrmLead(crmLeadId: string): Promise<CrmLead> {
  return post<CrmLead>(`/crm/leads/${crmLeadId}/retry`);
}

export function syncBusinessNow(businessId: string): Promise<CrmLead> {
  return post<CrmLead>(`/crm/businesses/${businessId}/sync-now`);
}

export function syncAllCrm(): Promise<SyncAllResult> {
  return post<SyncAllResult>("/crm/sync-all");
}

export type CsvExport = { filename: string; blob: Blob };

/**
 * The CSV needs the bearer token, so it cannot be a plain link. Fetched like every other
 * request (one refresh on a 401) and handed back as a blob for the browser to save.
 */
export async function downloadCrmExport(scope: "new" | "all"): Promise<CsvExport> {
  const path = withQuery("/crm/export.csv", { scope });
  let response = await send(path, { method: "GET" }, getAccessToken());
  if (response.status === 401) {
    const token = await refreshAccessToken();
    if (!token) {
      clearAccessToken();
      onSessionLost();
      throw await toApiError(response);
    }
    response = await send(path, { method: "GET" }, token);
  }
  if (!response.ok) throw await toApiError(response);
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename="([^"]+)"/.exec(disposition);
  return { filename: match?.[1] ?? "radar-leads.csv", blob: await response.blob() };
}

// --- duplicates ------------------------------------------------------------------------

export function getMatchCandidates(cursor?: string): Promise<MatchCandidatePage> {
  return get<MatchCandidatePage>("/match-candidates", { status: "pending", limit: 50, cursor });
}

export function decideMatchCandidate(
  candidateId: string,
  decision: "merge" | "keep_apart",
): Promise<MatchCandidate> {
  return post<MatchCandidate>(`/match-candidates/${candidateId}/decision`, { decision });
}

// --- suppressions ----------------------------------------------------------------------

export function getSuppressions(activeOnly = true, cursor?: string): Promise<SuppressionPage> {
  return get<SuppressionPage>("/suppressions", { active_only: activeOnly, limit: 100, cursor });
}

export function addSuppression(body: SuppressionCreate): Promise<SuppressionRead> {
  return post<SuppressionRead>("/suppressions", body);
}

export function liftSuppression(id: string): Promise<SuppressionRead> {
  return post<SuppressionRead>(`/suppressions/${id}/lift`);
}

// --- lookups used by the duplicate picker ------------------------------------------------

export type BusinessSummary = Schemas["BusinessSummary"];
export type OpportunitySummary = Schemas["OpportunitySummary"];

export function searchBusinesses(q: string): Promise<Schemas["Page_BusinessSummary_"]> {
  return get<Schemas["Page_BusinessSummary_"]>("/businesses", { q, limit: 10 });
}

export function getBusinessOpportunities(
  businessId: string,
): Promise<Schemas["Page_OpportunitySummary_"]> {
  return get<Schemas["Page_OpportunitySummary_"]>(`/businesses/${businessId}/opportunities`, {
    limit: 50,
  });
}

export const SERVICES: readonly { key: string; name: string }[] = [
  { key: "website_design", name: "Website redesign" },
  { key: "seo_gbp", name: "SEO / Google profile" },
  { key: "booking_setup", name: "Online booking" },
  { key: "ai_chat_setup", name: "Chat assistant" },
  { key: "ads_social", name: "Ads & social" },
];

export const REJECT_REASONS = [
  "evidence_wrong",
  "business_closed",
  "wrong_industry",
  "ai_mistake",
  "other",
] as const;
export const NOT_A_FIT_REASONS = [
  "too_small",
  "too_large",
  "outside_area",
  "already_client",
  "other",
] as const;
