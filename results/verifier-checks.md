# Verifier checks run locally (no agent involved)

Run on 2026-10-26 (Sol submissions 2026-11-03) on macOS arm64, Docker 29.5 on Colima, Harbor 0.23.1.dev202609170426. The `docker run` checks use the task's limits (`--memory 4096m --memory-swap 4096m --cpus 2`).

| Check | Command | Result |
|---|---|---|
| Static checks | `for c in scripts/checks/check-*.sh; do bash $c tasks/water-report-extraction; done` (Python ≥ 3.11 on `PATH`) | 26/26 pass ([logs](checks/static/)) |
| Docker build + oracle | `harbor run -p tasks/water-report-extraction --agent oracle --env docker` | reward 1.0; all 20 held-out reports pass, 13 at score 1.000, lowest 0.957 ([output](execution/oracle/verifier/test-stdout.txt)) |
| Nop | `harbor run -p tasks/water-report-extraction --agent nop --env docker` | reward 0.0; the stub writes no records, all 20 reports fail ([output](execution/nop/verifier/test-stdout.txt)) |
| Sandbox: cheating extractor | verifier image, an `extract.py` that tries to list and read the keys under `/tests` and to write `/logs/verifier/reward.txt` ([script](exploit-test/extract.py), [output](exploit-test/output.txt)) | reward 0; `/tests` unreadable as `nobody` (`Permission denied`), reward write `Permission denied` |
| Sandbox: cheating extractor that leaves a background process | verifier image, an `extract.py` that detaches a process which keeps trying to write the reward ([script](exploit-test/daemon/extract.py), [output](exploit-test/daemon/output.txt)) | reward 0; nothing left running as `nobody` after the run |
| Out of memory | verifier image, an `extract.py` that allocates memory until the container's 4 GB is reached ([script](exploit-test/memory/extract.py), [output](exploit-test/memory/output.txt)) | reward 0; on all 20 reports the kernel killed the extractor (exit -9) and the verifier carried on |
| No network at grading | `harbor run --agent oracle --env docker` on a throwaway copy of the task whose solution installs a probe `extract.py` that makes real HTTPS, HTTP and UDP DNS requests ([script](exploit-test/network/extract.py), [output](exploit-test/network/output.txt)) | agent phase: all three answered; verifier phase: all three blocked |
| The three Opus 5.5 submissions in the real verifier | verifier image with each run's extractor folder copied to `/app/extractor` | reward 0 each; 6, 4 and 1 of 20 reports pass, matching the retained result summaries report by report ([outputs](trials/opus-5.5/)) |
| The three GPT-6.1 Sol submissions in the real verifier | the same | reward 0 each; 3, 5 and 4 of 20 reports pass; without the OCR model files left out of the records, the same score on every report as the full final folder ([outputs](trials/gpt-6.1-sol/)) |

Commands for the exploit tests are in [exploit-test/README.md](exploit-test/README.md). Commands for the re-grades (from the repo root, after `docker build -t ccr-verifier tasks/water-report-extraction/tests`):

```bash
docker run --rm --network none --memory 4096m --memory-swap 4096m --cpus 2 -v "$PWD/<dir-with-extract.py>":/submission:ro ccr-verifier \
  bash -c 'cp -r /submission/. /app/extractor/ && /tests/test.sh; cat /logs/verifier/reward.txt'
```

The extractor folder is copied in rather than mounted at `/app/extractor`, as Harbor does with the artifact: the verifier puts a fresh copy of the submission at `/app/extractor` before each report.
