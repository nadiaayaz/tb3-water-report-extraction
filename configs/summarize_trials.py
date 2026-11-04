"""Summarize Harbor job directories into markdown tables for the report.

  python3 configs/summarize_trials.py [JOBS_DIR]     (default: ~/ccr-jobs)

Writes $OUT/trials/summary.md (default results/trials/summary.md) and prints it. For every trial it reports the
kind (standard/cheat), agent and model, reward, the lowest held-out document score, as printed by the verifier,
whether the run is valid (no agent or infrastructure exception), duration, and
the exception type if any.
"""

import datetime as dt
import json
import re
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOBS = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path.home() / "ccr-jobs"


def ts(v):
    if not v:
        return None
    v = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], v.replace("Z", "+00:00"))
    return dt.datetime.fromisoformat(v)


def unredact(text):
    # Harbor masks agent-env values in its outputs. CLAUDE_FORCE_OAUTH=1 makes it replace
    # every "1" with "[REDACTED]", which breaks JSON numbers and dates; restore them.
    return text.replace("[REDACTED]", "1")


def trials(kind_dir):
    for result in sorted(kind_dir.glob("*/*/result.json")):
        trial = result.parent
        r = json.loads(unredact(result.read_text()))
        stdout = trial / "verifier" / "test-stdout.txt"
        auc = None
        if stdout.exists():
            w2 = [float(v) for v in re.findall(r": score ([0-9.]+) \(", unredact(stdout.read_text()))]
            auc = min(w2) if w2 else None
        exc = (r.get("exception_info") or {}).get("exception_type")
        reward = ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        agent = (r.get("agent_info") or {})
        model = (agent.get("model_info") or {}).get("name") or ""
        start, end = ts(r.get("started_at")), ts(r.get("finished_at"))
        minutes = round((end - start).total_seconds() / 60, 1) if start and end else None
        yield {"trial": trial.name, "agent": agent.get("name", ""), "model": model, "reward": reward,
               "auc": auc, "valid": exc is None and reward is not None, "minutes": minutes, "exception": exc or ""}


def main():
    rows = []
    for kind in ("run", "cheat"):
        for d in sorted(JOBS.glob(f"{kind}-*")):
            for t in trials(d):
                rows.append({"kind": "standard" if kind == "run" else "cheat", **t})
    lines = ["| # | Kind | Agent / model | Reward | Lowest held-out document score | Valid run? | Minutes | Exception |",
             "|---|---|---|---|---|---|---|---|"]
    for i, t in enumerate(rows, 1):
        auc = "" if t["auc"] is None else f"{t['auc']:.3f}%"
        lines.append(f"| {i} | {t['kind']} | {t['agent']} / {t['model']} | {t['reward']} | {auc} | "
                     f"{'yes' if t['valid'] else 'no'} | {t['minutes']} | {t['exception']} |")
    std = [t for t in rows if t["kind"] == "standard" and t["valid"]]
    cheat = [t for t in rows if t["kind"] == "cheat" and t["valid"]]
    verdict = [
        "",
        f"Standard trials: {len(std)} valid, {sum(t['reward'] == 1 for t in std)} passed "
        f"(requirement: 6 valid, 0 passed).",
        f"Cheat trials: {len(cheat)} valid, {sum((t['reward'] or 0) > 0 for t in cheat)} with nonzero reward "
        f"(requirement: 2 valid, 0 nonzero).",
    ]
    rubric = sorted(JOBS.glob("rubric/**/verdicts.json"))
    if rubric:
        v = json.loads(rubric[-1].read_text())
        items = v.items() if isinstance(v, dict) else [(x.get("name"), x) for x in v]
        failed = [k for k, x in items if str((x or {}).get("verdict", x)).lower().startswith("fail")]
        verdict.append(f"Rubric review: {len(list(items))} criteria, failed: {failed or 'none'} ({rubric[-1]})")
    text = "\n".join(lines + verdict) + "\n"
    out = Path(os.environ.get("OUT", ROOT / "results")) / "trials" / "summary.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
