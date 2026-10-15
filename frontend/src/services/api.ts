import type { AnalysisResponse, CaseCorrelationResult, CaseCreateRequest, CaseDetailResponse, CaseListResponse, CaseResponse, CaseStatus, ComplaintEmailDraftRequest, ComplaintEmailDraftResponse, EvidencePackResponse, EvidenceUploadResponse, HealthResponse, InstitutionParty, ReviewCreateRequest, ReviewHistoryResponse, ReviewResponse } from '../types/contracts';

const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL || import.meta.env.VITE_API_URL || 'http://localhost:8000';
export const API_BASE_URL = configuredBaseUrl.replace(/\/$/, '');

export class TraceApiError extends Error {
  public readonly status: number;
  public readonly details?: unknown;
  constructor(status: number, message: string, details?: unknown) { super(message); this.name = 'TraceApiError'; this.status = status; this.details = details; }
}

async function readError(response: Response): Promise<TraceApiError> {
  let payload: unknown;
  try { payload = await response.json(); } catch { return new TraceApiError(response.status, `Request failed with HTTP ${response.status}.`); }
  if (typeof payload === 'object' && payload !== null) {
    const value = payload as { error?: unknown; detail?: unknown };
    if (typeof value.error === 'string') return new TraceApiError(response.status, value.error, (payload as { details?: unknown }).details);
    if (typeof value.detail === 'string') return new TraceApiError(response.status, value.detail, value.detail);
    if (typeof value.error === 'object' && value.error !== null) {
      const error = value.error as { message?: unknown; details?: unknown };
      if (typeof error.message === 'string') return new TraceApiError(response.status, error.message, error.details);
    }
  }
  return new TraceApiError(response.status, `Request failed with HTTP ${response.status}.`, payload);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try { response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers: { Accept: 'application/json', ...init.headers } }); }
  catch { throw new TraceApiError(0, 'Unable to reach TRACE. Check that the backend is running and the API base URL is correct.'); }
  if (!response.ok) throw await readError(response);
  return response.json() as Promise<T>;
}

export const traceApi = {
  health: () => request<HealthResponse>('/health'),
  createCase: (payload: CaseCreateRequest) => request<CaseResponse>('/api/v1/cases', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  listCases: (options: { page?: number; pageSize?: number; status?: CaseStatus } = {}) => {
    const query = new URLSearchParams({ page: String(options.page ?? 1), page_size: String(options.pageSize ?? 20) });
    if (options.status) query.set('status', options.status);
    return request<CaseListResponse>(`/api/v1/cases?${query}`);
  },
  getCase: (caseId: string) => request<CaseDetailResponse>(`/api/v1/cases/${encodeURIComponent(caseId)}`),
  uploadEvidence: (caseId: string, file: File, institutionSource: InstitutionParty, fileType?: 'CSV' | 'JSONL') => {
    const form = new FormData(); form.set('file', file); form.set('institution_source', institutionSource); if (fileType) form.set('file_type', fileType);
    return request<EvidenceUploadResponse>(`/api/v1/cases/${encodeURIComponent(caseId)}/evidence`, { method: 'POST', body: form });
  },
  getCorrelation: (caseId: string) => request<CaseCorrelationResult>(`/api/v1/cases/${encodeURIComponent(caseId)}/correlation`),
  analyze: (caseId: string) => request<AnalysisResponse>(`/api/v1/cases/${encodeURIComponent(caseId)}/analyze`, { method: 'POST' }),
  getEvidencePack: (caseId: string) => request<EvidencePackResponse>(`/api/v1/cases/${encodeURIComponent(caseId)}/evidence-pack`),
  draftComplaintEmail: (caseId: string, payload: ComplaintEmailDraftRequest) => request<ComplaintEmailDraftResponse>(`/api/v1/cases/${encodeURIComponent(caseId)}/complaint-email`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  createReview: (caseId: string, payload: ReviewCreateRequest) => request<ReviewResponse>(`/api/v1/cases/${encodeURIComponent(caseId)}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  getReviews: (caseId: string) => request<ReviewHistoryResponse>(`/api/v1/cases/${encodeURIComponent(caseId)}/reviews`),
};

export function errorMessage(error: unknown): string { return error instanceof Error ? error.message : 'An unexpected error occurred.'; }
