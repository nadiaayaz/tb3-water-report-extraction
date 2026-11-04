Built [extract.py](/Users/dev/tb3-work/runs/ccr-sol-s2-probe/app/extractor/extract.py) with table parsing, multiple-source handling, and an offline OCR fallback.

All four examples recover **62/62 rows**, with **99.4–100% field agreement**, in roughly **2–5 seconds per report**. Additional layout tests pass.

Confidence is moderate for unseen reports. Scanned and unusual layouts remain risks to the 95% target.