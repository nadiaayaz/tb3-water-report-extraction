# Drinking-water report extractor

Run:

```sh
python3 /app/extractor/extract.py report.pdf output.json
```

The extractor uses PDF characters and cell geometry, identifies column meanings,
and reconstructs results from drawn or borderless tables. It handles source
column groups, transposed tables, merged cells, wrapped headings, and tables
continued onto another page. Values are kept as printed rather than inferred
from regulatory standards. It does not read answer keys or use the network.

The supplied Python environment has the dependencies in `requirements.txt`.
Poppler's `pdftoppm` is used for image-only tables. The OCR models are bundled;
no model download occurs at runtime. An installed `tesseract` executable is used
for selected cell crops when available, with the bundled English language data.
RapidOCR remains available when Tesseract is absent.

Set `CCR_DEBUG=1` to print table-recognition diagnostics to stderr. The requested
JSON output is always written to the output path.

Validation during development included all four supplied reports, 108 generated
rows with varied column order/alignment/borders, additional semantic and
cross-page fixtures, rotated pages, and rasterized copies of the supplied tables.
The original four reports recovered every expected row, with over 99% agreement
on the ten required fields in each report. The dense three-page scan completed
in 27.6 seconds with the extractor directory read-only. These checks do not prove
the same accuracy on every unseen layout.
