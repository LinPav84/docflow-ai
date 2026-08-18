# DocFlow AI

DocFlow AI is a hackathon MVP for turning accounting documents—starting with
Kazakhstan inventory issue notes and invoices—into traceable, ERP-ready data.
Nutrient DWS performs extraction; DocFlow AI owns the deterministic domain logic,
review state, and auditability.

## Pipeline and architecture

```text
Document
→ Nutrient DWS extraction
→ Canonical Mapping
→ Deterministic Normalization
→ Validation
→ Confidence / Review decision
→ Human Review
→ Approval
→ JSON / CSV / XLSX / ERP-ready export
→ Audit trail
```

Business logic, canonical schemas, normalization, validation, state transitions,
and auditability belong in Python and the application database. n8n may be added
later as an orchestration layer, but it is not the product architecture.

## Current milestone: deterministic normalization

Only deterministic normalization is implemented. The public API is
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

Run tests and static formatting/import checks:

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

The primary real extraction fixture is
`fixtures/nutrient/sintech_run_b.json`.

## Security

API keys, credentials, `.env` files, and unapproved confidential documents must
never be committed. This milestone has no external API dependency and makes no
network calls.

## Roadmap

The **Validation Engine is the next milestone**. It is intentionally not part of
the normalization implementation and must not begin until this milestone has been
externally reviewed and approved.
