"""Print raw text samples for audit."""
import fitz, sys
sys.path.insert(0, r"D:\OCR_MASTER\ocr-platform-complete")

docs = {
    "salesinvoice.pdf": r"D:\OCR_MASTER\datasets\salesinvoice.pdf",
    "Glsprint.pdf": r"D:\OCR_MASTER\datasets\Glsprint.pdf",
    "POPRINT.pdf": r"D:\OCR_MASTER\datasets\POPRINT.pdf",
    "GRN.pdf": r"D:\OCR_MASTER\datasets\GRN.pdf",
}
for name, path in docs.items():
    doc = fitz.open(path)
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    print(f"\n{'='*60}\n{name} (len={len(text)})")
    print(text[:3000])
