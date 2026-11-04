# Claude Opus 5.5 runs (three, all failed)

The author reports that these trials ran through Harbor on a temporary VM, using Claude Code as the agent harness, `claude-opus-5-5` at max effort, `CLAUDE_CODE_MAX_OUTPUT_TOKENS=128000` and a 2-hour limit. The task workspace was `/app`, with pdfplumber, pypdf and poppler-utils available. The retained summaries record no web requests or access to answer material. The VM was lost; original Harbor job configurations, trajectories and trial IDs are unavailable. The files here are retained result summaries and artifacts, not native Harbor exports. [Reproduction runner](../../../run_opus_trials.sh).

Each run ended by itself with a finished extractor and a closing summary; none reached the 2-hour limit, none hit a usage limit or an API error, and none was resumed or interrupted. Runs 2 and 3 ran at the same time.

| Trial | Start (UTC) | Duration | Held-out reports passed | Failed reports (score; pass mark 0.95) |
|---|---|---|---|---|
| 1 | 2026-10-21 08:01 | 54 min | 6 of 20 | ga-athensclarke (0.214), sc-florence (0.296), vt-richmond (0.355), tn-kub (0.513), wa-whitworth (0.536), ca-mammoth (0.591), ks-kucampus (0.632), mi-annarbor (0.715), mn-minneapolis (0.722), in-bloomington (0.730), ar-beaverwd (0.736), or-eweb (0.742), co-boulder (0.772), az-scottsdale (0.930) |
| 2 | 2026-10-21 09:27 | 80 min | 4 of 20 | sc-florence (0.010), mn-minneapolis (0.078), ga-athensclarke (0.222), ca-mammoth (0.394), mi-annarbor (0.579), wa-whitworth (0.696), tn-kub (0.704), co-boulder (0.709), mt-whitefish (0.713), in-bloomington (0.775), vt-richmond (0.787), ak-unalaska (0.848), or-eweb (0.875), va-kinggeorge-canterbury (0.875), ar-beaverwd (0.879), ks-kucampus (0.939) |
| 3 | 2026-10-21 09:27 | 21 min | 1 of 20 | fl-capecoral (0.123), ak-unalaska (0.153), va-kinggeorge-canterbury (0.175), sc-florence (0.198), ar-beaverwd (0.256), vt-richmond (0.362), wa-whitworth (0.368), az-scottsdale (0.483), ga-athensclarke (0.495), tn-kub (0.524), ks-kucampus (0.545), hi-kauai-lihue (0.695), co-boulder (0.744), ca-mammoth (0.777), mt-whitefish (0.786), mi-annarbor (0.789), in-bloomington (0.802), mn-minneapolis (0.833), or-eweb (0.938) |

Scores are from this repository's verifier image, which runs the extractor on one report at a time as `nobody`; they match the retained result summaries report by report. Each submission scores 1.0 on all four examples. In their closing messages the runs put the chance that every held-out report passes at "roughly even" (run 1), "about a one-in-three chance" (run 2) and "25–35%" (run 3).

The runs saw an earlier revision of `/app/FORMAT.md` (run 1 v1, runs 2 and 3 v2). The packaged v3.1 states some rules more explicitly; the examples and their keys were the same. The submissions are graded here on the v3.1 keys (`run.json`, `format_md_seen`).

Per trial:
- `submission/extract.py`: the file the agent delivered, unmodified (each run delivered this one file).
- `verifier/test-stdout.txt`: that file graded in the repository's verifier image.
- `agent_final_report.md`: the agent's closing message, verbatim.
- `run.json`: retained model, timestamps, duration, tool-call counts and failed reports with scores; Harbor provenance is attributed to the author's account.

Agent transcripts are not included.

## Which reports failed how often

- **In all three runs (13):** ar-beaverwd, ca-mammoth, co-boulder, ga-athensclarke, in-bloomington, ks-kucampus, mi-annarbor, mn-minneapolis, or-eweb, sc-florence, tn-kub, vt-richmond, wa-whitworth.
- **In two (4):** ak-unalaska, az-scottsdale, mt-whitefish, va-kinggeorge-canterbury.
- **In one (2):** fl-capecoral, hi-kauai-lihue.
- Only de-dover passed in every run.
