The extractor is at `/app/extractor/extract.py` and all four example reports clear the 0.95 bar on my local scorer. I'm not confident every held-out report will: I'd put it at roughly 25–35%. My scorer is an approximation of the grader, since the exact grader wasn't available.

**Scores on the example reports:**

| Report | Score | What it got wrong |
|---|---|---|
| mn-mankato | 1.000 | — |
| oh-toledo | 1.000 | — |
| ny-medina | 0.995 | One fact: the key writes the limit as the text "0.3 NTU", while the format's rule says to output 0.3 with unit NTU. I followed the rule. |
| pa-reserve | 0.994 | One fact: the key has "≤" where the PDF's text layer has "<". |

**How it works:** it uses only pdfplumber, plus `pdftotext` for finding the system ID (the PWSID).
- **Finding tables:** it looks for heading rows by keywords (contaminant, MCL, MCLG, level, range, violation, unit, date, source). Multi-line, vertically centred headings are merged into columns, and table titles and in-table group labels are kept out of the headings.
- **Columns:** each heading is assigned a field following FORMAT.md's precedence. A column with no heading that holds Yes/No values is treated as the violation column; Toledo needs this.
- **Rows:** wrapped cells are grouped into one row using vertical gaps and ruling lines, plus a check for a new value starting on the next line. This handles tables with centred cells (Medina), top-aligned cells, and sub-rows that share a merged cell (PA's turbidity rows).
- **Cell values:** it handles ranges in their various forms (dash, "to", ND, "<"), a level with its range in brackets, labelled values like "Average: x Range: a-b", place-labelled values ("Gulf Street 41 (27-54)…") as one record per place, units printed inside cells, footnote markers, and dates or units inside the contaminant name.
- **Extras:**
  - Tables that continue onto the next page without repeating their header.
  - Separate column groups per plant or well become separate records with a location.
  - If a table's only measured column is a count, that count is the level.
  - In multi-system files, the system ID switches when a different ID appears at the top of a page.
  - There is a 100-second time guard. It runs in about 2 seconds per report and never crashes; on errors it writes whatever it has.

**Why I'm not more confident:** four examples is a small sample against the strict 0.95 bar on layouts I haven't seen. The main risks:
- Layouts unlike the examples: two-column newsletter pages, rotated headings, unusual heading wording.
- Two cases in FORMAT.md are not handled at all:
  - Tables where contaminants are the columns and dates or places are the rows.
  - Turbidity-style sub-row labels such as "Turbidity - Highest single measurement".
- Scanned, image-only PDFs produce no records. There is no OCR, because the target environment doesn't list Tesseract.
- The rule that switches system ID mid-file could wrongly fire on a single-system report that prints another supplier's ID at the top of a page.
