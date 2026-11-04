I've written `/app/extractor/extract.py`. All four sample reports score 1.000 on a local scorer that copies the grading description, but I'd put the chance that every held-out report clears 0.95 at roughly even. Reports laid out like the samples should pass; an unfamiliar layout or a different annotator habit could sink one report, and one is enough to fail the task. The scorer is my approximation, not the real grader.

**What it does**
- **Text cleanup:** it drops footnote markers (small or raised characters such as "Copper¹" or "TTHM¹"). It also turns underlined "<" into "≤", which the Reserve key expects, and turns sideways pages upright first.
- **System ID:** it reads the PWSID from the full text and ignores IDs mentioned in a "we purchase water from…" context. If a file holds several systems, each table gets the ID of the section it sits in.
- **Tables with ruling lines:** these use pdfplumber's cell grid, with my own matching of text to cells and header text to columns. It handles multi-line headers, cells merged across rows, sub-rows ("Turbidity – Highest single measurement"), and tables that continue onto the next page without repeating the header. It also handles zebra-striped tables, which pdfplumber breaks into one fragment per shaded row.
- **Tables without lines:** a fallback finds the header, works out columns from the whitespace gaps, and groups lines into rows, including multi-line and vertically centred cells.
- **Field rules from `FORMAT.md`:** column types come from the headers (level, range, MCL, MCLG, unit, date, violation, compliance, counts, advisory values). It handles values with units, labelled values in a cell ("Average: 0.89 Range: …"), several place-labelled values in one cell, the unit fallback order, and flipping "In Compliance? Yes" to "N". Tables laid out the other way round (contaminants as columns) are covered too.
- **Speed and failure:** about 1–2 s per report and 15 s for a 93-page file. A time guard stops it at 95 s, and on any failure it still writes a valid JSON file.

**How I tested it**
- **Samples and synthetic reports:** besides the four samples, I built about ten synthetic reports by rendering HTML with Chrome and hand-wrote their keys. They cover Texas/Illinois- and California-style headers, columns grouped by plant, sub-rows, contaminants-as-columns, striped and shaded tables, a table spanning two pages, each in ruled and unruled form, plus rotated copies of two samples. All score ≥ 0.998.
- **Samples with ruling lines ignored:** Mankato and Toledo still score 1.00 and Reserve 0.99. Medina drops to 0.10: without lines its vertically centred cells next to a long health-effects column are ambiguous. That only matters if a held-out report has that layout and no lines.

**Known risks**
- Scanned, image-only PDFs give no records; no OCR tool is guaranteed on the grading machine.
- Layouts I haven't anticipated, and annotator conventions that differ from the four keys, are the main threat.
- One deliberate departure from `FORMAT.md`: when a table has a unit column, a limit printed with its unit (Medina's "0.3 NTU") stays as printed, not split into 0.3 plus a unit. That matches the Medina key but not the literal rule; Mankato, with no unit column, still splits "10 ppm" into 10.

My test files (scorer, synthetic PDFs, Chrome profile) are in `/Users/dev/ccr-r1-probe/work`. Early on I accidentally downloaded a pip package into `/tmp/x`, outside the project folder; I deleted it straight away.
