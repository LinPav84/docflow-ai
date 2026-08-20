"use client";

import { useMemo, useState } from "react";

type Status = "REVIEW" | "PASS" | "VERIFIED" | "FAILED";

type ValidationIssue = {
  reason_code: string;
  field_path: string;
  message: string;
  severity: string;
  expected: string | number | null;
  actual: string | number | null;
};

type AuditEvent = {
  event_id: string;
  event_type: string;
  timestamp: string;
  sequence: number;
  revision: number;
  data: Record<string, unknown>;
};

type DocumentSummary = {
  document_number: string | null;
  document_date: string | null;
  supplier_name: string | null;
  supplier_tax_id: string | null;
  buyer_name: string | null;
  buyer_tax_id: string | null;
  currency: string | null;
  responsible_person: string | null;
  subtotal: string | null;
  vat_total: string | null;
  grand_total: string | null;
};

type LineItem = {
  source_index: number;
  line_number: string | null;
  product_description: string | null;
  sku: string | null;
  barcode: string | null;
  unit: string | null;
  quantity: string | null;
  unit_price: string | null;
  vat_amount: string | null;
  line_total: string | null;
  line_total_raw: string | null;
};

type DemoState = {
  document_id: string;
  mode: "fixture";
  status: Status;
  revision: number;
  can_verify: boolean;
  can_export: boolean;
  document: DocumentSummary;
  line_items: LineItem[];
  validation: {
    decision: Status;
    issue_count: number;
    issues: ValidationIssue[];
  };
  audit: {
    document_id: string;
    events: AuditEvent[];
  };
};

const API_URL = process.env.NEXT_PUBLIC_DOCFLOW_API_URL ?? "http://localhost:8000";
const CORRECTION_PATH = "line_items[0].line_total";
const PROCESSING_STAGES = ["Uploading", "Extracting", "Normalizing", "Validating"];

const AUDIT_LABELS: Record<string, string> = {
  DOCUMENT_INGESTED: "Document ingested",
  EXTRACTION_COMPLETED: "Extraction completed",
  VALIDATION_COMPLETED: "Validation completed",
  REVIEW_STARTED: "Review started",
  CORRECTION_APPLIED: "Correction applied",
  DOCUMENT_APPROVED: "Document approved",
  DOCUMENT_VERIFIED: "Document verified",
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new Error("The DocFlow backend is unavailable. Start the API and try again.");
  }
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? "The request could not be completed.");
  }
  return (await response.json()) as T;
}

function displayAmount(value: string | number | null): string {
  if (value === null) return "—";
  const [whole, fraction] = String(value).replace(/\s/g, "").split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  return fraction === undefined ? grouped : `${grouped},${fraction}`;
}

function shortTime(value: string): string {
  return new Intl.DateTimeFormat("en", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function StatusBadge({ status }: { status: Status }) {
  return (
    <span className={`status-badge status-${status.toLowerCase()}`}>
      <span className="status-dot" aria-hidden="true" />
      {status}
    </span>
  );
}

export default function Home() {
  const [state, setState] = useState<DemoState | null>(null);
  const [processingStage, setProcessingStage] = useState<string | null>(null);
  const [correction, setCorrection] = useState("167 881,00");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showAudit, setShowAudit] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const problemPaths = useMemo(
    () => new Set(state?.validation.issues.map((issue) => issue.field_path) ?? []),
    [state],
  );

  async function loadDemo() {
    setError(null);
    setIsSubmitting(true);
    setProcessingStage(PROCESSING_STAGES[0]);
    try {
      const result = await request<DemoState>("/api/demo/start", { method: "POST" });
      for (const stage of PROCESSING_STAGES.slice(1)) {
        setProcessingStage(stage);
        await new Promise((resolve) => window.setTimeout(resolve, 90));
      }
      setState(result);
      setCorrection(result.line_items[0]?.line_total_raw ?? "167 881,00");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The demo could not be loaded.");
    } finally {
      setProcessingStage(null);
      setIsSubmitting(false);
    }
  }

  async function applyCorrection() {
    setError(null);
    setIsSubmitting(true);
    try {
      const result = await request<DemoState>("/api/demo/correct", {
        method: "POST",
        body: JSON.stringify({ field_path: CORRECTION_PATH, raw_value: correction }),
      });
      setState(result);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The correction was rejected.");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function verifyDocument() {
    setError(null);
    setIsSubmitting(true);
    try {
      setState(await request<DemoState>("/api/demo/verify", { method: "POST" }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Verification was not allowed.");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function resetDemo() {
    setError(null);
    setIsSubmitting(true);
    try {
      const result = await request<DemoState>("/api/demo/reset", { method: "POST" });
      setState(result);
      setCorrection(result.line_items[0]?.line_total_raw ?? "");
      setShowAudit(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The demo could not be reset.");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function downloadExport(format: "json" | "csv") {
    setError(null);
    try {
      const response = await fetch(`${API_URL}/api/demo/export/${format}`);
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(payload?.detail ?? "Export is not available.");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `docflow-document-139.${format}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Export could not be downloaded.");
    }
  }

  return (
    <main className="app-shell">
      <header className="site-header">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            D
          </div>
          <div>
            <div className="brand-name">DocFlow AI</div>
            <div className="brand-caption">Verified document operations</div>
          </div>
        </div>
        <div className="header-actions">
          <span className="mode-pill"><span aria-hidden="true">●</span> Demo fixture</span>
          {state && (
            <button className="button button-quiet" onClick={resetDemo} disabled={isSubmitting}>
              Reset demo
            </button>
          )}
        </div>
      </header>

      {error && (
        <div className="error-banner" role="alert">
          <span className="error-icon" aria-hidden="true">!</span>
          <span>{error}</span>
          <button aria-label="Dismiss error" onClick={() => setError(null)}>×</button>
        </div>
      )}

      {!state ? (
        <section className="upload-screen">
          <div className="eyebrow">DOCUMENT OPERATIONS, VERIFIED</div>
          <h1>From business documents<br />to verified data.</h1>
          <p className="hero-copy">
            AI extraction + deterministic validation + human review + audit trail.
          </p>

          <div className="upload-card">
            <div className="demo-entry">
              <div className="demo-entry-label">FULL PRODUCT WALKTHROUGH</div>
              <button className="button button-primary demo-primary" onClick={loadDemo} disabled={isSubmitting}>
                <span aria-hidden="true">▶</span> Load demo document
              </button>
              <p>Run the complete credential-free review flow</p>
            </div>

            <div className="capability-divider" aria-hidden="true">
              <span>Future capability</span>
            </div>

            <div className="drop-zone drop-zone-disabled" aria-disabled="true">
              <span className="upload-icon" aria-hidden="true">↑</span>
              <strong>Upload your document</strong>
              <span>Coming soon — live document processing</span>
              <small>PDF, JPG or PNG</small>
            </div>
          </div>

          {processingStage && (
            <div className="processing-card" role="status" aria-live="polite">
              <div className="processing-title">Preparing review workspace</div>
              <div className="stage-list">
                {PROCESSING_STAGES.map((stage) => {
                  const currentIndex = PROCESSING_STAGES.indexOf(processingStage);
                  const stageIndex = PROCESSING_STAGES.indexOf(stage);
                  return (
                    <div className={stageIndex <= currentIndex ? "stage active" : "stage"} key={stage}>
                      <span>{stageIndex < currentIndex ? "✓" : stageIndex === currentIndex ? "•" : ""}</span>
                      {stage}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <div className="trust-row" aria-label="Demo characteristics">
            <span>◇ Deterministic validation</span>
            <span>⌁ Human-in-the-loop</span>
            <span>✓ Audit-ready output</span>
          </div>
        </section>
      ) : (
        <ReviewWorkspace
          state={state}
          correction={correction}
          setCorrection={setCorrection}
          isSubmitting={isSubmitting}
          problemPaths={problemPaths}
          showAudit={showAudit}
          setShowAudit={setShowAudit}
          applyCorrection={applyCorrection}
          verifyDocument={verifyDocument}
          downloadExport={downloadExport}
        />
      )}
    </main>
  );
}

type WorkspaceProps = {
  state: DemoState;
  correction: string;
  setCorrection: (value: string) => void;
  isSubmitting: boolean;
  problemPaths: Set<string>;
  showAudit: boolean;
  setShowAudit: (value: boolean) => void;
  applyCorrection: () => Promise<void>;
  verifyDocument: () => Promise<void>;
  downloadExport: (format: "json" | "csv") => Promise<void>;
};

function ReviewWorkspace(props: WorkspaceProps) {
  const { state } = props;
  const item = state.line_items[0];
  const correctionIssue = state.validation.issues.find(
    (issue) => issue.field_path === CORRECTION_PATH,
  );
  return (
    <div className="workspace">
      <div className="document-bar">
        <div>
          <div className="breadcrumb">DOCUMENT REVIEW <span>/</span> FORM Z-2</div>
          <div className="document-title-row">
            <h1>Document #{state.document.document_number}</h1>
            <StatusBadge status={state.status} />
          </div>
          <p>{state.document.supplier_name}</p>
        </div>
        <div className="document-bar-actions">
          <div className="revision-label">Revision {state.revision}</div>
          <button className="button button-quiet" onClick={() => props.setShowAudit(!props.showAudit)}>
            {props.showAudit ? "Hide" : "Open"} audit trail
          </button>
          {state.status === "PASS" && (
            <button
              className="button button-verify"
              onClick={props.verifyDocument}
              disabled={props.isSubmitting}
            >
              Verify document <span aria-hidden="true">→</span>
            </button>
          )}
        </div>
      </div>

      {state.status === "VERIFIED" && (
        <div className="verified-banner" role="status">
          <span className="verified-check" aria-hidden="true">✓</span>
          <div>
            <strong>Document verified</strong>
            <span>Validated and explicitly approved.</span>
          </div>
          <div className="export-actions">
            <button className="button button-export" onClick={() => props.downloadExport("json")}>
              ↓ Download JSON
            </button>
            <button className="button button-export" onClick={() => props.downloadExport("csv")}>
              ↓ Download CSV
            </button>
          </div>
        </div>
      )}

      <div className={`workspace-grid ${props.showAudit ? "with-audit" : ""}`}>
        <section className="preview-panel" aria-label="Document preview">
          <div className="panel-heading">
            <div><span className="panel-icon">▤</span> Document preview</div>
            <span>Fixture summary</span>
          </div>
          <DocumentPreview document={state.document} item={item} />
        </section>

        <section className="data-panel" aria-label="Structured document data">
          <ValidationSummary state={state} />
          <DocumentFields document={state.document} />
          <LineItemsTable
            item={item}
            status={state.status}
            problemPaths={props.problemPaths}
            correction={props.correction}
            expectedCorrection={correctionIssue?.expected ?? null}
            setCorrection={props.setCorrection}
          />
          {state.status === "REVIEW" && props.problemPaths.has(CORRECTION_PATH) && (
            <div className="correction-footer">
              <div>
                <strong>Ready to correct?</strong>
                <span>The backend will normalize and revalidate this raw value.</span>
              </div>
              <button
                className="button button-primary"
                onClick={props.applyCorrection}
                disabled={props.isSubmitting || !props.correction.trim()}
              >
                Apply correction
              </button>
            </div>
          )}
        </section>

        {props.showAudit && <AuditTimeline events={state.audit.events} />}
      </div>
    </div>
  );
}

function ValidationSummary({ state }: { state: DemoState }) {
  if (state.status === "PASS") {
    return (
      <div className="validation-card validation-pass">
        <span className="validation-symbol" aria-hidden="true">✓</span>
        <div>
          <div className="section-kicker">VALIDATION</div>
          <h2>No accounting inconsistencies detected.</h2>
          <p>Machine validation passed. Explicit verification is still required.</p>
        </div>
      </div>
    );
  }
  if (state.status === "VERIFIED") {
    return (
      <div className="validation-card validation-verified">
        <span className="validation-symbol" aria-hidden="true">✓</span>
        <div>
          <div className="section-kicker">VALIDATION</div>
          <h2>Approved for export.</h2>
          <p>The document is PASS and has been explicitly verified.</p>
        </div>
      </div>
    );
  }
  return (
    <div className="validation-section">
      <div className="validation-heading">
        <div>
          <div className="section-kicker">VALIDATION</div>
          <h2>{state.validation.issue_count} issues found</h2>
        </div>
        <span className="review-required">Review required</span>
      </div>
      <div className="issue-list">
        {state.validation.issues.map((issue) => (
          <article className="issue-card" key={`${issue.reason_code}-${issue.field_path}`}>
            <span className="issue-symbol" aria-hidden="true">!</span>
            <div className="issue-content">
              <div className="issue-code">{issue.reason_code}</div>
              <div className="issue-path">{issue.field_path}</div>
              <p>{issue.message}</p>
              <div className="issue-values">
                <span>Expected <strong>{displayAmount(issue.expected)}</strong></span>
                <span>Actual <strong>{displayAmount(issue.actual)}</strong></span>
              </div>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

function DocumentFields({ document }: { document: DocumentSummary }) {
  const fields = [
    ["Document number", document.document_number],
    ["Date", document.document_date],
    ["Supplier", document.supplier_name],
    ["Supplier BIN", document.supplier_tax_id],
    ["Buyer", document.buyer_name],
    ["Currency", document.currency],
    ["Grand total", `${displayAmount(document.grand_total)} ${document.currency ?? ""}`],
  ];
  return (
    <section className="fields-section">
      <div className="subsection-title">Document fields <span>Normalized values</span></div>
      <dl className="field-grid">
        {fields.map(([label, value]) => (
          <div className={label === "Supplier" || label === "Buyer" ? "field wide" : "field"} key={label}>
            <dt>{label}</dt>
            <dd>{value || "—"}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function LineItemsTable({
  item,
  status,
  problemPaths,
  correction,
  expectedCorrection,
  setCorrection,
}: {
  item: LineItem;
  status: Status;
  problemPaths: Set<string>;
  correction: string;
  expectedCorrection: string | number | null;
  setCorrection: (value: string) => void;
}) {
  const lineProblem = problemPaths.has(CORRECTION_PATH);
  return (
    <section className="line-items-section">
      <div className="subsection-title">Line items <span>1 item</span></div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th><th>Product</th><th>SKU</th><th>Qty</th><th>Unit</th>
              <th>Unit price</th><th>VAT</th><th>Line total</th><th>Status</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>{item.line_number}</td>
              <td className="product-cell">{item.product_description}</td>
              <td className="mono">{item.sku}</td>
              <td>{displayAmount(item.quantity)}</td>
              <td>{item.unit}</td>
              <td>{displayAmount(item.unit_price)}</td>
              <td>{displayAmount(item.vat_amount)}</td>
              <td className={lineProblem ? "problem-cell" : "resolved-cell"}>
                {lineProblem && status === "REVIEW" ? (
                  <label>
                    <span className="sr-only">Correct line total</span>
                    <input
                      value={correction}
                      onChange={(event) => setCorrection(event.target.value)}
                      aria-describedby="line-total-help"
                    />
                    <small id="line-total-help">
                      Expected {displayAmount(expectedCorrection)}
                    </small>
                  </label>
                ) : (
                  <strong>{displayAmount(item.line_total)}</strong>
                )}
              </td>
              <td>
                <span className={lineProblem ? "row-state row-review" : "row-state row-valid"}>
                  {lineProblem ? "! Review" : "✓ Valid"}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}

function DocumentPreview({ document, item }: { document: DocumentSummary; item: LineItem }) {
  return (
    <div className="paper-wrap">
      <div className="paper">
        <div className="paper-topline">Форма З-2</div>
        <h3>НАКЛАДНАЯ НА ОТПУСК ЗАПАСОВ<br />НА СТОРОНУ</h3>
        <div className="paper-meta">
          <span>Номер документа<br /><strong>{document.document_number}</strong></span>
          <span>Дата составления<br /><strong>{document.document_date}</strong></span>
        </div>
        <div className="paper-fields">
          <p><span>Организация</span>{document.supplier_name}</p>
          <p><span>БИН</span>{document.supplier_tax_id}</p>
          <p><span>Получатель</span>{document.buyer_name}</p>
        </div>
        <table className="paper-table">
          <thead><tr><th>№</th><th>Наименование</th><th>Код</th><th>Кол.</th><th>Цена</th><th>Сумма</th></tr></thead>
          <tbody><tr><td>1</td><td>{item.product_description}</td><td>{item.sku}</td><td>{item.quantity}</td><td>{displayAmount(item.unit_price)}</td><td>{displayAmount(item.line_total)}</td></tr></tbody>
        </table>
        <div className="paper-total"><span>Итого</span><strong>{displayAmount(document.grand_total)} ₸</strong></div>
        <div className="paper-signatures"><span>Отпустил __________</span><span>Получил __________</span></div>
      </div>
      <div className="preview-note"><span aria-hidden="true">i</span> Fixture summary preview · Original PDF is not stored</div>
    </div>
  );
}

function AuditTimeline({ events }: { events: AuditEvent[] }) {
  return (
    <aside className="audit-panel" aria-label="Audit trail">
      <div className="panel-heading">
        <div><span className="panel-icon">↳</span> Audit trail</div>
        <span>{events.length} events</span>
      </div>
      <div className="audit-intro">
        <span className="audit-shield" aria-hidden="true">✓</span>
        <div><strong>Immutable history</strong><span>Every state transition is recorded.</span></div>
      </div>
      <ol className="timeline">
        {events.map((event) => {
          const decision = event.data.decision;
          const isCorrection = event.event_type === "CORRECTION_APPLIED";
          return (
            <li key={event.event_id}>
              <span className={`timeline-dot ${event.event_type === "DOCUMENT_VERIFIED" ? "complete" : ""}`} aria-hidden="true">✓</span>
              <div className="timeline-entry">
                <div className="timeline-title">
                  <strong>{AUDIT_LABELS[event.event_type] ?? event.event_type}</strong>
                  <time>{shortTime(event.timestamp)}</time>
                </div>
                <div className="event-type">{event.event_type}</div>
                <div className="event-meta">
                  <span>Revision {event.revision}</span>
                  {typeof decision === "string" && <span className={`mini-status mini-${decision.toLowerCase()}`}>{decision}</span>}
                </div>
                {isCorrection && (
                  <div className="correction-audit">
                    <code>{String(event.data.field_path)}</code>
                    <div><span>Old</span><del>{String(event.data.old_raw_value)}</del></div>
                    <div><span>New</span><ins>{String(event.data.new_raw_value)}</ins></div>
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </aside>
  );
}
