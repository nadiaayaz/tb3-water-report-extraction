Run with the supplied Python environment:

    python3 extract.py report.pdf output.json

The extractor identifies result tables from PDF geometry, reads column meanings, and emits the fields defined in FORMAT.md. It handles merged cells, repeated source groups, labelled locations, multiple observations in one cell, transposed tables, range sub-rows, regulatory and advisory limits, and count-only microbiological results. Poppler supplies document text and system IDs. All extraction happens locally.

Image-only pages use the installed Tesseract executable when available, with the packaged English model. The fallback recognizes table rules and runs OCR on columns and cells. OCR is less reliable than extraction from PDF text, especially for small print, decimal points and unusual layouts. Native text extraction requires only the supplied pdfplumber, pypdf and Poppler installations; Pillow is included through pdfplumber. Tesseract is already installed in the build environment.

The packaged model is from [tesseract-ocr/tessdata_best](https://github.com/tesseract-ocr/tessdata_best). Its license is in tessdata/LICENSE. No model downloads occur at runtime.

Development validation includes the four provided answer keys, versions with table borders removed, generated source-group and transposed tables, a 50-page generated report, and 1,000 randomized heading/order combinations. The runtime does not read development fixtures or answer keys.

Set CCR_DEBUG=1 to log table decisions and write an additional output.json.tables.json diagnostic file. Normal operation writes only the requested JSON file. Expensive OCR operations are bounded to leave time for JSON output within the report's time limit.

Final local checks recovered 62/62 expected rows from the four original reports, with per-report graded-field agreement of 100%, 99.55%, 100%, and 99.41%. Original-report runtimes were 2.0–4.9 seconds. Removing the borders from those reports gave 98.18–99.17% agreement. The 50-page generated report produced all 100 expected records correctly; the randomized heading/order checks had zero failures in 1,000 cases. These checks establish performance on the available material, not a guarantee for unseen layouts or scans.
