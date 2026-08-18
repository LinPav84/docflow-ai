# DocFlow AI

DocFlow AI is a hackathon MVP for turning accounting documents—starting with
Kazakhstan inventory issue notes and invoices—into traceable, ERP-ready data.
Nutrient DWS performs extraction; DocFlow AI owns the deterministic domain logic,
review state, and auditability.

## Pipeline and architecture

```text
Document
→ Upload / Ingestion
→ Nutrient DWS extraction
→ Extraction Mapping
→ Line Items processing
→ Deterministic Normalization
→ Validation Engine
→ Human Review State
→ Approval
→ Audit trail
→ VERIFIED-only JSON / CSV export
```

Business logic, canonical schemas, normalization, validation, state transitions,
and auditability belong in Python and the application database. n8n may be added
later as an orchestration layer, but it is not the product architecture.

## Current milestone: Demo UI v1

Demo UI v1 is a credential-free, single-page demonstration of the complete DocFlow
review lifecycle. A lightweight FastAPI bridge owns one in-memory fixture session;
the Next.js App Router frontend renders only backend state and submits user actions.
Python remains the source of truth for normalization, deterministic validation,
review transitions, explicit approval, audit events, and exports.

```text
apps/web (Next.js / React / TypeScript)
        ↓ HTTP on localhost
docflow.api (FastAPI transport)
        ↓ controlled immutable-state replacement
docflow.demo (fixture lifecycle orchestration)
        ↓ existing functions only
normalization → validation → review → audit → export
```

### Run locally

Python 3.11+ and Node.js 20.9+ are required. From the repository root, install and
start the backend:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m uvicorn docflow.api:app --reload --port 8000
```

In a second terminal, start the frontend:

```bash
cd apps/web
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The frontend uses
`http://localhost:8000` by default; set `NEXT_PUBLIC_DOCFLOW_API_URL` before the
frontend command only when the local API runs elsewhere. Development CORS is
restricted to `localhost:3000` and `127.0.0.1:3000`, not a wildcard.

### Demo Fixture Mode

**Load demo document** uses the checked-in real Sintech schema-extraction response,
so it needs no Nutrient key or network access. The demo service deliberately changes
only the first line total from `167 881,00` to `167 981,00`. Existing Python domain
functions then produce `LINE_TOTAL_MISMATCH` and `GRAND_TOTAL_MISMATCH` honestly.

The API keeps one document in memory for a single-process hackathon demo. It uses a
lock and replaces immutable `ReviewSession` and `AuditTrail` snapshots; it is not a
database or production persistence layer. **Reset demo** reconstructs the original
REVIEW scenario, revision zero, and its four initial audit events. Restarting the API
also loses current demo state. Live Nutrient upload is intentionally not included.

### Demo API

- `GET /api/health` — fixture-mode health check;
- `POST /api/demo/start` — load a fresh REVIEW fixture lifecycle;
- `POST /api/demo/reset` — restore the intentional REVIEW state;
- `GET /api/demo/state` — read the current in-memory state;
- `POST /api/demo/correct` — submit `field_path` and `raw_value` to `apply_correction()`;
- `POST /api/demo/verify` — call `approve_review()` for a PASS session;
- `GET /api/demo/export/json` — canonical VERIFIED-only JSON;
- `GET /api/demo/export/csv` — VERIFIED-only flat line-item CSV.

The UI does not contain accounting validation rules. Corrections are never patched
locally: each response is the new Python-owned session, validation result, revision,
and audit trail. Before VERIFIED, both export endpoints return a controlled rejection.

### Judge demo script

1. Click **Load demo document**.
2. Observe **REVIEW** and two accounting issues.
3. Correct the line total to `167 881,00` and click **Apply correction**.
4. Observe **PASS**; note that the document is not VERIFIED yet.
5. Click **Verify document**.
6. Observe **VERIFIED** and the explicit approval confirmation.
7. Inspect the eight-event audit trail, including old/new correction values.
8. Download canonical JSON and flat CSV.
9. Click **Reset demo** to restore the starting scenario for another run.

### Screenshots

Screenshots can be added after external review under `docs/screenshots/`. The app
already provides dedicated upload, REVIEW, PASS, and VERIFIED states for recording.

Demo UI v1 does not add authentication, users, persistence, Supabase, billing,
dashboards, document lists, 1C/ERP integration, XLSX, n8n, confidence scoring, AI
correction, multi-document state, or complex PDF rendering.

## Export Layer v1

Export Layer v1 exposes pure in-memory exports for explicitly VERIFIED documents:

- `export_verified_json(session, audit_trail)` returns a canonical structured object;
- `export_verified_csv(session)` returns flat line-item CSV text with document context
  repeated on every row.

PASS alone is not exportable. Both functions require `ReviewStatus.VERIFIED`, a PASS
validation result, and a fresh defensive validation match. JSON additionally
requires an audit trail ending in `DOCUMENT_VERIFIED` at the same review revision.
Stale, tampered, or mismatched state is rejected with controlled export errors.

Accounting fields use normalized values. Identifiers remain strings, missing values
become JSON `null` or empty CSV cells, and every `Decimal` is rendered as a plain
non-scientific string without converting through `float`. JSON embeds the existing
safe audit representation in deterministic event order. CSV uses Python's standard
`csv` module, fixed column order, RFC-style quoting, Unicode text, and one row per
source-order line item.

The export domain functions write no files. Demo UI v1 now exposes them through
credential-free HTTP download routes after VERIFIED; XLSX, server-side file
persistence, database storage, Supabase, n8n, 1C/ERP connectors, confidence scoring,
AI, and PDF export remain unimplemented.

## Audit Trail v1

Audit Trail v1 provides a document-scoped, append-only `AuditTrail` containing an
immutable tuple of typed `AuditEvent` records. Events use UUID identifiers,
timezone-aware UTC timestamps, contiguous sequences starting at 1, and explicit
review revisions. Appending an event returns a new trail; previous events and trail
objects remain unchanged.

Typed immutable payloads record only lifecycle facts needed for traceability:
ingestion metadata, extraction completion, ordered validation reason codes, review
start state, exact correction raw values, explicit approval, and the resulting
VERIFIED state. Events never contain API keys, source bytes, authorization headers,
or complete provider responses. All events in one trail must share the same
ingestion document ID, and correction revisions cannot move backwards.

`audit_trail_to_dict()` prepares an ordered JSON-safe in-memory representation with
UTC ISO timestamps and safe Decimal strings. It does not write files. Audit Trail v1
is domain logic only and remains in memory; database persistence, migrations, audit
viewer UI, identities, permissions, and exports are later milestones.

## Human Review State v1

The immutable human-review domain workflow is:

```text
REVIEW
→ raw correction
→ existing deterministic normalization
→ immediate revalidation
→ PASS
→ explicit human approval
→ VERIFIED
```

`start_review()` freshly validates the document and rejects a supplied validation
result unless its decision and complete ordered issue set match exactly. A review
session therefore cannot begin with stale or fabricated validation state.
`apply_correction()` accepts only supported document or indexed line-item field
paths, records an append-only raw-value correction, rebuilds the frozen document
through `normalize_document()`, and immediately calls `validate_document()`.
Successful corrections increment the revision exactly once; failed paths or
normalization errors leave the prior session unchanged.

`approve_review()` performs a fresh validation check and only permits explicit
approval when the current effective document is `PASS`. **PASS and VERIFIED are not
the same state**: PASS is a machine validation outcome, while VERIFIED records a
separate human approval action. Any later correction removes VERIFIED status and
requires another explicit approval. Row count and source order cannot be changed by
the v1 correction API.

This milestone contains backend domain logic only. It adds no UI, authentication,
database audit trail, user identity, confidence scoring, AI correction, exports, or
row add/delete operations.

## Validation Engine v1

`docflow.validate_document(document)` applies deterministic accounting checks to an
immutable `NormalizedDocument` and returns an immutable `ValidationResult` with a
`PASS`, `REVIEW`, or `FAIL` decision plus ordered, path-aware issues. Current
user-correctable problems produce `REVIEW`; `FAIL` is reserved for structurally
unusable validation inputs and is intentionally rare.

Validation v1 checks required document and line-item fields, currency presence,
12-ASCII-digit Kazakhstan tax ID formatting, positive quantities, line arithmetic,
grand-total reconciliation, and VAT-total reconciliation. All arithmetic uses
`Decimal`, never `float`. Monetary comparisons pass when the absolute difference is
at most `0.02` **or** the relative difference is at most `0.0001` (0.01%); zero
expected values use the absolute rule safely. Form Z-2 grand total is the sum of
listed line totals, with VAT already included and never added again.

Checks never mutate, infer, calculate missing inputs, correct, or normalize the
source document. Issue order is deterministic: document requirements, tax IDs,
line items in source order, grand total, then VAT total. AI/provider confidence
scoring is **not implemented** because the normalized models do not contain a
trustworthy confidence signal.

## Line Items v1

`docflow.map_line_items_from_extraction(response_json)` is the structural boundary
between Nutrient's schema-based `/extract` response and downstream normalization.
It reads only `output.data.line_items` and returns an immutable tuple of
`MappedLineItem` rows.

This layer preserves source order, duplicate rows, and exact raw field values. Each
row receives a zero-based `source_index` and a stable `source_path`, such as
`line_items[0]`; `field_path(name)` derives paths such as
`line_items[0].quantity`. Missing or null `line_items` maps to an empty tuple,
missing row fields remain `None`, and unknown provider fields are ignored. Malformed
envelopes, non-list `line_items`, and non-object rows raise a controlled,
path-aware `LineItemMappingError`.

Line-item mapping makes no accounting decisions: it does not sort, deduplicate,
merge, infer, calculate, correct OCR text, or convert numbers. Deterministic
normalization remains responsible for conversions such as raw numeric strings to
`Decimal`; the Validation Engine owns the implemented required-field, arithmetic,
VAT, and business-consistency checks.

## Upload / ingestion

The ingestion layer accepts one local document, validates it at the boundary,
assigns a UUID document ID, calls the official Nutrient DWS Data Extraction API,
and preserves the exact successful response bytes before any mapping,
normalization, or validation.

Supported MVP file types are deliberately limited to:

- PDF (`.pdf`, `application/pdf`);
- JPEG (`.jpg` or `.jpeg`, `image/jpeg`);
- PNG (`.png`, `image/png`).

Both the extension and the file signature are checked. Uploads exceeding the
configured size limit are rejected before the provider request. The original
filename is retained as metadata and is never used as document identity.

The Nutrient-specific HTTP code is isolated in `docflow.nutrient.NutrientClient`.
Nutrient exposes two distinct Data Extraction paths:

- **Parse** (`/extraction/parse`) returns full-document Markdown or spatial structure;
- **Extract** (`/extraction/extract`) returns business fields shaped by a supplied JSON Schema.

DocFlow uses **Extract** because the current pipeline requires Form Z-2 accounting
fields rather than a spatial document representation. It follows Nutrient's
current multipart contract:

```text
POST https://api.nutrient.io/extraction/extract
multipart: file + instructions
instructions: {
  "schema": { ... },
  "parseConfig": {"mode":"understand"},
  "instructions": "document-level extraction guidance"
}
```

The initial schema lives in `docflow.schemas`, outside the HTTP client. It declares
the approved Form Z-2 header, line-item, and total fields as strings so identifiers
keep leading zeroes and locale-formatted accounting values remain available to the
deterministic normalization layer. Fields are optional and the extraction guidance
explicitly forbids inference, calculation, semantic correction, row merging, or
invented values. `IngestionService` accepts a different schema, instructions, and
parse mode when a later approved document type requires them.

The service uses an explicit timeout, handles authentication and HTTP failures,
and never includes the API key in controlled exception messages.

Example:

```python
from pathlib import Path

from docflow import IngestionService, NutrientClient

service = IngestionService(nutrient_client=NutrientClient())
result = service.ingest(Path("safe-test-document.pdf"))

print(result.metadata.document_id)
print(result.metadata.raw_response_path)
```

Successful response bytes are saved unchanged at:

```text
artifacts/raw/{document_id}/nutrient_response.json
```

The artifact root is configurable through `IngestionService`, allowing tests and
deployments to use an isolated location. Raw artifacts are ignored by Git because
they can contain confidential accounting data.

## Deterministic normalization

The previously approved normalization layer remains available through
`docflow.normalize_document(payload)`. It returns typed, immutable models in which
every field has both the exact `raw_value` and a normalized `value`.

Current behavior includes:

- empty and whitespace-only strings become `None`;
- money and quantities use `Decimal`, never `float`;
- spaces are accepted as validated thousands separators;
- comma and dot decimal formats are supported when unambiguous;
- ISO `YYYY-MM-DD` dates are preserved;
- document numbers, tax IDs, SKUs, and barcodes remain strings, including leading zeros;
- line items retain their order and are normalized independently;
- malformed or ambiguous numbers and dates raise a path-aware `NormalizationError`;
- no missing value is inferred or calculated.

Example:

```python
from decimal import Decimal

from docflow import normalize_document

document = normalize_document(
    {
        "document_number": "139",
        "grand_total": "167 881,00",
        "line_items": [{"sku": "00000002498", "quantity": "1"}],
    }
)

assert document.grand_total.raw_value == "167 881,00"
assert document.grand_total.value == Decimal("167881.00")
assert document.line_items[0].sku.value == "00000002498"
```

## Development setup

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Required environment variables:

- `NUTRIENT_API_KEY` — required for provider calls and read only from the environment;
- `DOCFLOW_MAX_FILE_SIZE_MB` — optional positive size limit in MB; defaults to `10`.

`.env.example` contains empty placeholders only. The project does not load `.env`
files automatically; inject secrets through the process environment or a secret
manager.

Run tests and static formatting/import checks:

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

The approved Run B data fixture is `fixtures/nutrient/sintech_run_b.json`. A
schema-based `/extract` response envelope containing the same data is available at
`fixtures/nutrient/sintech_extract_response.json` for mocked ingestion tests.

## Optional manual smoke test

Unit tests never call Nutrient and do not require an API key. To perform one
optional live smoke test, first provide `NUTRIENT_API_KEY` through a secure process
environment and use only a non-sensitive document:

```bash
python -c 'from pathlib import Path; from docflow import IngestionService, NutrientClient; result = IngestionService(nutrient_client=NutrientClient()).ingest(Path("safe-test-document.pdf")); print(result.metadata)'
```

This makes one billable/provider request and writes the raw response under
`artifacts/raw/`. Do not use a private customer document for a development smoke
test.

## Security

API keys, credentials, `.env` files, raw development artifacts, uploaded files,
and unapproved confidential documents must never be committed. Unit tests use
`httpx.MockTransport`, so the test suite makes no real HTTP calls.

## Roadmap

Authentication, production persistence, database audit storage, live multi-document
workflows, XLSX, and ERP integrations remain later milestones. Confidence scoring is
**not implemented** and must not be fabricated without trustworthy source confidence
values.
