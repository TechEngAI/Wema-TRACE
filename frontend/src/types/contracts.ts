/** Typed mirrors of the currently supported TRACE main-backend contracts. */

export type OutcomeEnum = 'DISCREPANCY_CONFIRMED' | 'LIKELY_ROOT_CAUSE' | 'INSUFFICIENT_EVIDENCE' | 'CONFLICTING_EVIDENCE' | 'NO_DISCREPANCY_FOUND';
export type StrengthEnum = 'HIGH' | 'MODERATE' | 'LOW' | 'INSUFFICIENT';
export type InstitutionParty = 'ORIGINATING_BANK' | 'SWITCH' | 'RECEIVING_BANK' | 'MERCHANT';
export type EventAction = 'DEBIT' | 'CREDIT' | 'SWITCH_IN' | 'SWITCH_OUT' | 'ACK' | 'REVERSAL_ATTEMPT' | 'TIMEOUT';
export type EventStatus = 'SUCCESS' | 'PENDING' | 'FAILED' | 'TIMEOUT';
export type FlagSeverity = 'LOW' | 'MEDIUM' | 'CRITICAL';
export type CaseStatus = 'OPEN' | 'ANALYZED' | 'REVIEWED' | 'CLOSED';
export type CorrelationStatus = 'CONFIRMED' | 'CANDIDATE' | 'AMBIGUOUS' | 'UNMATCHED';
export type CorrelationMethod = 'SESSION_AND_RRN_MATCH' | 'SESSION_ID_MATCH' | 'RRN_MATCH' | 'INSTITUTION_SCOPED_REFERENCE' | 'TEMPORAL_AMOUNT_PROXIMITY' | 'PARTIAL_REFERENCE_HEURISTIC' | 'NONE';
export type IdentifierScope = 'GLOBAL_INTERBANK' | 'INSTITUTION_LOCAL' | 'NONE';

export const OUTCOMES: readonly OutcomeEnum[] = ['DISCREPANCY_CONFIRMED', 'LIKELY_ROOT_CAUSE', 'INSUFFICIENT_EVIDENCE', 'CONFLICTING_EVIDENCE', 'NO_DISCREPANCY_FOUND'] as const;
export const STRENGTHS: readonly StrengthEnum[] = ['HIGH', 'MODERATE', 'LOW', 'INSUFFICIENT'] as const;
export const INSTITUTION_PARTIES: readonly InstitutionParty[] = ['ORIGINATING_BANK', 'SWITCH', 'RECEIVING_BANK', 'MERCHANT'] as const;

export interface HealthResponse { status: string; service: string; version: string; timestamp: string; environment: string; }
export interface CaseCreateRequest { reference: string; initiating_party: InstitutionParty; counterparty: InstitutionParty; disputed_amount: number; currency?: string; session_id?: string; rrn?: string; narrative?: string; metadata?: Record<string, unknown>; }
export interface CaseResponse { case_id: string; reference: string; initiating_party: InstitutionParty; counterparty: InstitutionParty; disputed_amount: number; currency: string; status: CaseStatus; created_at: string; metadata: Record<string, unknown>; }
export interface CaseListItem extends CaseResponse { latest_outcome: OutcomeEnum | null; latest_strength: StrengthEnum | null; }
export interface CaseListResponse { items: CaseListItem[]; total: number; page: number; page_size: number; total_pages: number; }
export interface TimelineEvent { event_id: string; timestamp: string; party: InstitutionParty; action: EventAction; status: EventStatus; amount_minor_units: number; currency: string; reference: string | null; session_id: string | null; rrn: string | null; channel: string | null; raw_record_id: string; data_quality_notes: string | null; }
export interface MissingEvidenceItem { expected_party: InstitutionParty; expected_action: EventAction; window_start: string; window_end: string; reason: string; evidence_type?: string | null; description?: string | null; impact?: string | null; recommended_request?: string | null; }
export interface RecommendedEvidenceItem { evidence_type: string; expected_party: InstitutionParty | null; rationale: string; related_event_ids: string[]; }
export interface DataQualityFlagItem { flag_code: string; severity: FlagSeverity; description: string; affected_event_ids: string[]; impact: string; }
export interface CounterfactualItem { scenario: string; condition: string; outcome_if_condition_met: OutcomeEnum; explanation: string; }
export interface AnalysisExplanation { summary: string; reasoning_explanation: string; supporting_explanation: string; contradiction_explanation: string; uncertainty_explanation: string; next_step_explanation: string; }
export interface AnalysisResponse { case_id: string; outcome: OutcomeEnum; strength: StrengthEnum; rule_version: string; category: string; timeline: TimelineEvent[]; supporting_event_ids: string[]; missing_evidence: MissingEvidenceItem[]; data_quality_flags: DataQualityFlagItem[]; counterfactuals: CounterfactualItem[]; outcome_reason: string; strength_reason: string; scope_examined: string; supporting_evidence: string[]; contradicting_evidence: string[]; alternative_explanations: string[]; recommended_next_evidence: RecommendedEvidenceItem[]; explanation: AnalysisExplanation | null; }
export interface CaseDetailResponse extends CaseResponse { events_count: number; latest_analysis: AnalysisResponse | null; }
export interface EvidenceUploadResponse { source_file_id: string; case_id: string; filename: string; records_ingested: number; sha256_hash: string; status: string; }
export interface CorrelationEventRef { event_id: string; raw_record_id: string; party: InstitutionParty; action: EventAction; status: EventStatus; amount_minor_units: number; currency: string; session_id: string | null; rrn: string | null; reference: string | null; event_timestamp: string; }
export interface CorrelationGroup { group_id: string; status: CorrelationStatus; method: CorrelationMethod; identifier_scope: IdentifierScope; reason_code: string; reason_description: string; member_event_ids: string[]; events: CorrelationEventRef[]; identifying_keys: Record<string, unknown>; temporal_ordering_ambiguous: boolean; duplicate_identity_event_ids: string[]; contradictions: string[]; }
export interface CaseCorrelationResult { case_id: string; algorithm_version: string; computed_at: string; total_events: number; groups: CorrelationGroup[]; summary: Record<string, number>; }
export interface EvidencePackTimelineEvent { event_id: string; event_timestamp: string; institution: InstitutionParty; action: EventAction; status: EventStatus; amount_minor_units: number; currency: string; session_id: string | null; rrn: string | null; reference: string | null; raw_record_id: string; source_file_id: string | null; data_quality_flags: string[]; }
export interface EvidencePackResponse { pack_schema_version: string; pack_id: string; case_id: string; case_reference: string; generated_at: string; analysis_rule_version: string; explanation_status: string; investigation_aid_notice: string; summary: { outcome: OutcomeEnum; strength: StrengthEnum; outcome_reason: string; strength_reason: string; scope_examined: string; disputed_amount_minor_units: number; currency: string; case_session_id: string | null; case_rrn: string | null; identifier_source: string; }; correlation: { algorithm_version: string; summary: Record<string, number>; groups: CorrelationGroup[]; }; timeline: EvidencePackTimelineEvent[]; evidence: { supporting_event_ids: string[]; contradicting_event_ids: string[]; missing_evidence: MissingEvidenceItem[]; alternative_explanations: string[]; recommended_next_evidence: RecommendedEvidenceItem[]; }; data_quality_flags: DataQualityFlagItem[]; limitations: string[]; explanation: AnalysisExplanation; raw_file_hashes: Array<{ institution: InstitutionParty; filename: string; sha256: string }>; integrity: { algorithm: string; sha256: string; hashed_representation: string; limitation: string; }; }

export type ComplaintEmailPurpose = 'REQUEST_MISSING_EVIDENCE' | 'INVESTIGATE_CONFLICTING_STATUSES' | 'CLARIFY_SUSPECTED_DISCREPANCY' | 'REQUEST_CONFIRMATION';
export const COMPLAINT_EMAIL_PURPOSES: readonly ComplaintEmailPurpose[] = [
  'REQUEST_MISSING_EVIDENCE',
  'INVESTIGATE_CONFLICTING_STATUSES',
  'CLARIFY_SUSPECTED_DISCREPANCY',
  'REQUEST_CONFIRMATION',
] as const;

export interface ComplaintEmailDraftRequest {
  recipient_institution: InstitutionParty;
  recipient_role?: string;
  recipient_email?: string;
  purpose?: ComplaintEmailPurpose;
  additional_context?: string;
}

export interface ComplaintEmailDraftResponse {
  draft_id: string;
  case_id: string;
  case_reference: string;
  recipient_institution: InstitutionParty;
  recipient_role: string | null;
  recipient_email: string | null;
  purpose: ComplaintEmailPurpose;
  subject: string;
  greeting: string;
  body: string;
  factual_summary: string;
  discrepancy_details: string;
  evidence_summary: string;
  requested_records: string[];
  closing: string;
  authoritative_outcome: OutcomeEnum;
  authoritative_strength: StrengthEnum;
  is_draft: boolean;
  requires_human_review: boolean;
  disclaimer: string;
  generated_at: string;
  generator: string;
  data_quality_caveats: string[];
}

export type ReviewDecision = 'ACCEPT_FINDING' | 'DISPUTE_FINDING' | 'REQUEST_MORE_EVIDENCE';
export const REVIEW_DECISIONS: readonly ReviewDecision[] = ['ACCEPT_FINDING', 'DISPUTE_FINDING', 'REQUEST_MORE_EVIDENCE'] as const;
export interface ReviewCreateRequest { reviewer_display_name: string; decision: ReviewDecision; rationale: string; notes?: string; requested_additional_evidence?: string[]; }
export interface ReviewResponse { review_id: string; case_id: string; reviewer_display_name: string; reviewer_identity_verified: boolean; decision: ReviewDecision; rationale: string; notes: string | null; requested_additional_evidence: string[]; machine_finding: { finding_reference: string; outcome: OutcomeEnum; strength: StrengthEnum; rule_version: string; category: string; outcome_reason: string; strength_reason: string; scope_examined: string; }; created_at: string; }
export interface ReviewHistoryResponse { case_id: string; reviews: ReviewResponse[]; }
