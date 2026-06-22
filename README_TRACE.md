# OCR Field Evidence & Source Tracing (Phase 2A)

This module implements a reusable evidence and source tracing framework for all OCR extracted, validated, and auto-filled fields.

## Schema Metadata

Each field trace tracks the following attributes:
* `field_name`: The canonical field name.
* `value`: The current raw/extracted value.
* `normalized_value`: Standardized value formats (cleaned dates/numeric formats) to make comparisons robust.
* `source_type`: One of:
  * `current_document`: OCR/regex extracted.
  * `training_dataset`: Merged from matching template layout.
  * `manual_correction`: Edited manually by operator.
  * `calculated_value`: Derived via math check rules.
  * `erp_master`: Resolved ERP mapping.
  * `fallback`: Gemini LLM fallback.
  * `unknown`
* `source_engine`: OCR engine (e.g. `paddleocr`), model (e.g. `gemini`), or system (e.g. `training_template`, `user`).
* `confidence`: Extracted or assigned confidence score.
* `validation_status`: `valid` or `invalid`.
* `validation_reason`: Warning/error details if invalid.
* `page_number`: Target page number (null for local heuristic regex).
* `evidence_text`: Substring or regex match context.
* `template_id` / `template_name`: Matched template layout metadata.
* `is_training_supplied`: `True` if derived from training template match.
* `is_current_document_supplied`: `True` if value was found in the raw document layout.
* `is_dynamic_field`: `True` for volatile fields (dates, invoice/PO numbers, quantity, prices, totals).
* `is_reusable_field`: `True` for stable static fields (salesperson, payment terms, addresses, carrier, contacts, currency).
* `conflict_status`: Training merge conflict resolution info.

## Response Payload

The `/api/v1/upload/jobs/{job_id}` GET endpoint exposes this mapping under:
- `source_traces`: Key-value map of canonical field name to SourceTrace metadata object.
- `ocr_response.source_traces`: Forward-compatible duplicate map.
