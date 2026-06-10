"""
ocr_service.py — PaddleOCR 3.x compatible, oneDNN/PIR-safe
Engine order: PaddleOCR (enable_mkldnn=False) → pytesseract → mock
"""
from __future__ import annotations

import asyncio
import io
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from loguru import logger
from app.core.config import get_settings

SUPPORTED_IMAGE_MIMES = {
    "image/png", "image/jpeg", "image/jpg",
    "image/webp", "image/tiff", "image/bmp",
}
SUPPORTED_MIMES = SUPPORTED_IMAGE_MIMES | {"application/pdf"}
REAL_OCR_INSTALL_HINT = "pip install paddlepaddle paddleocr pymupdf pillow"
settings = get_settings()
MAX_OCR_IMAGE_SIDE = 2000
PDF_TEXT_MIN_CHARS = 40
OCR_FAILURE_TEXT = "OCR extraction failed"
COPY_LABEL_RE = re.compile(r"\b(?:duplicate|triplicate|accounts|extra)\b", re.I)
ORIGINAL_LABEL_RE = re.compile(r"\boriginal\b", re.I)


# ── Engine container ──────────────────────────────────────────────────────────

@dataclass
class OCREngine:
    name: str
    client: Any = None
    error: Optional[str] = None


_engine: Optional[OCREngine] = None
_lock = asyncio.Lock()


class _MockOCR:
    def predict(self, img: Any) -> List[Dict]:
        return [{"rec_texts": ["Mock OCR - install paddlepaddle+paddleocr"], "rec_scores": [0.95], "rec_boxes": None}]


# ── Engine loader ─────────────────────────────────────────────────────────────

async def get_engine() -> OCREngine:
    global _engine
    async with _lock:
        if _engine is None:
            _engine = await _load_engine()
        return _engine


async def _load_engine() -> OCREngine:
    loop = asyncio.get_event_loop()

    # ── 1. PaddleOCR 3.x with enable_mkldnn=False (bypasses oneDNN/PIR bug) ──
    try:
        from paddleocr import PaddleOCR  # type: ignore

        def build_paddle() -> Any:
            # enable_mkldnn=False prevents the PIR/oneDNN ConvertPirAttribute crash
            # that occurs when Paddle's new executor tries to use oneDNN on CPU.
            attempts = [
                {"lang": "en", "enable_mkldnn": False},
                {"lang": "en", "enable_mkldnn": False, "use_textline_orientation": False},
                {"lang": "en", "enable_mkldnn": False, "use_doc_unwarping": False, "use_textline_orientation": False},
                {"lang": "en"},
            ]
            last_exc: Exception = RuntimeError("PaddleOCR: all constructor attempts failed")
            for kwargs in attempts:
                try:
                    return PaddleOCR(**kwargs)
                except Exception as exc:
                    last_exc = exc
            raise last_exc

        client = await loop.run_in_executor(None, build_paddle)

        # Verify predict actually works (catches runtime PIR crash at first call)
        def smoke_test(c: Any) -> None:
            test_arr = np.ones((64, 200, 3), dtype=np.uint8) * 255
            try:
                c.predict(test_arr)
            except NotImplementedError as exc:
                raise RuntimeError(f"Paddle inference PIR/oneDNN error: {exc}") from exc

        await loop.run_in_executor(None, smoke_test, client)
        logger.info("OCR engine: PaddleOCR (enable_mkldnn=False) — READY")
        return OCREngine("paddleocr", client)
    except Exception as exc:
        logger.warning(f"PaddleOCR unavailable: {exc}")

    # ── 2. pytesseract ────────────────────────────────────────────────────────
    try:
        import pytesseract  # type: ignore
        import shutil

        # Auto-locate tesseract binary on Windows
        import os
        binary = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
        if binary is None:
            candidates = []
            for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
                if root:
                    candidates.append(str(Path(root) / "Tesseract-OCR" / "tesseract.exe"))
            for c in candidates:
                if Path(c).exists():
                    pytesseract.pytesseract.tesseract_cmd = c
                    binary = c
                    break

        if binary:
            pytesseract.get_tesseract_version()   # throws if binary broken
            logger.info(f"OCR engine: tesseract @ {binary} — READY")
            return OCREngine("tesseract", pytesseract)
        else:
            raise RuntimeError("Tesseract binary not found")
    except Exception as exc:
        logger.warning(f"pytesseract unavailable: {exc}")

    # ── 3. Mock fallback ──────────────────────────────────────────────────────
    if settings.APP_ENV == "production":
        raise RuntimeError("No real OCR engine found. Mock OCR is disabled in production.")
    logger.warning("OCR engine: MOCK (no real engine available) — install paddlepaddle+paddleocr")
    return OCREngine("mock", _MockOCR(), "No real OCR engine found")


# ── Image validation ──────────────────────────────────────────────────────────

def _safe_open_image(data: bytes):
    """Open + validate image bytes. Raises ValueError on bad input."""
    from PIL import Image, UnidentifiedImageError
    if not data:
        raise ValueError("Empty file — nothing to process.")
    try:
        buf = io.BytesIO(data)
        img = Image.open(buf)
        img.verify()
        buf.seek(0)
        return Image.open(buf).convert("RGB")
    except UnidentifiedImageError:
        raise ValueError("Unsupported or corrupted file. Please upload valid PDF/JPG/PNG.")
    except Exception as exc:
        raise ValueError(f"Cannot read image: {exc}. Please upload valid PDF/JPG/PNG.")


# ── Preprocessing ─────────────────────────────────────────────────────────────

def _resize_to_max_side(img: Any, max_side: int = MAX_OCR_IMAGE_SIDE) -> Any:
    w, h = img.size
    if max(w, h) <= max_side:
        return img
    ratio = max_side / float(max(w, h))
    return img.resize((max(1, int(w * ratio)), max(1, int(h * ratio))))


def _preprocess_image(img: Any, mode: str = "normal") -> Any:
    from PIL import ImageEnhance, ImageFilter, ImageOps
    img = ImageOps.exif_transpose(img).convert("RGB")
    img = _resize_to_max_side(img, MAX_OCR_IMAGE_SIDE)
    w, h = img.size
    longest = max(w, h)
    if longest < 700:
        scale = min(3, max(1, int(MAX_OCR_IMAGE_SIDE / max(1, longest))))
    elif longest < 1200:
        scale = 2
    else:
        scale = 1
    if scale > 1:
        img = img.resize((w * scale, h * scale))
    img = _resize_to_max_side(img, MAX_OCR_IMAGE_SIDE)
    img = img.convert("L")
    img = img.filter(ImageFilter.MedianFilter(size=3))
    enhance = 2.5 if mode == "normal" else 3.2
    img = ImageEnhance.Contrast(img).enhance(enhance)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=200 if mode == "normal" else 280, threshold=2))
    threshold = 145 if mode == "normal" else 170
    img = img.point(lambda p: 255 if p > threshold else 0)
    return img.convert("RGB")


def _ocr_quality(text: str) -> Dict[str, Any]:
    clean = (text or "").strip()
    lines = [line for line in clean.splitlines() if line.strip()]
    return {
        "raw_text_length": len(clean),
        "detected_line_count": len(lines),
        "raw_text_preview": clean[:300],
    }


def _save_debug_image(img: Any, label: str) -> str:
    try:
        debug_dir = Path("output") / "ocr_debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        path = debug_dir / f"{int(time.time() * 1000)}_{label}.png"
        img.save(path)
        return str(path)
    except Exception as exc:
        logger.warning(f"Debug image save failed: {exc}")
        return ""


# ── PaddleOCR 3.x result parser ───────────────────────────────────────────────

def _parse_paddle3_result(raw: Any) -> Tuple[str, float]:
    """
    Parse PaddleOCR 3.x predict() output.
    Shape: list of dicts with rec_texts, rec_scores, rec_boxes (N,4) [x1,y1,x2,y2].
    Merges text in reading order (top→bottom, left→right).
    """
    if hasattr(raw, "__iter__") and not isinstance(raw, (list, tuple, dict, str)):
        raw = list(raw)

    result = raw[0] if isinstance(raw, (list, tuple)) and raw else raw

    if isinstance(result, dict):
        rec_texts: List[str] = result.get("rec_texts") or []
        rec_scores: List[float] = result.get("rec_scores") or []
        rec_boxes = result.get("rec_boxes")
        if rec_boxes is None:
            rec_boxes = result.get("rec_polys")
    else:
        rec_texts, rec_scores, rec_boxes = [], [], []
        try:
            lines = result if isinstance(result, (list, tuple)) else raw
            if isinstance(lines, (list, tuple)) and lines and isinstance(lines[0], (list, tuple)):
                if len(lines) == 1 and isinstance(lines[0], (list, tuple)) and lines[0] and isinstance(lines[0][0], (list, tuple)):
                    lines = lines[0]
                for item in lines:
                    if isinstance(item, dict):
                        if "rec_texts" in item:
                            rec_texts.extend(item.get("rec_texts") or [])
                            rec_scores.extend(item.get("rec_scores") or [])
                            if item.get("rec_boxes") is not None:
                                if isinstance(rec_boxes, list):
                                    rec_boxes.extend(item.get("rec_boxes"))
                        continue
                    if not isinstance(item, (list, tuple)) or len(item) < 2:
                        continue
                    box, text_conf = item[0], item[1]
                    if isinstance(text_conf, (list, tuple)) and text_conf:
                        rec_texts.append(str(text_conf[0]))
                        rec_scores.append(float(text_conf[1]) if len(text_conf) > 1 else 0.7)
                        rec_boxes.append(box)
        except Exception as exc:
            logger.warning(f"PaddleOCR 2.x result parse failed: {exc}")

    if not rec_texts:
        return "", 0.0

    # Order lines by Y position (reading order)
    try:
        if rec_boxes is not None:
            boxes = np.array(rec_boxes) if not isinstance(rec_boxes, np.ndarray) else rec_boxes
            if boxes.ndim == 2 and boxes.shape[1] >= 2:
                # Shape (N, 4): [x1, y1, x2, y2]
                y_vals = boxes[:, 1].tolist()
            elif boxes.ndim == 3:
                # Shape (N, 4, 2): polygon points
                y_vals = [float(boxes[i, 0, 1]) for i in range(len(rec_texts))]
            else:
                y_vals = list(range(len(rec_texts)))
        else:
            y_vals = list(range(len(rec_texts)))

        # Group into visual lines (texts within 15px of each other merge on same line)
        indexed = sorted(zip(y_vals, range(len(rec_texts))), key=lambda t: t[0])
        lines: List[List[int]] = []
        current_line: List[int] = []
        last_y: Optional[float] = None
        LINE_GAP = 15

        for y, idx in indexed:
            if last_y is None or abs(y - last_y) <= LINE_GAP:
                current_line.append(idx)
            else:
                lines.append(current_line)
                current_line = [idx]
            last_y = y
        if current_line:
            lines.append(current_line)

        # Within each line, sort by X position if boxes available
        ordered_texts: List[str] = []
        for line_idxs in lines:
            if rec_boxes is not None and boxes.ndim == 2 and boxes.shape[1] >= 1:
                x_sorted = sorted(line_idxs, key=lambda i: float(boxes[i, 0]))
            else:
                x_sorted = line_idxs
            line_text = " ".join(rec_texts[i] for i in x_sorted if rec_texts[i].strip())
            if line_text.strip():
                ordered_texts.append(line_text)

        text = "\n".join(ordered_texts)
    except Exception as parse_exc:
        logger.warning(f"PaddleOCR result ordering failed: {parse_exc} — using raw order")
        text = "\n".join(t for t in rec_texts if t.strip())

    confs = [float(s) for s in rec_scores if 0 <= float(s) <= 1]
    conf = round(sum(confs) / len(confs), 4) if confs else (0.7 if text.strip() else 0.0)
    return text.strip(), conf


# ── Tesseract result parser ───────────────────────────────────────────────────

def _parse_tesseract_result(client: Any, img: Any) -> Tuple[str, float]:
    text = client.image_to_string(img, config="--oem 3 --psm 6").strip()
    return text, 0.75 if text else 0.0


# ── Single-image OCR ──────────────────────────────────────────────────────────

async def _run_ocr(arr: np.ndarray, engine: OCREngine, img_pil: Any = None) -> Tuple[str, float]:
    """Run OCR on a numpy array. img_pil needed for tesseract."""
    loop = asyncio.get_event_loop()

    if engine.name == "paddleocr":
        def _call_paddle():
            raw = engine.client.predict(arr)
            return _parse_paddle3_result(raw)
        try:
            return await loop.run_in_executor(None, _call_paddle)
        except Exception as exc:
            logger.warning(f"PaddleOCR predict failed: {exc}, trying Tesseract fallback")
            try:
                import pytesseract
                import shutil
                import os
                from pathlib import Path
                binary = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
                if not binary:
                    for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
                        if root and Path(root).joinpath("Tesseract-OCR", "tesseract.exe").exists():
                            binary = str(Path(root) / "Tesseract-OCR" / "tesseract.exe")
                            pytesseract.pytesseract.tesseract_cmd = binary
                            break
                if binary:
                    from PIL import Image
                    pil = img_pil if img_pil is not None else Image.fromarray(arr)
                    def _call_tess():
                        return _parse_tesseract_result(pytesseract, pil)
                    return await loop.run_in_executor(None, _call_tess)
            except Exception as tess_exc:
                logger.error(f"Tesseract fallback also failed: {tess_exc}")
            return "", 0.0

    if engine.name == "tesseract":
        from PIL import Image
        pil = img_pil if img_pil is not None else Image.fromarray(arr)
        def _call_tess_main():
            return _parse_tesseract_result(engine.client, pil)
        try:
            return await loop.run_in_executor(None, _call_tess_main)
        except Exception as exc:
            logger.error(f"Tesseract runtime failed: {exc}")
            return "", 0.0

    # No real OCR engine is available.
    return "", 0.0


async def ocr_image_bytes(data: bytes, engine: Optional[OCREngine] = None) -> Tuple[str, float]:
    base_img = _safe_open_image(data)
    selected = engine or await get_engine()

    # Normal preprocess
    img_normal = _preprocess_image(base_img, "normal")
    _save_debug_image(img_normal, "img_normal")
    arr = np.array(img_normal)
    text, conf = await _run_ocr(arr, selected, img_normal)

    # Retry with stronger preprocess if text is weak
    if len((text or "").strip()) < 20:
        img_strong = _preprocess_image(base_img, "strong")
        _save_debug_image(img_strong, "img_strong")
        arr2 = np.array(img_strong)
        text2, conf2 = await _run_ocr(arr2, selected, img_strong)
        if len((text2 or "").strip()) > len((text or "").strip()):
            text, conf = text2, conf2

    return (text or "").strip(), conf


# ── PDF OCR ───────────────────────────────────────────────────────────────────

def _original_only_chunks(chunks: List[str]) -> List[str]:
    original = [t for t in chunks if ORIGINAL_LABEL_RE.search(t or "") and not COPY_LABEL_RE.search(t or "")]
    if original:
        return original
    return [t for t in chunks if not COPY_LABEL_RE.search(t or "")]


def _extract_pdf_text(doc: Any, max_pages: int) -> str:
    chunks: List[str] = []
    for page in list(doc)[:max_pages]:
        try:
            page_text = (page.get_text("text") or "").strip()
            if page_text:
                chunks.append(page_text)
        except Exception as exc:
            logger.warning(f"PDF page {page.number} text extraction error (skipping): {exc}")
            
    return "\n\n".join(chunks).strip()


async def ocr_pdf_bytes(data: bytes, engine: Optional[OCREngine] = None) -> Tuple[str, float, int, Dict[str, int]]:
    if not data:
        raise ValueError("Empty file — nothing to process.")

    selected = engine or await get_engine()
    timings: Dict[str, int] = {"pdf_text_extraction_ms": 0, "ocr_ms": 0}

    # ── PyMuPDF renderer ──────────────────────────────────────────────────────
    try:
        import fitz  # type: ignore
        from PIL import Image
    except ImportError as exc:
        raise ValueError(f"PDF OCR requires PyMuPDF (pymupdf). Install: pip install pymupdf. Details: {exc}")

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {exc}. File may be corrupted.")

    if doc.page_count == 0:
        raise ValueError("PDF has no pages.")

    max_pages = max(1, settings.MAX_PDF_PAGES)
    t_text = time.monotonic()
    extracted_text = _extract_pdf_text(doc, max_pages)
    timings["pdf_text_extraction_ms"] = int((time.monotonic() - t_text) * 1000)
    logger.info(
        "PDF text extraction: pages_checked={} total_pages={} ms={} text_len={}",
        min(doc.page_count, max_pages),
        doc.page_count,
        timings["pdf_text_extraction_ms"],
        len(extracted_text),
    )
    if len(extracted_text.strip()) >= PDF_TEXT_MIN_CHARS:
        return extracted_text, 1.0, doc.page_count, timings

    all_text: List[str] = []
    all_confs: List[float] = []
    t_ocr = time.monotonic()
    for page in list(doc)[:max_pages]:
        try:
            rect = page.rect
            zoom = min(2.0, MAX_OCR_IMAGE_SIDE / max(float(rect.width), float(rect.height)))
            zoom = max(1.25, zoom)
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            base_img = _resize_to_max_side(Image.frombytes("RGB", (pix.width, pix.height), pix.samples), MAX_OCR_IMAGE_SIDE)
            img = _preprocess_image(base_img, "normal")
            _save_debug_image(img, f"pdf_p{page.number+1}_normal")
            arr = np.array(img)
            text, conf = await _run_ocr(arr, selected, img)

            if len((text or "").strip()) < 20:
                img2 = _preprocess_image(base_img, "strong")
                _save_debug_image(img2, f"pdf_p{page.number+1}_strong")
                t2, c2 = await _run_ocr(np.array(img2), selected, img2)
                if len((t2 or "").strip()) > len((text or "").strip()):
                    text, conf = t2, c2

            all_text.append((text or "").strip())
            all_confs.append(conf)
        except Exception as page_exc:
            logger.warning(f"PDF page {page.number} OCR error (skipping): {page_exc}")
            all_text.append("")
            all_confs.append(0.0)

    allowed_texts = _original_only_chunks(all_text)
    valid_texts = []
    valid_confs = []
    for t, c in zip(all_text, all_confs):
        if t in allowed_texts:
            valid_texts.append(t)
            valid_confs.append(c)
                
    if not valid_texts and all_text:
        valid_texts = [all_text[0]]
        valid_confs = [all_confs[0]]

    avg = round(sum(valid_confs) / len(valid_confs), 4) if valid_confs else 0.0
    full_text = "\n\n".join(t for t in valid_texts if t)
    timings["ocr_ms"] = int((time.monotonic() - t_ocr) * 1000)
    return full_text, avg, doc.page_count, timings


# ── Main entry ────────────────────────────────────────────────────────────────

async def process_file(data: bytes, mime_type: str) -> Dict:
    """
    Entry point called by the upload router.
    Returns dict with full_text, raw_text, avg_confidence, page_count, processing_ms, ocr_engine.
    Raises ValueError with clean user message on bad input.
    """
    if not data:
        raise ValueError("Empty file — nothing to process.")
    if mime_type not in SUPPORTED_MIMES:
        raise ValueError(
            f"Unsupported file type '{mime_type}'. "
            "Please upload valid PDF/JPG/PNG/WEBP/TIFF."
        )

    t0 = time.monotonic()
    engine = await get_engine()
    logger.info(f"OCR start: engine={engine.name} mime={mime_type} size={len(data)}")

    warnings: List[str] = []
    timings: Dict[str, int] = {"pdf_text_extraction_ms": 0, "ocr_ms": 0}
    try:
        if mime_type == "application/pdf":
            text, conf, pages, timings = await ocr_pdf_bytes(data, engine)
        else:
            t_ocr = time.monotonic()
            text, conf = await ocr_image_bytes(data, engine)
            timings["ocr_ms"] = int((time.monotonic() - t_ocr) * 1000)
            pages = 1
    except Exception as exc:
        logger.exception(f"OCR processing failed gracefully: mime={mime_type} error={exc}")
        text, conf, pages = "", 0.0, 0
        warnings.append(str(exc))

    text = (text or "").strip()
    extraction_failed = not text
    if extraction_failed:
        if engine.name == "mock":
            warnings.append("No real OCR engine available.")
        text = OCR_FAILURE_TEXT
        conf = 0.0
    elif engine.name == "mock":
        warnings.append("No real OCR engine available; using embedded PDF text.")

    ms = int((time.monotonic() - t0) * 1000)
    quality = _ocr_quality(text)

    is_mock = engine.name == "mock"
    logger.info(
        "OCR done: engine={} mime={} pages={} conf={:.0%} ms={} text_len={} lines={}",
        engine.name, mime_type, pages, conf, ms, quality["raw_text_length"],
        quality["detected_line_count"],
    )

    result: Dict = {
        "full_text":        text,
        "raw_text":         text,
        "text":             text,
        "extracted_text":   text,
        "raw_text_length":  quality["raw_text_length"],
        "detected_line_count": quality["detected_line_count"],
        "raw_text_preview": quality["raw_text_preview"],
        "avg_confidence":   conf,
        "page_count":       pages,
        "processing_ms":    ms,
        "pdf_text_extraction_ms": timings.get("pdf_text_extraction_ms", 0),
        "ocr_ms":           timings.get("ocr_ms", ms),
        "engine":           engine.name,
        "ocr_engine":       engine.name,
        "is_mock_ocr":      is_mock,
        "weak_ocr":         extraction_failed or len(text.strip()) < 20,
        "success":          True,
        "fields":           {},
        "warnings":         warnings,
    }
    if extraction_failed:
        result["weak_ocr_message"] = OCR_FAILURE_TEXT
    elif len(text.strip()) < 20:
        result["weak_ocr_message"] = "OCR returned very little text. Try a clearer image or paste text manually."
    if is_mock:
        result["warning"] = "Real OCR engine not installed."
        result["install_hint"] = REAL_OCR_INSTALL_HINT
    return result
