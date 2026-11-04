# Output format: detected-contaminant results from a drinking-water quality report

For each input PDF the extractor writes one JSON file:

```json
{
  "records": [
    {
      "system": "MN1270024",
      "contaminant": "Nitrate",
      "sample_date": "2024",
      "level": 1.63,
      "range_low": "ND",
      "range_high": 1.63,
      "unit": "ppm",
      "mcl": 10,
      "mclg": 10,
      "violation": "N"
    }
  ]
}
```

One record per contaminant row of every **results table** in the report. Every value is read from
the page as printed; nothing is computed, converted or corrected (a printed error is recorded as printed).

## Which tables and rows

- A **results table** is a table (a grid of rows and columns, however it is drawn) in which each row is
  one contaminant or water-quality parameter and at least one column holds levels measured in this
  system's water (or its source water). This covers regulated, unregulated (UCMR), secondary,
  disinfectant / disinfection by-product, lead and copper, microbiological, turbidity, radiological,
  sodium/hardness and "other parameters" tables, whether or not the level is a detection. A
  microbiological table whose only measured column is a count (e.g. "Highest No. of Detections",
  "Number of positive samples") is a results table, and that count column is its level column.
- Every row of a results table is a record, including rows whose level is printed as ND, 0 or "Absent".
  Exception: a sub-row labelled as the range of the row above ("Range of Results", "Range of quarterly
  samples") is not a record; its values fill that parent record's `range_low` / `range_high`.
  If the same contaminant appears in several tables (or several times), each appearance is a record.
- Not results tables: lists or tables of contaminants stated (by title or text) to be *not detected*
  that have no level column, definitions/abbreviations, violation summaries (rows are violations or
  events), sampling schedules, health-effects or source-description tables without levels, lead
  service-line inventories, assessment counts. Values that appear only in running text are not records.
- When a table gives levels in separate column groups for different sources, plants, wells, entry points
  or areas, write one record per row per column group, with `location` set to that group's heading.
  Likewise when one table is split under sub-headings naming sources, `location` is the sub-heading, and
  when one level cell holds several values each labelled with a place ("Gulf St 41 (27-54); Maple Rd 18
  (14-29)"), write one record per labelled value with `location` set to the label. Likewise a value cell
  holding several values, each paired line by line with its own entries in other columns of the row
  ("3.49 ppm (RAA)" over "3.61 ppm (MPA)" beside "LWTP" over "LWTP"), is several records, one per line.
- Tables laid out the other way round: when a table's columns are contaminants and its rows are sampling
  dates or places, write one record per contaminant column per row; the row's date goes to `sample_date`
  (or its place to `location`). When a table's rows are plants, wells or sites for a single parameter
  named in the table title (e.g. "TOC Removal Ratio" with one row per plant), `contaminant` is that
  parameter and `location` is the row label. If such a table gives the parameter in two unit columns
  (e.g. mg/L and grains per gallon for each area), write one record per row: the first unit column is
  the level (its unit from its heading) and the other goes to `other_values`.

## Fields

| field | rule |
|---|---|
| `system` | The PWSID (public water system ID) of the water system whose report this is, as printed without the label (e.g. "PWSID #: 7010032" -> "7010032"); the same for every record, including tables of water purchased from another supplier. `null` if the report prints none in its text (an ID visible only inside a photograph or picture does not count). |
| `contaminant` | Text of the row's contaminant cell, without footnote markers (*, †, superscripts, bracketed note numbers) and without a unit or year given in parentheses (those go to `unit` / `sample_date`), and without a leading item number ("13. Barium" -> "Barium"); other parenthesised text stays ("Copper (90th percentile)"). Notes trailing the name in the same cell
about testing ("Next Testing 2027", "(Tested 30 at risk homes in 2024)") are not part of the name (a date in
them still goes to `sample_date`). If the cell is a continuation of a merged cell above, use the merged cell's text. If the row is a sub-row with its own label (e.g. "Turbidity" / "Highest single measurement"), join them as "Turbidity - Highest single measurement" (a label printed inside the value cell itself is not a sub-row label; it is dropped as in `level`). |
| `sample_date` | Text of the row's date/year column (headings like Date, Sample Date, Year Sampled, Test Dates, Collection Date) as printed, or a date/year printed in the contaminant cell (the date or year only: "(tested in 2023)" -> "2023"). Otherwise `null` (do not use the report year or a table title). A date given for a next or future test ("Next Testing 2027") is not a sample date. |
| `level` | The value in the row's **level column** (see below). A number when the cell is a number; otherwise the text as printed, e.g. "ND", "<0.5", "Absent", "0 positive". Drop footnote markers and any label in the cell ("90th percentile value = 0.21" -> 0.21). When the cell holds several labelled values ("Max 1.28 / Average 0.69 / Range 0.47-1.28"), the level is the first value printed that is not the labelled range. |
| `range_low`, `range_high` | From a range column, or from a range printed with a "Range" label inside the row's value cell ("Average: 0.89 Range: 0.31-1.36"). A single cell "0.5 - 1.2" gives 0.5 and 1.2; "ND - 1.2" gives "ND" and 1.2; separate Low/High (Min/Max) columns give their values; a range cell holding a single value gives that value for both. A labelled value in a range-column cell ("8.0 - 22 / Highest LRAA: 16.8", "Highest result: 0.038") is not part of the range: the cell gives only the range it prints (`null` if none) and the labelled value goes to `other_values` (in a combined "Range or Level" column it is the level). A non-numeric statement in a range
column ("one measure", "100% of monthly samples ≤ 0.3 NTU") is not a range: the range is `null` and the text
may go to `other_values`. A combined column headed like "Range or Level Detected" is a range when its cell
holds a range and the level when its cell holds a single value (then the range is `null`). `null` when no
range is printed. |
| `unit` | From a unit column; else a unit printed in the contaminant cell; else one printed with the level value; else one printed with the MCL value; else the unit in the level column's heading; else one printed with the range value; else a unit stated for the whole table in its title or caption;
else `null`. As printed, including any qualifier ("ppm as CaCO3", "mg/L as N"). A word naming the
quantity rather than a unit of measure ("Percent Removal", "Removal Ratio") is not a unit. |
| `mcl` | The row's cell in the column of the regulatory limit it is held to: MCL, SMCL (secondary tables), Action Level, MRDL, TT, Highest Level Allowed, minimum residual / removal required. As printed: a number, or text such as "TT", "AL=15", "MRDL=4", "35%-45%". Advisory comparison columns (Comparison Value, Health Advisory, Health-Based Value, HRL, Notification Level) are not the MCL; they go in `other_values`. |
| `mclg` | The row's MCLG cell (or PHG / MRDLG / ALG cell when that column takes its place) as printed. The column heading decides: a column headed MCLG fills `mclg` even when it holds secondary limits. |
| `violation` | "Y" or "N" from a violation column (Violation, Violation Y/N, Exceeds MCL?, MCL Exceeded). Columns about the action level ("90th Percentile AL (Yes/No)", "Exceeds AL") are violation columns. For a column that states compliance (In Compliance?, Meets Standard?, Met Standards, Pass), "Yes" gives "N" and "No" gives "Y". A check mark counts as Yes and a cross (✗) as No. `null` when the table has no such column. |
| `location` | Optional; see column groups above. Not graded. |
| `other_values` | Optional object of further value columns, `{heading: value}`. Not graded. |

Blank cells, cells printed as "N/A", "NA", "None", "No Range", "-" or "--" (any case), and cells that only refer to a
note ("see note 3") are `null`. Footnote markers are dropped from every field. A unit printed inside a value
cell ("2.8 ppb", "10 ppm") goes to `unit` and the number to the field.

**Level column.** The value columns of a table are its columns of measured values, excluding columns
for range (Range, Low, High, Min, Max, Lowest, Highest *of a range pair*), MCL / AL / MRDL / TT, MCLG /
PHG / MRDLG, unit, date/year, violation/compliance, likely source, and counts (number of samples, number
of sites above the action level, number of positive samples). The **level column** is the leftmost value
column. (So in "Average | Range | Highest" the level is the Average; in "90th Percentile | # Sites
Over AL" it is the 90th percentile.) Other value columns may go in `other_values`.

**Multi-system files.** If one file contains the reports of several water systems, write the records of
every table; `system` is the PWSID printed for the system whose part of the file the table is in.

## How it is graded

Rows are matched to the answer key by contaminant name (ignoring case, spacing and punctuation); numbers
are compared numerically; a document's score is the share of key facts recovered, where records that are
not in the key count against the score. Every report must reach 0.95.
