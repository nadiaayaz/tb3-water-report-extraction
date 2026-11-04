I built the extractor at `/app/extractor/extract.py`, but I can't promise every held-out report will reach 0.95. My rough estimate is about a one-in-three chance that all of them do.

**What it does**
- **Ruled tables** (most of the examples): it uses pdfplumber's drawn lines and boxes only to find columns and rows, and places the words itself. That works around header shading boxes, merged name/MCLG cells that span several rows, header rows repeated inside a table, headers printed above the box, and tables drawn with only vertical lines.
- **Unruled tables**: it finds headers by their wording, columns by the white space between them, and rows by horizontal rules or vertical gaps. It handles names and values that wrap over several lines, column labels spanning several sub-columns, and titles or footnotes next to the table.
- **Each column is given a role from its heading**: name, level, range, low/high, unit, MCL, MCLG, date, violation or compliance, count, advisory, source and so on. The rules in FORMAT.md are then applied: column groups and per-well columns, transposed tables, per-plant tables, parent/sub-row names, "Range of Results" sub-rows, labelled values in one cell ("Gulf St 41 (27-54); …"), footnote markers dropped, notes like "Next Testing 2027", PWSID handling, files containing several systems, and a supplier's ID ignored.
- **Tables that aren't results** (definitions, violation summaries, sampling schedules, well lists, water-rate and water-use tables, lead service-line inventories) produce no records.
- **Runtime**: it needs only pdfplumber, plus pdftotext if present. A typical report takes under 1 s and a 93-page file took 7.5 s. If a report runs long, it stops at 100 s and writes what it has so far.

**Results**

Scores are from my own scorer, written from the last section of FORMAT.md. The real grader may compare text more strictly.

| Test set | Result |
|---|---|
| 4 example reports | all 1.000 |
| 31 synthetic reports I generated (ruled, unruled, rules-only, shaded, special cases, tables that must give nothing) | all 1.000 |
| 10 synthetic reports from a separate test agent | 8 at 1.000; one at 0.979 (Texas-style unruled), one at 0.983 (two stacked tables) |
| Examples with the drawn lines ignored (to test the unruled path) | Toledo 1.000, Mankato 1.000, PA 0.994, Medina 0.42 |

**Main risks**
- **Scanned (image-only) reports** will produce no records and fail. No OCR engine is available in the run environment.
- **Dense tables without lines**, where every cell wraps and values are centred vertically, are still weak. That's what the Medina 0.42 means.
- **The answer key is inconsistent on units inside value cells.** Medina keeps "0.3 NTU" as text, while Mankato turns "10 ppm" into 10. I copy both by keeping the text only when the table has its own unit column, which is a guess.
- **Two ruled tables drawn almost touching** get merged into one, which caused the 0.983 test's miss.
- **Layouts I haven't seen** may simply not be handled.
