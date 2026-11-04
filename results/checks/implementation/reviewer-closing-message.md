I completed a thorough review of the `water-report-extraction` task, including actually running the reference solution against all 4 visible and all 20 hidden held-out PDFs to empirically verify the README's claims.

Key findings:
- **Empirically verified**: The reference solution passed all 20 hidden reports (reproducing the exact `az-scottsdale` score of 0.9565 = 198/207 claimed in the README) and all 4 visible reports; the stub extractor scored 0 on all 20 as claimed.
- All 34 criteria passed except `do_not_modify_enforced`, which is **not applicable** (the instruction contains no such constraint).
- This is a well-constructed task: real-world document extraction, strong anti-cheat isolation (root-only hidden data, pristine-copy resets, unprivileged execution), binary reward properly thresholded, CTRF reporting wired up, and README explanations that are detailed and verifiably consistent with the actual test/solution code.

Result written to `/app/task/check-result.json`.
