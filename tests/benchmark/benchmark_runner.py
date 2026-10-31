#!/usr/bin/env python3
"""
benchmark_runner.py — OCR Accuracy Benchmark Runner.
Processes documents through the active service pipeline, computes detailed metrics
against ground-truth JSON files, generates JSON/Markdown reports, and supports
baseline comparison.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Insert workspace root to sys.path
WORKSPACE_ROOT = Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(WORKSPACE_ROOT))

# Backend service imports
from app.services.ocr_service import process_file
from app.services.company_form_mapper import auto_fill, map_sales_invoice_fields, classify_document, normalize_mapped_fields
from app.services.validation_service import validate_mapped_fields


# ── Helpers ──────────────────────────────────────────────────────────────────

def normalize_str(val: Any) -> str:
    """Normalize string to make comparisons robust to punctuation/casing/whitespace."""
    if val is None:
        return ""
    s = str(val).strip().lower()
    s = re.sub(r"[\s,;._'’₹$€£%\-!]+", "", s)
    return s


def is_date_or_amount_field(fieldname: str) -> bool:
    """Classify field name to separate headers from dates and amounts."""
    low = fieldname.lower()
    return any(term in low for term in ("date", "total", "amount", "price", "rate", "value", "charges", "igst", "tax", "ex_rate", "exchange"))


def compare_values(actual: Any, expected: Any) -> bool:
    """Compare normalized values."""
    return normalize_str(actual) == normalize_str(expected)


# ── Pipeline Runner ──────────────────────────────────────────────────────────

async def run_document_pipeline(file_path: Path, filename: str) -> Dict[str, Any]:
    """Execute the backend OCR + extraction service pipeline directly."""
    t0 = time.monotonic()
    
    with open(file_path, "rb") as f:
        data = f.read()
    
    # 1. Run OCR service
    ocr_result = await process_file(data, "application/pdf")
    text = ocr_result.get("text", "")
    
    # 2. Run Classification
    cls_result = classify_document(text, filename=filename)
    doc_type = cls_result["doc_type"]
    mapper = cls_result["mapper"]
    
    # 3. Run Extraction (Sales vs AutoFill)
    if mapper == "sales":
        mapped_result = map_sales_invoice_fields(text)
        from app.services.company_form_mapper import flatten_item_rows_for_legacy_fields
        mapped_result = flatten_item_rows_for_legacy_fields(mapped_result, doc_type, text)
    else:
        mapped_result = auto_fill(text, doc_type=doc_type.lower())
    
    # 4. Normalize mapped fields
    mapped_fields = normalize_mapped_fields(mapped_result.get("mapped_fields", {}))
    
    # 5. Extract flat dict of values
    flat_fields = {}
    for k, v in mapped_fields.items():
        if k == "item_rows":
            continue
        val = v.get("value") if isinstance(v, dict) else v
        if val is not None:
            flat_fields[k] = val
            
    item_rows = mapped_fields.get("item_rows", [])
    if isinstance(item_rows, dict):
        item_rows = item_rows.get("value", [])
        
    # 6. Check if training matched (optional, requires DB context; default to false if not running in API)
    training_template_matched = ocr_result.get("training_template_matched", False)
    
    # 7. Run validation and build source traces
    validation = validate_mapped_fields(
        mapped_fields,
        raw_text=text,
        doc_type=doc_type,
    )
    
    from app.services.source_trace import build_source_traces
    trace_meta = {
        "ocr_engine": ocr_result.get("ocr_engine") or ocr_result.get("engine") or "paddleocr",
    }
    source_traces = build_source_traces(
        mapped_fields=mapped_fields,
        validated_fields=validation["validated_fields"],
        validation_report=validation,
        raw_mapped_fields=mapped_result.get("mapped_fields", {}),
        extra_meta=trace_meta
    )

    duration_ms = int((time.monotonic() - t0) * 1000)
    
    return {
        "document_type": doc_type,
        "fields": flat_fields,
        "item_rows": item_rows,
        "processing_time_ms": duration_ms,
        "training_template_matched": training_template_matched,
        "source_traces": source_traces,
    }


# ── Metrics Evaluator ────────────────────────────────────────────────────────

def evaluate_document(actual: Dict[str, Any], ground_truth: Dict[str, Any]) -> Dict[str, Any]:
    """Compare pipeline results against ground-truth and compute metrics."""
    filename = ground_truth["filename"]
    
    # 1. Classification
    exp_doc_type = ground_truth["document_type"]
    act_doc_type = actual["document_type"]
    class_matched = exp_doc_type.upper() == act_doc_type.upper()
    
    # 2. Company matching
    exp_company = ground_truth.get("company_name", "")
    act_company = actual["fields"].get("company_name", actual["fields"].get("supplier_name", ""))
    company_matched = compare_values(act_company, exp_company) if exp_company else True
    
    # 3. Headers and Dates/Amounts
    header_stats = {"correct": 0, "incorrect": 0, "missing": 0, "total": 0}
    da_stats = {"correct": 0, "incorrect": 0, "missing": 0, "total": 0}
    
    detailed_fields = {}
    
    gt_fields = ground_truth.get("fields", {})
    for fname, exp_val in gt_fields.items():
        act_val = actual["fields"].get(fname)
        is_da = is_date_or_amount_field(fname)
        stats = da_stats if is_da else header_stats
        
        stats["total"] += 1
        
        if act_val is None or str(act_val).strip() == "":
            stats["missing"] += 1
            match_status = "missing"
        elif compare_values(act_val, exp_val):
            stats["correct"] += 1
            match_status = "correct"
        else:
            stats["incorrect"] += 1
            match_status = "incorrect"
            
        detailed_fields[fname] = {
            "expected": exp_val,
            "actual": act_val,
            "status": match_status,
            "category": "date_or_amount" if is_da else "header"
        }
        
    # 4. Item Rows
    exp_row_count = ground_truth.get("item_rows_count", 0)
    act_row_count = len(actual["item_rows"])
    row_count_matched = exp_row_count == act_row_count
    
    exp_first_row = ground_truth.get("first_item_row")
    act_first_row = actual["item_rows"][0] if actual["item_rows"] else None
    
    first_row_matched = True
    first_row_details = {}
    if exp_first_row and act_first_row:
        for k, exp_val in exp_first_row.items():
            act_val = act_first_row.get(k)
            matched = compare_values(act_val, exp_val)
            if not matched:
                first_row_matched = False
            first_row_details[k] = {
                "expected": exp_val,
                "actual": act_val,
                "matched": matched
            }
    elif exp_first_row:
        first_row_matched = False
        
    # 5. Missing Fields (Absent fields)
    gt_missing = ground_truth.get("missing_fields", [])
    actually_missing = []
    false_positives = []
    for fname in gt_missing:
        act_val = actual["fields"].get(fname)
        if act_val is None or str(act_val).strip() == "":
            actually_missing.append(fname)
        else:
            false_positives.append(fname)
            
    # 6. Auto-fill coverage
    expected_filled_fields = [f for f, v in gt_fields.items() if v]
    actual_filled_fields = [f for f in expected_filled_fields if actual["fields"].get(f)]
    coverage = len(actual_filled_fields) / len(expected_filled_fields) if expected_filled_fields else 1.0
    
    # 7. Training dataset usage
    training_used = actual["training_template_matched"]
    
    # Summarize document scores
    header_acc = header_stats["correct"] / header_stats["total"] if header_stats["total"] > 0 else 1.0
    da_acc = da_stats["correct"] / da_stats["total"] if da_stats["total"] > 0 else 1.0
    
    return {
        "filename": filename,
        "classification": {
            "expected": exp_doc_type,
            "actual": act_doc_type,
            "matched": class_matched
        },
        "company_match": {
            "expected": exp_company,
            "actual": act_company,
            "matched": company_matched
        },
        "header_metrics": {
            "correct": header_stats["correct"],
            "incorrect": header_stats["incorrect"],
            "missing": header_stats["missing"],
            "total": header_stats["total"],
            "accuracy": header_acc
        },
        "dates_and_amounts_metrics": {
            "correct": da_stats["correct"],
            "incorrect": da_stats["incorrect"],
            "missing": da_stats["missing"],
            "total": da_stats["total"],
            "accuracy": da_acc
        },
        "item_rows_metrics": {
            "expected_count": exp_row_count,
            "actual_count": act_row_count,
            "count_matched": row_count_matched,
            "first_row_matched": first_row_matched,
            "first_row_details": first_row_details
        },
        "missing_fields_metrics": {
            "expected_missing_count": len(gt_missing),
            "actually_missing_count": len(actually_missing),
            "false_positives_count": len(false_positives),
            "false_positives": false_positives
        },
        "auto_fill_coverage": coverage,
        "processing_time_ms": actual["processing_time_ms"],
        "training_template_matched": training_used,
        "detailed_fields": detailed_fields,
        "source_traces": actual.get("source_traces", {})
    }


# ── Report Generator ──────────────────────────────────────────────────────────

def compute_overall_stats(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_docs = len(results)
    if total_docs == 0:
        return {}
        
    class_acc = sum(1 for r in results if r["classification"]["matched"]) / total_docs
    company_acc = sum(1 for r in results if r["company_match"]["matched"]) / total_docs
    avg_header_acc = sum(r["header_metrics"]["accuracy"] for r in results) / total_docs
    avg_da_acc = sum(r["dates_and_amounts_metrics"]["accuracy"] for r in results) / total_docs
    avg_coverage = sum(r["auto_fill_coverage"] for r in results) / total_docs
    total_fp = sum(r["missing_fields_metrics"]["false_positives_count"] for r in results)
    total_time = sum(r["processing_time_ms"] for r in results)
    
    return {
        "total_documents": total_docs,
        "classification_accuracy": class_acc,
        "company_matching_accuracy": company_acc,
        "average_header_accuracy": avg_header_acc,
        "average_dates_and_amounts_accuracy": avg_da_acc,
        "average_auto_fill_coverage": avg_coverage,
        "total_false_positives": total_fp,
        "total_processing_time_ms": total_time,
        "average_processing_time_ms": total_time / total_docs
    }


def generate_markdown_report(overall: Dict[str, Any], results: List[Dict[str, Any]], baseline: Dict[str, Any] = None) -> str:
    lines = []
    lines.append("# OCR Accuracy Benchmark Report")
    lines.append(f"Date generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
    
    # Overall summary table
    lines.append("## Overall Metrics")
    lines.append("| Metric | Candidate | Baseline | Change |")
    lines.append("|---|---|---|---|")
    
    metrics_to_show = [
        ("Classification Accuracy", "classification_accuracy", "{:.1%}"),
        ("Company Matching Accuracy", "company_matching_accuracy", "{:.1%}"),
        ("Average Header Accuracy", "average_header_accuracy", "{:.1%}"),
        ("Average Dates & Amounts Accuracy", "average_dates_and_amounts_accuracy", "{:.1%}"),
        ("Average Auto-fill Coverage", "average_auto_fill_coverage", "{:.1%}"),
        ("Total False Positives", "total_false_positives", "{}"),
        ("Avg Processing Time (ms)", "average_processing_time_ms", "{:.0f}")
    ]
    
    base_stats = baseline.get("overall_stats", {}) if baseline else {}
    
    for label, key, fmt in metrics_to_show:
        cand_val = overall[key]
        base_val = base_stats.get(key)
        
        cand_str = fmt.format(cand_val)
        if base_val is not None:
            base_str = fmt.format(base_val)
            diff = cand_val - base_val
            if "time" in key or "positives" in key:
                # Lower is better
                if diff < 0:
                    diff_str = f"🟢 {fmt.format(diff)}"
                elif diff > 0:
                    diff_str = f"🔴 +{fmt.format(diff)}"
                else:
                    diff_str = "0"
            else:
                # Higher is better
                if diff > 0:
                    diff_str = f"🟢 +{fmt.format(diff)}"
                elif diff < 0:
                    diff_str = f"🔴 {fmt.format(diff)}"
                else:
                    diff_str = "0"
        else:
            base_str = "N/A"
            diff_str = "N/A"
            
        lines.append(f"| {label} | {cand_str} | {base_str} | {diff_str} |")
        
    lines.append("\n## Detailed Results")
    for r in results:
        lines.append(f"### 📄 {r['filename']}")
        lines.append(f"- **Classification**: {r['classification']['actual']} (Expected: {r['classification']['expected']}) - {'✅ Pass' if r['classification']['matched'] else '❌ Fail'}")
        lines.append(f"- **Company Resolution**: {r['company_match']['actual']} (Expected: {r['company_match']['expected']}) - {'✅ Pass' if r['company_match']['matched'] else '❌ Fail'}")
        lines.append(f"- **Header Fields Accuracy**: {r['header_metrics']['accuracy']:.1%} ({r['header_metrics']['correct']}/{r['header_metrics']['total']})")
        lines.append(f"- **Dates & Amounts Accuracy**: {r['dates_and_amounts_metrics']['accuracy']:.1%} ({r['dates_and_amounts_metrics']['correct']}/{r['dates_and_amounts_metrics']['total']})")
        lines.append(f"- **Item Rows**: {r['item_rows_metrics']['actual_count']} (Expected: {r['item_rows_metrics']['expected_count']}) - {'✅ Count Match' if r['item_rows_metrics']['count_matched'] else '❌ Count Mismatch'}, {'✅ First Row Match' if r['item_rows_metrics']['first_row_matched'] else '❌ First Row Mismatch'}")
        lines.append(f"- **False Positives**: {r['missing_fields_metrics']['false_positives_count']} ({', '.join(r['missing_fields_metrics']['false_positives']) if r['missing_fields_metrics']['false_positives'] else 'none'})")
        lines.append(f"- **Processing Time**: {r['processing_time_ms']} ms")
        lines.append("")
        
    # Regressions / Improvements section
    if baseline:
        lines.append("## Regression & Improvement Audit")
        regressions = []
        improvements = []
        
        # Compare field by field
        base_results = {d["filename"]: d for d in baseline.get("detailed_results", [])}
        for r in results:
            fname = r["filename"]
            base_r = base_results.get(fname)
            if not base_r:
                continue
                
            # Class match diff
            if base_r["classification"]["matched"] and not r["classification"]["matched"]:
                regressions.append(f"{fname}: Classification regressed from correct to incorrect.")
            elif not base_r["classification"]["matched"] and r["classification"]["matched"]:
                improvements.append(f"{fname}: Classification improved from incorrect to correct.")
                
            # Company match diff
            if base_r["company_match"]["matched"] and not r["company_match"]["matched"]:
                regressions.append(f"{fname}: Company resolution regressed.")
            elif not base_r["company_match"]["matched"] and r["company_match"]["matched"]:
                improvements.append(f"{fname}: Company resolution improved.")
                
            # Fields diff
            cand_fields = r["detailed_fields"]
            base_fields = base_r.get("detailed_fields", {})
            for f, details in cand_fields.items():
                base_details = base_fields.get(f)
                if not base_details:
                    continue
                if base_details["status"] == "correct" and details["status"] != "correct":
                    regressions.append(f"{fname}: Field `{f}` regressed (expected '{details['expected']}', baseline was correct, actual '{details['actual']}')")
                elif base_details["status"] != "correct" and details["status"] == "correct":
                    improvements.append(f"{fname}: Field `{f}` improved (expected '{details['expected']}', baseline was incorrect, actual '{details['actual']}')")
                    
        if regressions:
            lines.append("### 🔴 Regressions")
            for reg in regressions:
                lines.append(f"- {reg}")
        else:
            lines.append("### 🔴 Regressions\nNone.")
            
        lines.append("")
        if improvements:
            lines.append("### 🟢 Improvements")
            for imp in improvements:
                lines.append(f"- {imp}")
        else:
            lines.append("### 🟢 Improvements\nNone.")
            
    return "\n".join(lines)


# ── Main Runner Execution ─────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="OCR Accuracy Benchmark Runner")
    parser.add_argument("--manifest", type=str, default="tests/benchmark/manifest.json", help="Path to manifest JSON file")
    parser.add_argument("--compare-with", type=str, default=None, help="Path to baseline JSON report file")
    parser.add_argument("--output-json", type=str, default="benchmark_report_candidate.json", help="Path to write candidate JSON report")
    parser.add_argument("--output-md", type=str, default="benchmark_report_candidate.md", help="Path to write candidate Markdown report")
    args = parser.parse_args()
    
    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"Manifest file not found: {manifest_path}", file=sys.stderr)
        sys.exit(1)
        
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
        
    dataset_dir_str = manifest.get("dataset_dir", "datasets")
    dataset_dir = Path(dataset_dir_str)
    
    documents = manifest.get("documents", [])
    
    results = []
    
    print("==================================================")
    print("      OCR Accuracy Benchmark execution start      ")
    print("==================================================")
    
    for doc in documents:
        file_name = doc["file"]
        file_path = dataset_dir / file_name
        
        if not file_path.exists():
            print(f"Dataset file not found: {file_path}", file=sys.stderr)
            continue
            
        gt_rel_path = doc["ground_truth"]
        gt_path = manifest_path.parent / gt_rel_path
        
        if not gt_path.exists():
            print(f"Ground-truth file not found: {gt_path}", file=sys.stderr)
            continue
            
        with open(gt_path, "r", encoding="utf-8") as gt_f:
            ground_truth = json.load(gt_f)
            
        print(f"Running pipeline on: {file_name}...")
        actual = await run_document_pipeline(file_path, file_name)
        
        print(f"Evaluating: {file_name}...")
        doc_result = evaluate_document(actual, ground_truth)
        results.append(doc_result)
        
        # Short print stats
        print(f"  Class match: {'Yes' if doc_result['classification']['matched'] else 'No'}")
        print(f"  Header accuracy: {doc_result['header_metrics']['accuracy']:.1%}")
        print(f"  Dates & Amounts accuracy: {doc_result['dates_and_amounts_metrics']['accuracy']:.1%}")
        print(f"  Item rows matched: {'Yes' if doc_result['item_rows_metrics']['count_matched'] else 'No'}")
        # Source trace counts print
        traces = actual.get("source_traces", {})
        source_counts = {}
        for t in traces.values():
            st = t.get("source_type", "unknown")
            source_counts[st] = source_counts.get(st, 0) + 1
        source_str = ", ".join(f"{k}={v}" for k, v in sorted(source_counts.items()))
        print(f"  Source traces: {source_str}")
        print(f"  Time taken: {doc_result['processing_time_ms']} ms")
        print("--------------------------------------------------")
        
    overall_stats = compute_overall_stats(results)
    
    # Load baseline if provided
    baseline = None
    if args.compare_with:
        baseline_path = Path(args.compare_with)
        if baseline_path.exists():
            with open(baseline_path, "r", encoding="utf-8") as base_f:
                baseline = json.load(base_f)
            print(f"Loaded baseline report for comparison: {baseline_path}")
        else:
            print(f"Baseline report file not found: {baseline_path}", file=sys.stderr)
            
    # Write JSON report
    report_json = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "overall_stats": overall_stats,
        "detailed_results": results
    }
    
    with open(args.output_json, "w", encoding="utf-8") as j_f:
        json.dump(report_json, j_f, indent=2)
    print(f"Candidate JSON report written to: {args.output_json}")
    
    # Generate and Write Markdown report
    report_md = generate_markdown_report(overall_stats, results, baseline)
    with open(args.output_md, "w", encoding="utf-8") as m_f:
        m_f.write(report_md)
    print(f"Candidate Markdown report written to: {args.output_md}")
    
    print("\n========================= SUMMARY =========================")
    print(f"Total documents: {overall_stats['total_documents']}")
    print(f"Classification accuracy: {overall_stats['classification_accuracy']:.1%}")
    print(f"Company matching accuracy: {overall_stats['company_matching_accuracy']:.1%}")
    print(f"Average header accuracy: {overall_stats['average_header_accuracy']:.1%}")
    print(f"Average dates & amounts accuracy: {overall_stats['average_dates_and_amounts_accuracy']:.1%}")
    print(f"Average auto-fill coverage: {overall_stats['average_auto_fill_coverage']:.1%}")
    print(f"Total false positives: {overall_stats['total_false_positives']}")
    print(f"Total processing time: {overall_stats['total_processing_time_ms']} ms")
    print("===========================================================")


if __name__ == "__main__":
    asyncio.run(main())
