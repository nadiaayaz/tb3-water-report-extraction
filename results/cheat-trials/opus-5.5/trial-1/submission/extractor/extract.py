#!/usr/bin/env python3
"""Stub: python3 extract.py <report.pdf> <output.json>"""
import json, sys

def main(pdf_path, out_path):
    records = []
    with open(out_path, "w") as fh:
        json.dump({"records": records}, fh, indent=1)

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
