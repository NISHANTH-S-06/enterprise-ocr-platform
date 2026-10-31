# Phase 3A: Model Benchmark Readiness Audit

This document establishes the readiness baseline and plan for integrating and benchmarking additional OCR/VLM models into the OCR Enterprise Platform in an isolated, safe manner.

## 1. Current Baseline OCR Capability Summary

* **Active Engine**: PaddleOCR v3.x (`paddleocr==3.5.0` on top of `paddlepaddle==3.3.1` and `paddlex==3.5.2`) running on CPU.
* **oneDNN / PIR Safety**: Configured with `enable_mkldnn=False` and standard environment variables (`FLAGS_use_mkldnn=0`, `FLAGS_enable_pir_api=0`, `OMP_NUM_THREADS=1`, `PADDLE_CPP_LOGLEVEL=3`) to prevent PIR execution crashes under CPU runner.
* **Fallback Chain**: PaddleOCR (CPU) $\rightarrow$ pytesseract (v0.3.13) $\rightarrow$ MockOCR (test environment fallback).
* **Text Extraction**: Priortizes native PDF vector text extraction (using `PyMuPDF` v1.27.2) if text length exceeds 40 characters. Rasters and runs PaddleOCR on scanned images/scanned PDFs.
* **Metrics**: 100% accuracy on all 4 core documents in the benchmark suite, with **0 false positives** after Phase 2B stabilization.

---

## 2. Candidate Model Comparison Table

| Model | Purpose | Install Needed Later | Hardware Risk | Integration Risk | Recommended Order |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **PP-StructureV3** | Layout analysis and table/item-row structure parsing. | Minor weight downloads only; fits existing Paddle core. | Low CPU/memory overhead. | Low; shares Paddle core. | **1 (First)** |
| **PaddleOCR-VL** | Complex key-value and spatial document understanding. | requires `paddlex` VLM weights and dependencies. | Moderate RAM/CPU usage. | Moderate; model configuration. | **2 (Second)** |
| **DeepSeek OCR** | Multi-format text and visual layout parsing. | PyTorch, Hugging Face `transformers`, CUDA drivers. | High CPU/VRAM usage. | High; dependency conflicts. | **3 (Third)** |
| **Qwen-VL** | Advanced VLM for zero-shot invoice parsing. | PyTorch, `transformers`, model weights (~10GB). | Very High CPU/VRAM usage. | High; slow inference on CPU. | **4 (Fourth)** |

---

## 3. Recommended First Test Model & Justification

**PP-StructureV3** is recommended to be tested first because:
1. **Zero Library Upgrades**: It runs on top of the already installed and verified PaddlePaddle (`paddlepaddle==3.3.1`) framework.
2. **Minimal Footprint**: Low memory/CPU usage, matching current hardware baseline assumptions without requiring CUDA GPUs.
3. **Targeted Value**: It directly enhances layout detection and tabular parsing, which are crucial for heavy item-row documents (like `POPRINT.pdf` with 55 rows).

---

## 4. Proposed Adapter Architecture

To isolate new model integration and prevent behavior regressions, an adapter interface is defined:

```python
from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseOCRAdapter(ABC):
    @abstractmethod
    async def process_document(self, data: bytes, mime_type: str, **kwargs) -> Dict[str, Any]:
        """
        Extracts raw text and structures key-value fields.
        Returns a dictionary conforming to the Model Normalized Output Schema.
        """
        pass
```

### Timeout & Fallback Rules
* Each adapter execution must wrap in a strict timeout (e.g., 15 seconds) using `asyncio.wait_for`.
* If a timeout or runtime inference exception occurs, the system must **fail-soft** and immediately fall back to the baseline PaddleOCR engine, recording the failure in `warnings`.
* **Zero Regression**: If `ACTIVE_OCR_ADAPTER` is unset or set to `paddleocr`, the pipeline skips adapter logic entirely and uses the existing baseline path.

---

## 5. Proposed Normalized Model Output Schema

Adapters must return output matching the following schema:

```json
{
  "success": true,
  "text": "Extracted raw text...",
  "raw_text_length": 1050,
  "detected_line_count": 45,
  "avg_confidence": 0.88,
  "page_count": 1,
  "processing_ms": 320,
  "engine": "adapter_model_name",
  "mapped_fields": {
    "field_name": {
      "value": "extracted_value",
      "confidence": 0.92,
      "method": "regex/vlm",
      "source": "adapter_model_name",
      "source_evidence": "Evidence context from OCR/VLM output (bounding box or prompt text)"
    }
  },
  "item_rows": [
    {
      "row_index": "0",
      "product": "KNIFE BRUSH",
      "qty": "51",
      "price": "5.00",
      "total": "255.00"
    }
  ],
  "warnings": []
}
```

---

## 6. Model Benchmark Metrics

Candidate models will be evaluated against:
1. **Header Accuracy**: Correctly mapped header fields compared to ground truth.
2. **Item-Row Accuracy**: F1-score/exact match on table rows (SN, description, quantity, price, total).
3. **False Positives**: Count of fields extracted that are expected to be absent (`missing_fields`).
4. **Processing Time**: Core extraction latency (ms) per page.
5. **Memory Usage**: Peak RAM and VRAM footprint during inference.
6. **Source Trace Quality**: Bounding box coordinates or prompt evidence presence.
7. **Export Readiness**: Number of validation failures blocking export.

---

## 7. Dataset Requirements

The test suite must span:
* **Clean PDF**: Text-searchable invoice (vector PDF) to check native extraction paths.
* **Scanned Invoice**: Raster image to test OCR clarity and preprocessing.
* **Table-Heavy Document**: Document containing 20+ rows to test row segmentation.
* **Purchase Order**: PO structure verifying supplier and PO-specific headers.
* **GRN**: Document containing specific warehouse receipt notes.
* **Low-Quality/Rotated**: Noisy, skewed, or low-resolution image to stress-test layout binarization.

---

## 8. Risks and Mitigations

* **VLM Hallucination**:
  * *Risk*: VLM models inventing amounts or dates that are absent.
  * *Mitigation*: Run output values through `validation_service.py` strict format checkers (such as `GST_RE` or `AMOUNT_RE`) and clear mismatched values.
* **Slow CPU Inference**:
  * *Risk*: VLMs blocking worker processes for 15+ seconds per document.
  * *Mitigation*: Offload adapter processing to background execution workers with strict async timeouts.
* **Dependency Conflicts**:
  * *Risk*: Installing newer `torch` or `transformers` packages breaks existing Paddle core.
  * *Mitigation*: Run VLM models in containerized microservices, exposing them via lightweight REST APIs to the main backend.
* **Windows CUDA Compatibility**:
  * *Risk*: Complex CUDA setup on Windows hosts.
  * *Mitigation*: Confine model runner hosts to Linux Docker runtimes.

---

## 9. Phase 3B Execution Plan

1. **Adapter Registry**: Implement `BaseOCRAdapter` and register `PaddleOCRAdapter` as the default.
2. **Mock Adapter Integration**: Add a mock adapter shell for testing pipeline routing without installing packages.
3. **Isolated Test Environments**: Setup a Docker container with CUDA and PyTorch for testing DeepSeek/Qwen-VL.
4. **Benchmark Extension**: Update `benchmark_runner.py` to iterate over active adapters and produce side-by-side accuracy/performance reports.
5. **Regression Verification**: Ensure that the baseline PaddleOCR engine continues to pass with 0 false positives.
