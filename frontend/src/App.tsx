import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { API_BASE_URL, errorMessage, traceApi } from './services/api';
import {
  INSTITUTION_PARTIES,
  REVIEW_DECISIONS,
  type AnalysisResponse,
  type CaseCorrelationResult,
  type CaseDetailResponse,
  type CaseListResponse,
  type CaseStatus,
  type ComplaintEmailDraftResponse,
  type ComplaintEmailPurpose,
  type EvidencePackResponse,
  type EvidenceUploadResponse,
  type InstitutionParty,
  type ReviewDecision,
  type ReviewHistoryResponse,
} from './types/contracts';

type View = 'cases' | 'detail' | 'correlation' | 'analysis' | 'pack' | 'draft' | 'reviews';
const statuses: Array<CaseStatus | ''> = ['', 'OPEN', 'ANALYZED', 'REVIEWED', 'CLOSED'];

function Notice({ error, success }: { error?: string | null; success?: string | null }) {
  return (
    <>
      {error && <p className="notice error">{error}</p>}
      {success && <p className="notice success">{success}</p>}
    </>
  );
}

function Loading({ label = 'Loading…' }: { label?: string }) {
  return <p className="muted">{label}</p>;
}

function EvidenceIds({ title, ids }: { title: string; ids: string[] }) {
  return (
    <div>
      <h4>{title}</h4>
      {ids.length ? (
        <ul className="mono-list">
          {ids.map((id) => (
            <li key={id}>{id}</li>
          ))}
        </ul>
      ) : (
        <p className="muted">None recorded.</p>
      )}
    </div>
  );
}

export default function App() {
  const [view, setView] = useState<View>('cases');
  const [cases, setCases] = useState<CaseListResponse | null>(null);
  const [selected, setSelected] = useState<CaseDetailResponse | null>(null);
  const [correlation, setCorrelation] = useState<CaseCorrelationResult | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null);
  const [pack, setPack] = useState<EvidencePackResponse | null>(null);
  const [draft, setDraft] = useState<ComplaintEmailDraftResponse | null>(null);
  const [reviews, setReviews] = useState<ReviewHistoryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<CaseStatus | ''>('');

  const run = async <T,>(work: () => Promise<T>, apply: (value: T) => void, message?: string) => {
    setLoading(true);
    setError(null);
    setSuccess(null);
    try {
      const value = await work();
      apply(value);
      if (message) setSuccess(message);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoading(false);
    }
  };

  const loadCases = (nextPage = page, nextStatus = statusFilter) =>
    run(() => traceApi.listCases({ page: nextPage, status: nextStatus || undefined }), setCases);

  useEffect(() => {
    let active = true;
    void traceApi
      .listCases({ page: 1, status: statusFilter || undefined })
      .then((data) => {
        if (active) setCases(data);
      })
      .catch((caught: unknown) => {
        if (active) setError(errorMessage(caught));
      });
    return () => {
      active = false;
    };
  }, [statusFilter]);

  const openCase = (caseId: string) =>
    run(
      () => traceApi.getCase(caseId),
      (detail) => {
        setSelected(detail);
        setCorrelation(null);
        setAnalysis(null);
        setPack(null);
        setDraft(null);
        setReviews(null);
        setView('detail');
      }
    );

  const selectedId = selected?.case_id;
  const requireCase = () => {
    if (!selectedId) {
      setError('Open a case before using this workflow.');
      return false;
    }
    return true;
  };

  return (
    <div className="container">
      <header className="header">
        <div className="brand-badge">Parts 1–7 integrated</div>
        <h1 className="title">TRACE Investigation Platform</h1>
        <p className="subtitle">
          Synthetic evidence investigation. Findings remain owned by the deterministic rules engine.
        </p>
        <p className="muted">
          Main API: <code>{API_BASE_URL}</code>
        </p>
      </header>
      <nav className="tabs" aria-label="TRACE workflow">
        <button className={view === 'cases' ? 'active' : ''} onClick={() => setView('cases')}>
          Cases
        </button>
        {selected && (
          <>
            <button className={view === 'detail' ? 'active' : ''} onClick={() => setView('detail')}>
              Case detail
            </button>
            <button
              className={view === 'correlation' ? 'active' : ''}
              onClick={() => {
                if (requireCase())
                  void run(
                    () => traceApi.getCorrelation(selectedId!),
                    (data) => {
                      setCorrelation(data);
                      setView('correlation');
                    }
                  );
              }}
            >
              Correlation
            </button>
            <button
              className={view === 'analysis' ? 'active' : ''}
              onClick={() => {
                if (requireCase())
                  void run(
                    () => traceApi.analyze(selectedId!),
                    (data) => {
                      setAnalysis(data);
                      setView('analysis');
                    }
                  );
              }}
            >
              Analysis
            </button>
            <button
              className={view === 'reviews' ? 'active' : ''}
              onClick={() => {
                if (requireCase()) void run(() => traceApi.getReviews(selectedId!), (data) => { setReviews(data); setView('reviews'); });
              }}
            >
              Human reviews
            </button>
            <button
              className={view === 'pack' ? 'active' : ''}
              onClick={() => {
                if (requireCase())
                  void run(
                    () => traceApi.getEvidencePack(selectedId!),
                    (data) => {
                      setPack(data);
                      setView('pack');
                    }
                  );
              }}
            >
              Evidence pack
            </button>
            <button className={view === 'draft' ? 'active' : ''} onClick={() => setView('draft')}>
              Draft email
            </button>
          </>
        )}
      </nav>
      <Notice error={error} success={success} />
      {loading && <Loading />}
      {view === 'cases' && (
        <CasesView
          cases={cases}
          page={page}
          statusFilter={statusFilter}
          onStatus={(next) => {
            setPage(1);
            setStatusFilter(next);
          }}
          onPage={(next) => {
            setPage(next);
            void loadCases(next);
          }}
          onOpen={openCase}
          onCreated={(detail) => {
            setSelected(detail);
            setView('detail');
            void loadCases(1, statusFilter);
          }}
        />
      )}
      {view === 'detail' && selected && (
        <DetailView
          detail={selected}
          onRefresh={() => void openCase(selected.case_id)}
          onUploaded={() => void openCase(selected.case_id)}
          onDraft={() => setView('draft')}
        />
      )}
      {view === 'correlation' && selected && (
        <CorrelationView
          data={correlation}
          onLoad={() => void run(() => traceApi.getCorrelation(selected.case_id), setCorrelation)}
        />
      )}
      {view === 'analysis' && selected && (
        <AnalysisView
          data={analysis}
          onRun={() => void run(() => traceApi.analyze(selected.case_id), setAnalysis)}
        />
      )}
      {view === 'reviews' && selected && (
        <ReviewsView
          caseId={selected.case_id}
          history={reviews}
          onRefresh={() => void run(() => traceApi.getReviews(selected.case_id), setReviews)}
          onCreated={(history) => setReviews(history)}
        />
      )}
      {view === 'pack' && selected && (
        <PackView
          data={pack}
          onLoad={() => void run(() => traceApi.getEvidencePack(selected.case_id), setPack)}
        />
      )}
      {view === 'draft' && selected && (
        <DraftEmailView
          caseDetail={selected}
          draft={draft}
          onDraftGenerated={(d) => setDraft(d)}
        />
      )}
      <footer className="footer">
        Synthetic-data development only. Case/evidence routes have no access control and must not be publicly deployed with real financial data.
      </footer>
    </div>
  );
}

function CasesView({
  cases,
  page,
  statusFilter,
  onStatus,
  onPage,
  onOpen,
  onCreated,
}: {
  cases: CaseListResponse | null;
  page: number;
  statusFilter: CaseStatus | '';
  onStatus: (value: CaseStatus | '') => void;
  onPage: (page: number) => void;
  onOpen: (id: string) => void;
  onCreated: (caseData: CaseDetailResponse) => void;
}) {
  const [form, setForm] = useState({
    reference: '',
    initiating_party: 'ORIGINATING_BANK' as InstitutionParty,
    counterparty: 'RECEIVING_BANK' as InstitutionParty,
    disputed_amount: '',
    currency: 'NGN',
    session_id: '',
    rrn: '',
    narrative: '',
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    const amount = Number(form.disputed_amount);
    if (!form.reference.trim() || !Number.isInteger(amount) || amount <= 0) {
      setError('Reference and a positive whole-number minor-unit amount are required.');
      return;
    }
    setSubmitting(true);
    try {
      const created = await traceApi.createCase({
        ...form,
        reference: form.reference.trim(),
        disputed_amount: amount,
        session_id: form.session_id || undefined,
        rrn: form.rrn || undefined,
        narrative: form.narrative || undefined,
      });
      const detail = await traceApi.getCase(created.case_id);
      onCreated(detail);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <section className="card">
        <div className="card-title">
          <span>Create case</span>
        </div>
        <form className="form-grid" onSubmit={submit}>
          <label>
            Reference
            <input
              value={form.reference}
              onChange={(e) => setForm({ ...form, reference: e.target.value })}
              required
            />
          </label>
          <label>
            Disputed amount (minor units)
            <input
              type="number"
              min="1"
              step="1"
              value={form.disputed_amount}
              onChange={(e) => setForm({ ...form, disputed_amount: e.target.value })}
              required
            />
          </label>
          <label>
            Initiating party
            <select
              value={form.initiating_party}
              onChange={(e) => setForm({ ...form, initiating_party: e.target.value as InstitutionParty })}
            >
              {INSTITUTION_PARTIES.map((party) => (
                <option key={party}>{party}</option>
              ))}
            </select>
          </label>
          <label>
            Counterparty
            <select
              value={form.counterparty}
              onChange={(e) => setForm({ ...form, counterparty: e.target.value as InstitutionParty })}
            >
              {INSTITUTION_PARTIES.map((party) => (
                <option key={party}>{party}</option>
              ))}
            </select>
          </label>
          <label>
            Currency
            <input
              maxLength={3}
              value={form.currency}
              onChange={(e) => setForm({ ...form, currency: e.target.value.toUpperCase() })}
            />
          </label>
          <label>
            Session ID (optional)
            <input
              value={form.session_id}
              onChange={(e) => setForm({ ...form, session_id: e.target.value })}
            />
          </label>
          <label>
            RRN (optional)
            <input
              value={form.rrn}
              onChange={(e) => setForm({ ...form, rrn: e.target.value })}
            />
          </label>
          <label className="wide">
            Narrative (optional)
            <textarea
              value={form.narrative}
              onChange={(e) => setForm({ ...form, narrative: e.target.value })}
            />
          </label>
          <button disabled={submitting}>{submitting ? 'Creating…' : 'Create case'}</button>
        </form>
        <Notice error={error} />
      </section>
      <section className="card">
        <div className="card-title">
          <span>Cases</span>
          <label className="inline">
            Status{' '}
            <select value={statusFilter} onChange={(e) => onStatus(e.target.value as CaseStatus | '')}>
              {statuses.map((status) => (
                <option key={status} value={status}>
                  {status || 'All'}
                </option>
              ))}
            </select>
          </label>
        </div>
        {!cases ? (
          <Loading />
        ) : cases.items.length === 0 ? (
          <p className="muted">No cases match this filter.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Reference</th>
                  <th>Status</th>
                  <th>Amount</th>
                  <th>Created</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {cases.items.map((item) => (
                  <tr key={item.case_id}>
                    <td>{item.reference}</td>
                    <td>{item.status}</td>
                    <td>
                      {item.disputed_amount} {item.currency}
                    </td>
                    <td>{new Date(item.created_at).toLocaleString()}</td>
                    <td>
                      <button onClick={() => onOpen(item.case_id)}>Open</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="pagination">
          <span>
            Page {cases?.page ?? page} of {cases?.total_pages ?? 0} · {cases?.total ?? 0} cases
          </span>
          <button disabled={page <= 1} onClick={() => onPage(page - 1)}>
            Previous
          </button>
          <button disabled={!cases || page >= cases.total_pages} onClick={() => onPage(page + 1)}>
            Next
          </button>
        </div>
      </section>
    </>
  );
}

function DetailView({
  detail,
  onRefresh,
  onUploaded,
  onDraft,
}: {
  detail: CaseDetailResponse;
  onRefresh: () => void;
  onUploaded: () => void;
  onDraft: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [source, setSource] = useState<InstitutionParty>('ORIGINATING_BANK');
  const [upload, setUpload] = useState<EvidenceUploadResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!file) {
      setError('Choose a CSV or JSONL evidence file.');
      return;
    }
    setSending(true);
    setError(null);
    try {
      const type = file.name.toLowerCase().endsWith('.jsonl')
        ? 'JSONL'
        : file.name.toLowerCase().endsWith('.csv')
        ? 'CSV'
        : undefined;
      const result = await traceApi.uploadEvidence(detail.case_id, file, source, type);
      setUpload(result);
      onUploaded();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSending(false);
    }
  };

  return (
    <>
      <section className="card">
        <div className="card-title">
          <span>{detail.reference}</span>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button onClick={onDraft}>Draft investigation email</button>
            <button onClick={onRefresh}>Refresh</button>
          </div>
        </div>
        <dl className="details">
          <dt>Case ID</dt>
          <dd>
            <code>{detail.case_id}</code>
          </dd>
          <dt>Parties</dt>
          <dd>
            {detail.initiating_party} → {detail.counterparty}
          </dd>
          <dt>Disputed amount</dt>
          <dd>
            {detail.disputed_amount} {detail.currency} (minor units)
          </dd>
          <dt>Evidence records</dt>
          <dd>{detail.events_count} normalized event(s) recorded</dd>
          <dt>Latest analysis</dt>
          <dd>
            {detail.latest_analysis
              ? 'Returned by backend'
              : 'Not persisted by the current backend; run Analysis to compute on demand.'}
          </dd>
        </dl>
        <p className="muted">
          Case detail currently returns metadata and an event count, not raw evidence or a full event timeline.
        </p>
      </section>
      <section className="card">
        <div className="card-title">
          <span>Upload evidence</span>
        </div>
        <form className="form-grid" onSubmit={submit}>
          <label>
            Evidence file
            <input
              type="file"
              accept=".csv,.jsonl,text/csv,application/x-ndjson,application/jsonl"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <label>
            Institution source
            <select
              value={source}
              onChange={(e) => setSource(e.target.value as InstitutionParty)}
            >
              {INSTITUTION_PARTIES.map((party) => (
                <option key={party}>{party}</option>
              ))}
            </select>
          </label>
          <button disabled={sending}>{sending ? 'Uploading…' : 'Upload and ingest'}</button>
        </form>
        <Notice
          error={error}
          success={upload ? `${upload.records_ingested} record(s) ingested from ${upload.filename}.` : null}
        />
        {upload && (
          <p className="muted">
            Source SHA-256: <code>{upload.sha256_hash}</code>
          </p>
        )}
      </section>
    </>
  );
}

function CorrelationView({ data, onLoad }: { data: CaseCorrelationResult | null; onLoad: () => void }) {
  return (
    <section className="card">
      <div className="card-title">
        <span>Deterministic correlation</span>
        <button onClick={onLoad}>Refresh correlation</button>
      </div>
      {!data ? (
        <Loading label="Load correlation to inspect confirmed, candidate, ambiguous, and unmatched records." />
      ) : (
        <>
          <p className="muted">
            {data.total_events} event(s); algorithm {data.algorithm_version}. Amount/time proximity is never shown as a confirmed link.
          </p>
          {data.groups.map((group) => (
            <article className={`group ${group.status.toLowerCase()}`} key={group.group_id}>
              <h3>
                {group.status} · {group.method}
              </h3>
              <p>{group.reason_description}</p>
              <p className="muted">
                Identifier scope: {group.identifier_scope} · {group.member_event_ids.length} member(s)
              </p>
              {group.temporal_ordering_ambiguous && (
                <p className="notice error">Temporal ordering is ambiguous.</p>
              )}
              {group.contradictions.length > 0 && (
                <p className="notice error">Conflicts: {group.contradictions.join('; ')}</p>
              )}
              {group.duplicate_identity_event_ids.length > 0 && (
                <p className="notice success">
                  Duplicate-identity records: {group.duplicate_identity_event_ids.join(', ')}
                </p>
              )}
            </article>
          ))}
        </>
      )}
    </section>
  );
}

function AnalysisView({ data, onRun }: { data: AnalysisResponse | null; onRun: () => void }) {
  return (
    <section className="card">
      <div className="card-title">
        <span>Authoritative deterministic analysis</span>
        <button onClick={onRun}>Run analysis</button>
      </div>
      {!data ? (
        <Loading label="Run analysis to receive the backend’s authoritative finding." />
      ) : (
        <>
          <div className="badge-grid">
            <span className="badge badge-outcome">{data.outcome}</span>
            <span className="badge badge-strength">{data.strength}</span>
            <span className="badge">Rules {data.rule_version}</span>
          </div>
          <h3>{data.category}</h3>
          <p>{data.outcome_reason}</p>
          <p className="muted">{data.strength_reason}</p>
          <p className="muted">Scope: {data.scope_examined}</p>
          <EvidenceIds title="Supporting evidence" ids={data.supporting_evidence} />
          <EvidenceIds title="Contradicting evidence" ids={data.contradicting_evidence} />
          <h4>Missing evidence</h4>
          {data.missing_evidence.length ? (
            <ul>
              {data.missing_evidence.map((item, index) => (
                <li key={`${item.expected_party}-${index}`}>
                  {item.expected_party} {item.expected_action}: {item.reason}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">None recorded.</p>
          )}
          <h4>Recommended next evidence</h4>
          {data.recommended_next_evidence.length ? (
            <ul>
              {data.recommended_next_evidence.map((item, index) => (
                <li key={`${item.evidence_type}-${index}`}>
                  {item.evidence_type}: {item.rationale}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">None recorded.</p>
          )}
          <h4>Part 5 explanation (non-authoritative)</h4>
          {data.explanation ? (
            <div className="explanation">
              <p>{data.explanation.summary}</p>
              <p>{data.explanation.uncertainty_explanation}</p>
              <p>{data.explanation.next_step_explanation}</p>
            </div>
          ) : (
            <p className="muted">No explanation was returned.</p>
          )}
        </>
      )}
    </section>
  );
}

function PackView({ data, onLoad }: { data: EvidencePackResponse | null; onLoad: () => void }) {
  return (
    <section className="card">
      <div className="card-title">
        <span>Evidence pack</span>
        <button onClick={onLoad}>Generate current pack</button>
      </div>
      {!data ? (
        <Loading label="Generate a read-only pack from the current persisted evidence." />
      ) : (
        <>
          <p>{data.investigation_aid_notice}</p>
          <dl className="details">
            <dt>Pack ID</dt>
            <dd>
              <code>{data.pack_id}</code>
            </dd>
            <dt>Generated</dt>
            <dd>{new Date(data.generated_at).toLocaleString()}</dd>
            <dt>Finding</dt>
            <dd>
              {data.summary.outcome} / {data.summary.strength}
            </dd>
            <dt>Integrity</dt>
            <dd>
              <code>
                {data.integrity.algorithm}: {data.integrity.sha256}
              </code>
            </dd>
          </dl>
          <p className="muted">{data.integrity.limitation}</p>
          <h4>Raw source file hashes</h4>
          {data.raw_file_hashes.length ? (
            <ul>
              {data.raw_file_hashes.map((file) => (
                <li key={`${file.filename}-${file.sha256}`}>
                  {file.institution}: {file.filename} · <code>{file.sha256}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">No source files recorded.</p>
          )}
          <h4>Limitations</h4>
          <ul>
            {data.limitations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          <p className="muted">PDF export is not implemented by the backend, so no PDF control is shown.</p>
        </>
      )}
    </section>
  );
}

function DraftEmailView({
  caseDetail,
  draft,
  onDraftGenerated,
}: {
  caseDetail: CaseDetailResponse;
  draft: ComplaintEmailDraftResponse | null;
  onDraftGenerated: (draft: ComplaintEmailDraftResponse) => void;
}) {
  const [recipientInstitution, setRecipientInstitution] = useState<InstitutionParty>(
    caseDetail.counterparty || 'SWITCH'
  );
  const [recipientRole, setRecipientRole] = useState('');
  const [recipientEmail, setRecipientEmail] = useState('');
  const [purpose, setPurpose] = useState<ComplaintEmailPurpose | ''>('');
  const [additionalContext, setAdditionalContext] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setCopied(false);
    try {
      const response = await traceApi.draftComplaintEmail(caseDetail.case_id, {
        recipient_institution: recipientInstitution,
        recipient_role: recipientRole.trim() || undefined,
        recipient_email: recipientEmail.trim() || undefined,
        purpose: purpose || undefined,
        additional_context: additionalContext.trim() || undefined,
      });
      onDraftGenerated(response);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  };

  const handleCopy = async () => {
    if (!draft) return;
    try {
      await navigator.clipboard.writeText(draft.body);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      const textArea = document.createElement('textarea');
      textArea.value = draft.body;
      document.body.appendChild(textArea);
      textArea.select();
      document.execCommand('copy');
      document.body.removeChild(textArea);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    }
  };

  return (
    <>
      <section className="card">
        <div className="card-title">
          <span>Draft investigation email</span>
          <span className="badge badge-outcome">Drafting assistant only</span>
        </div>
        <p className="notice">
          <strong>Notice:</strong> This feature drafts a neutral, evidence-grounded investigation email for human review. It does not send emails, transfer funds, issue refunds, or make institutional commitments.
        </p>
        <form className="form-grid" onSubmit={handleSubmit}>
          <label>
            Recipient institution *
            <select
              value={recipientInstitution}
              onChange={(e) => setRecipientInstitution(e.target.value as InstitutionParty)}
              required
            >
              {INSTITUTION_PARTIES.map((party) => (
                <option key={party} value={party}>
                  {party}
                </option>
              ))}
            </select>
          </label>
          <label>
            Recipient role / team
            <input
              placeholder="e.g. Settlement Operations, Dispute Desk"
              value={recipientRole}
              onChange={(e) => setRecipientRole(e.target.value)}
              maxLength={100}
            />
          </label>
          <label>
            Recipient email address (optional)
            <input
              type="email"
              placeholder="e.g. disputes@institution.com"
              value={recipientEmail}
              onChange={(e) => setRecipientEmail(e.target.value)}
              maxLength={255}
            />
          </label>
          <label>
            Investigation purpose
            <select
              value={purpose}
              onChange={(e) => setPurpose(e.target.value as ComplaintEmailPurpose | '')}
            >
              <option value="">Automatic (inferred from authoritative outcome)</option>
              <option value="REQUEST_MISSING_EVIDENCE">Request missing transaction evidence</option>
              <option value="INVESTIGATE_CONFLICTING_STATUSES">Investigate conflicting transaction statuses</option>
              <option value="CLARIFY_SUSPECTED_DISCREPANCY">Clarify suspected payment discrepancy</option>
              <option value="REQUEST_CONFIRMATION">Request confirmation of reversal / credit</option>
            </select>
          </label>
          <label className="wide">
            Additional context / operator notes (optional, max 500 chars)
            <textarea
              placeholder="Optional non-authoritative notes for the draft. Untrusted instructions will be rejected."
              value={additionalContext}
              onChange={(e) => setAdditionalContext(e.target.value)}
              maxLength={500}
            />
          </label>
          <button type="submit" disabled={submitting}>
            {submitting ? 'Generating draft…' : 'Generate email draft'}
          </button>
        </form>
        <Notice error={error} />
      </section>

      {draft && (
        <section className="card">
          <div className="card-title">
            <span>Generated draft</span>
            <div className="badge-grid">
              <span className="badge badge-outcome">DRAFT ONLY</span>
              <span className="badge badge-strength">
                {draft.authoritative_outcome} / {draft.authoritative_strength}
              </span>
              <span className="badge">{draft.purpose}</span>
            </div>
          </div>
          <p className="notice error">
            <strong>Human review required:</strong> This draft has not been transmitted. Review all transaction identifiers, facts, and requested records before any manual transmission.
          </p>
          <dl className="details">
            <dt>Subject</dt>
            <dd>
              <strong>{draft.subject}</strong>
            </dd>
            <dt>Recipient</dt>
            <dd>
              {draft.recipient_institution}{' '}
              {draft.recipient_role ? `(${draft.recipient_role})` : ''}{' '}
              {draft.recipient_email ? `<${draft.recipient_email}>` : ''}
            </dd>
            <dt>Factual summary</dt>
            <dd>{draft.factual_summary}</dd>
            <dt>Discrepancy</dt>
            <dd>{draft.discrepancy_details}</dd>
            <dt>Generator</dt>
            <dd>
              <code>{draft.generator}</code>
            </dd>
          </dl>
          <h4>Requested records</h4>
          <ul>
            {draft.requested_records.map((rec, idx) => (
              <li key={idx}>{rec}</li>
            ))}
          </ul>
          {draft.data_quality_caveats.length > 0 && (
            <>
              <h4>Data quality caveats</h4>
              <ul>
                {draft.data_quality_caveats.map((c, idx) => (
                  <li key={idx}>{c}</li>
                ))}
              </ul>
            </>
          )}
          <h4>Full email draft text</h4>
          <pre className="email-preview">{draft.body}</pre>
          <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', marginTop: '1rem', flexWrap: 'wrap' }}>
            <button type="button" onClick={handleCopy}>
              {copied ? '✓ Copied to clipboard!' : 'Copy draft to clipboard'}
            </button>
            <span className="muted" style={{ fontSize: '0.85rem' }}>
              No email-send button: copy and paste into your verified institutional mail client.
            </span>
          </div>
          <p className="muted" style={{ marginTop: '1rem', fontStyle: 'italic', fontSize: '0.82rem' }}>
            {draft.disclaimer}
          </p>
        </section>
      )}
    </>
  );
}

function ReviewsView({
  caseId,
  history,
  onRefresh,
  onCreated,
}: {
  caseId: string;
  history: ReviewHistoryResponse | null;
  onRefresh: () => void;
  onCreated: (history: ReviewHistoryResponse) => void;
}) {
  const [reviewerDisplayName, setReviewerDisplayName] = useState('');
  const [decision, setDecision] = useState<ReviewDecision>('ACCEPT_FINDING');
  const [rationale, setRationale] = useState('');
  const [notes, setNotes] = useState('');
  const [requestedEvidence, setRequestedEvidence] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!reviewerDisplayName.trim() || !rationale.trim()) {
      setError('An unverified reviewer display name and written rationale are required.');
      return;
    }
    setSubmitting(true); setError(null);
    try {
      await traceApi.createReview(caseId, {
        reviewer_display_name: reviewerDisplayName.trim(),
        decision,
        rationale: rationale.trim(),
        notes: notes.trim() || undefined,
        requested_additional_evidence: requestedEvidence.split('\n').map((item) => item.trim()).filter(Boolean),
      });
      const updated = await traceApi.getReviews(caseId);
      onCreated(updated);
      setRationale(''); setNotes(''); setRequestedEvidence('');
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setSubmitting(false); }
  };

  return <>
    <section className="card">
      <div className="card-title"><span>Record human review</span><button onClick={onRefresh}>Refresh history</button></div>
      <p className="notice"><strong>Unverified identity:</strong> TRACE has no authentication. The supplied reviewer name is stored as unverified metadata. A human decision is separate from, and cannot change, the deterministic machine finding.</p>
      <form className="form-grid" onSubmit={submit}>
        <label>Reviewer display name<input maxLength={100} value={reviewerDisplayName} onChange={(e) => setReviewerDisplayName(e.target.value)} required /></label>
        <label>Human decision<select value={decision} onChange={(e) => setDecision(e.target.value as ReviewDecision)}>{REVIEW_DECISIONS.map((item) => <option key={item}>{item}</option>)}</select></label>
        <label className="wide">Required rationale<textarea maxLength={4000} value={rationale} onChange={(e) => setRationale(e.target.value)} required /></label>
        <label>Notes (optional)<textarea maxLength={4000} value={notes} onChange={(e) => setNotes(e.target.value)} /></label>
        <label>Requested additional evidence (optional; one per line)<textarea maxLength={4000} value={requestedEvidence} onChange={(e) => setRequestedEvidence(e.target.value)} /></label>
        <button disabled={submitting}>{submitting ? 'Recording review…' : 'Record human review'}</button>
      </form>
      <Notice error={error} />
    </section>
    <section className="card">
      <div className="card-title"><span>Review history</span></div>
      {!history ? <Loading label="Load review history to see human decisions and their machine-finding snapshots." /> : history.reviews.length === 0 ? <p className="muted">No human reviews have been recorded for this case.</p> : history.reviews.map((review) => <article className="group" key={review.review_id}>
        <h3>{review.decision}</h3><p className="muted">{new Date(review.created_at).toLocaleString()} · {review.reviewer_display_name} (identity unverified)</p>
        <p>{review.rationale}</p>{review.notes && <p className="muted">Notes: {review.notes}</p>}
        {review.requested_additional_evidence.length > 0 && <><h4>Reviewer-requested evidence</h4><ul>{review.requested_additional_evidence.map((item) => <li key={item}>{item}</li>)}</ul></>}
        <h4>Machine finding captured at review time</h4><p><span className="badge badge-outcome">{review.machine_finding.outcome}</span> <span className="badge badge-strength">{review.machine_finding.strength}</span></p><p className="muted">Rules {review.machine_finding.rule_version} · snapshot reference <code>{review.machine_finding.finding_reference}</code></p>
      </article>)}
    </section>
  </>;
}
