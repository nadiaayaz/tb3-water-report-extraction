# Claude Code / GPT-6.1 Sol runs (three, all failed)

The author reports that these trials ran through Harbor on a temporary VM, using Claude Code as the agent harness, `gpt-6.1-sol` at xhigh effort and a hard 2-hour limit. The task workspace was `/app`, with pdfplumber, pypdf and poppler-utils available. The retained summaries record no web search or access to answer material. Network access was on: run 1 pip-installed reportlab to draw test pages, run 3 an OCR package (rapidocr) and a code formatter, and run 2 downloaded an English OCR model. The trials ran consecutively on 2026-10-28–29 with `/app/FORMAT.md` v3.1. The VM was lost; original Harbor job configurations, trajectories and trial IDs are unavailable. The files here are retained result summaries and artifacts, not native Harbor exports. The original GPT-6.1 Sol configuration for Claude Code is unavailable.

All three ended by themselves (exit 0) with a finished extractor; none reached the 2-hour limit.

| Trial | Start (UTC) | Duration | Held-out reports passed | Its closing claim |
|---|---|---|---|---|
| 1 | 2026-10-28 20:52 | 106 min | 3 of 20 | "99.4–100% on all four answer keys, 100% across 36 generated layouts" |
| 2 | 2026-10-28 22:42 | 116 min | 5 of 20 | "All four examples recover 62/62 rows, with 99.4–100% field agreement" |
| 3 | 2026-10-29 00:43 | 113 min | 4 of 20 | "recovered all 62 example rows with 99.4–100% field agreement per report. All 108 generated layout-test rows matched exactly" |

Each closing message rated its confidence that every unseen report passes as moderate.

Scores are from this repository's verifier image.

Fourteen reports failed in all three runs and three in two; de-dover, fl-capecoral and hi-kauai-lihue passed every time. Of the 48 failures, 31 scored below 0.80.

Per trial: `submission/` (the agent's own files, unmodified; run 1 also kept its development tests under `tests/`), `verifier/test-stdout.txt` (the submission graded in the repository's verifier image), `agent_final_report.md` (the agent's closing message, verbatim) and `run.json` (model, times, token and command counts, failed reports with scores). Runs 2 and 3 also put third-party OCR model files (15 and 19 MB, with their licence files) under `extractor/tessdata/` and `extractor/models/`; they are listed in `run.json` and not copied here. Each submission here gives the same score on every held-out report as the full final folder. Agent transcripts are not included.
