# Drinking-Water Report Extraction

A drinking-water report may list a concentration beside its regulatory limit, put the measured range on the next line, and print the sample date under a heading shared by several contaminants. Another utility may arrange the same information quite differently. An extractor that reads one report correctly can still assign the wrong meaning to a column in the next.

I built this Terminal-Bench 3 task to test that problem directly: use four published water-quality reports and their answer keys to write an extractor for reports from 20 other water systems. The deliverable is a Python program that converts each report's contaminant results into structured JSON.

I am [Nadia Ayaz](https://github.com/nadiaayaz). The runnable package is in [`tasks/water-report-extraction/`](tasks/water-report-extraction/); its [reviewer notes](tasks/water-report-extraction/README.md) explain the reference extractor and verification design. I packaged it against `harbor-framework/terminal-bench` commit `1dcda871` and Harbor `0.23.1.dev202609170426`.

## Start with the example reports

The workspace supplies four Consumer Confidence Reports, the annual drinking-water quality reports published by US water systems:

- [`visible/`](tasks/water-report-extraction/environment/data/visible/): the four PDFs and a JSON answer key for each.
- [`FORMAT.md`](tasks/water-report-extraction/environment/data/FORMAT.md): which tables and rows to extract, how to read each field and how the output is scored.
- [`extractor/extract.py`](tasks/water-report-extraction/environment/data/extractor/extract.py): the stub to replace.

The submitted program must accept a new report and write its detected-contaminant results:

```bash
python3 /app/extractor/extract.py REPORT.pdf OUTPUT.json
```

Each record includes the contaminant, system ID, sample date, measured level, range endpoints, unit, regulatory limit (MCL), goal (MCLG) and violation flag. The agent has two hours to develop its solution. Extraction runs offline with Python 3.12, pdfplumber, pypdf and poppler-utils, with a 120-second limit per report. Supporting files under `/app/extractor/` are retained. The full contract is in [instruction.md](tasks/water-report-extraction/instruction.md).

All 24 PDFs are real publications downloaded from utility and municipal websites, with one hosted by a regional aquifer board. I kept the documents unedited and built the answer keys for this task. No reuse licence was found in the PDFs. [Data sources](DATA-SOURCES.md) lists every document and its origin; [NOTICE.md](NOTICE.md) explains the research use and removal procedure.

## What changes between reports

**The page structure changes.** The collection includes two-page brochures with several text columns, reports as long as 17 pages, ruled and unruled tables, merged headings and results for several plants side by side. Some tables place dates down the rows and contaminants across the columns. A parser has to identify the table's boundaries and relationships before reading its values.

**The meaning of a cell depends on its neighbours.** A measured level can share a cell with a label or unit. A range may appear beneath its parent row. Compliance text, footnotes and sample dates need to stay attached to the correct result. Taking a plausible number from the wrong column can produce a reasonable-looking record that is still incorrect.

The four examples give the agent a way to test its work. Every expected value is printed in the source PDF. The task requires no calculated results or unit conversion. I excluded reports whose interpretation could not be settled by the output rules, along with reports whose results tables existed only as images. This resembles the work of an environmental analyst or data engineer compiling measurements from independently designed utility reports.

My [reference extractor](tasks/water-report-extraction/solution/) reads positioned text, separates brochure columns and identifies results tables from their headings. It uses printed rules or whitespace to recover rows and columns, joins continuation lines to their parent results, and assigns fields according to `FORMAT.md`. It uses pdfplumber and contains no utility names, place names, filenames or branches for individual reports.

## How an extraction earns a pass

The [scorer](tasks/water-report-extraction/tests/score.py) matches predicted records to answer-key records by contaminant name. Recovering a row earns one fact; each agreeing field earns another. Additional records count against the score.

| Part of the record | How agreement is checked |
|---|---|
| Contaminant row | Name matching normalises case, spacing, punctuation and footnotes, with fixed rules for shortened names |
| Level, range endpoints, MCL and MCLG | Numerical comparison uses a relative tolerance of 1e-6; recognised non-detect, null and inequality forms are accepted |
| System ID, sample date and unit | Fixed normalisation rules allow equivalent spellings and representations |
| Violation flag | Y/N, Yes/No and boolean forms are accepted |

Each of the 20 held-out reports must score at least 0.95. The [tests](tasks/water-report-extraction/tests/test_outputs.py) also check that the extractor exists and the held-out set remains intact. Reward is binary: every check must pass to earn 1.

The 5% allowance accommodates occasional parsing loss, including characters missing from a PDF's text layer. It usually cannot absorb a column interpreted incorrectly throughout a table, because that mistake repeats on every affected row. The [reviewer notes](tasks/water-report-extraction/README.md#verification-explanation) describe the comparison rules and tolerance in more detail.

Hidden PDFs and keys live in a separate verifier image under root-only `/tests`. Harbor transfers the submitted extractor directory; the verifier preserves a root-only copy and restores it before each report. It supplies one PDF in a fresh directory and runs the extractor as `nobody`, offline, in its own process session. A 120-second watchdog and a 4 GB container limit bound execution. If memory is exhausted, the extractor is the first kill target and that report fails. After each report, remaining processes owned by `nobody` are killed. Root computes the score and writes the reward to a root-only directory. [Verifier checks](results/verifier-checks.md) record the isolation and exploit tests.

## Calibration before interpretation

I collected the reports on 2026-10-21, excluding reports prepared on a fixed state-agency template. A fixed-seed draw selected the held-out set from the collected pool. It contains 20 systems in 20 states, with no system shared between the examples and evaluation. Each answer key was built from two independent readings against the output format; disagreements were resolved on the page image.

| Calibration input | Example reports | Held-out reports | Outcome |
|---|---|---|---|
| Reference extractor, also used by the oracle | All four score 1.000 | 20/20 pass; 13 score 1.000, lowest 0.957 | Pass |
| Unchanged stub | No records written | 0/20 pass | Fail |

The lowest reference result is Scottsdale: 198 of 207 facts, or 0.9565 before rounding. I audited the reference's remaining losses against the format. They come from incomplete parsing or text-layer defects such as a dropped µ; none requires a reading rule absent from the specification. In the oracle run, each report took 0.5–2.9 seconds including verifier setup.

These checks establish that the packaged task has a working solution and that an empty submission fails.

## Recorded Harbor trials

I ran three trials per model through Harbor, using the Claude Code (`claude-code`) harness: Claude Opus 5.5 at `max` effort and GPT-6.1 Sol at `xhigh`. Each had a two-hour agent limit.

| Model and effort | Reports passed, trials 1 / 2 / 3 | Minutes to finish, trials 1 / 2 / 3 | Outcome |
|---|---|---|---|
| Claude Opus 5.5, `max` | 6 / 4 / 1 of 20 | 54 / 80 / 21 | All three fail; reward 0 |
| GPT-6.1 Sol, `xhigh` | 3 / 5 / 4 of 20 | 106 / 116 / 113 | All three fail; reward 0 |

Every submission finished on its own before the time limit. Opus scored 1.000 on all four examples in each trial. Sol's closing reports claimed 99.4–100% agreement on the examples and described successful tests on generated layouts. All three Sol submissions also included OCR fallbacks; removing the OCR models from trials 2 and 3 changes no held-out score because every graded report has a text layer.

Failures repeatedly involved the interpretation of a table. All six submissions misread Minneapolis's violation column. Four recovered only 4–6 of Richmond's 24 records from a PFAS table arranged with dates as rows. Five missed Bloomington's system ID, printed below its label. Across the set, twelve reports failed in every submission; Dover was the only report to pass all six.

The failures were generally well below the threshold: 40 of Opus's 49 failed report evaluations and 31 of Sol's 48 scored below 0.80. Detailed scores, delivered extractors and closing messages are in the [Opus records](results/trials/opus-5.5/) and [Sol records](results/trials/gpt-6.1-sol/). The saved extractors were graded in the repository's verifier image; the Opus re-grades reproduce the retained scores report by report.

The Opus trials saw earlier `FORMAT.md` revisions: v1, v2 and v3. The packaged v3.1 clarifies several reading rules while retaining the same examples and keys; all reported submissions are graded against v3.1 keys. Sol used v3.1.

For the adversarial check, both attempts received the unchanged [TB3 CI hack prompt](configs/hack-trial-prompt.md) after the task instruction. Opus stopped after one minute when a turn was cut off by Anthropic's safety classifier, reported no bypass and left the stub; the verifier awarded 0. The GPT-6.1 Sol attempt was refused by OpenAI's security classifier before any command ran and was not graded. The [adversarial records](results/cheat-trials/) retain those outcomes under the same Harbor and Claude Code setup described above.

Package validation includes [26 passing static checks](results/checks/static/), successful agent and verifier image builds, [oracle reward 1](results/execution/oracle/verifier/test-stdout.txt) and [nop reward 0](results/execution/nop/verifier/test-stdout.txt). The [implementation review](results/checks/implementation/check-result.json) recorded 34 passes, no failures and one item marked not applicable; [run details](results/checks/implementation/run.json) accompany that report.

## Run or inspect the package

Use the repository root for these commands. Static checks require a terminal-bench checkout and Python 3.11 or newer:

```bash
for check in /path/to/terminal-bench/scripts/checks/check-*.sh; do
  bash "$check" tasks/water-report-extraction
done
```

The task can be exercised without calling a model:

```bash
harbor run -p tasks/water-report-extraction --agent oracle --env docker
harbor run -p tasks/water-report-extraction --agent nop --env docker

# Re-score one saved submission.
docker build -t ccr-verifier tasks/water-report-extraction/tests
docker run --rm --network none --memory 4096m --memory-swap 4096m --cpus 2 \
  -v "$PWD/results/trials/opus-5.5/trial-1/submission":/submission:ro ccr-verifier \
  bash -c 'cp -r /submission/. /app/extractor/ && /tests/test.sh; cat /logs/verifier/reward.txt'
```

For new Opus trials, configure the credentials for your Harbor environment, then use the [launcher](run_opus_trials.sh):

```bash
./run_opus_trials.sh          # three Claude Code / Opus 5.5 max trials
./run_opus_trials.sh cheat    # one adversarial trial
```

The broader [Claude workflow](run_claude_trials.sh) also provides checks, rubric review and analysis. The original VM configurations, including GPT-6.1 Sol routing for Claude Code, are unavailable.

For a code review, start with [`task.toml`](tasks/water-report-extraction/task.toml), the [reference extractor](tasks/water-report-extraction/solution/) and the [verifier entry point](tasks/water-report-extraction/tests/test.sh). [configs/](configs/) contains the reproduction prompts, rubric and analysis tools. An implementation review can be run with a Claude sign-in token for the review model (`claude setup-token`):

```bash
harbor check tasks/water-report-extraction --rubric configs/task-implementation.toml --agent claude-code \
  --model anthropic/claude-sonnet-5 --ae CLAUDE_FORCE_OAUTH=1 \
  --ae CLAUDE_CODE_OAUTH_TOKEN=$CLAUDE_CODE_OAUTH_TOKEN -c configs/check-job.json
```

## My experience with the underlying work

I am a Senior AI Engineer with more than eight years of experience in production machine learning, document intelligence and LLM systems. At SmartAdvocate, I built distributed NLP and document-processing pipelines that turned millions of fragmented legal case files into structured, searchable records.

In my current work as an AI solution architect, I build evaluation and observability frameworks for agentic systems. That experience informed this task's emphasis on preserving field meaning across unfamiliar layouts, checking outputs against independently read keys, and separating a strong fit to examples from success on new documents. For this task I collected the reports, built the keys, implemented the reference extractor and audited its remaining errors against the output definition.
