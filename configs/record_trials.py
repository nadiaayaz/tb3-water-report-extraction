#!/usr/bin/env python3
"""Record the trials of one Harbor job in the repository.

    record_trials.py JOB_DIR RESULTS_DIR [standard|cheat]

For every trial of the job this writes RESULTS_DIR/trial-N/ (cheat-N/ for cheat
trials) with the delivered file, the verifier output, the agent's closing
message and a run summary. Agent transcripts, job configs and sign-in files are
never copied. A trial that was already recorded (same Harbor trial name) is
skipped, so the script can be run again.
"""
import json
import pathlib
import re
import shutil
import sys
from datetime import datetime

TOTAL = 20
LIMIT_MIN = 120


def when(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def closing_message(trial):
    path = trial / "agent" / "trajectory.json"
    if not path.exists():
        return None
    try:
        steps = json.loads(path.read_text()).get("steps", [])
    except ValueError:
        return None
    for step in reversed(steps):
        msg = step.get("message")
        if step.get("source") == "agent" and isinstance(msg, str) and msg.strip():
            return msg.strip()
    return None


def main():
    job, results = pathlib.Path(sys.argv[1]).expanduser(), pathlib.Path(sys.argv[2])
    kind = sys.argv[3] if len(sys.argv) > 3 else "standard"
    prefix = "cheat" if kind == "cheat" else "trial"
    results.mkdir(parents=True, exist_ok=True)
    done = {}
    for f in results.glob(f"{prefix}-*/run.json"):
        done[json.loads(f.read_text()).get("harbor_trial")] = f.parent.name
    trials = sorted((p for p in job.glob("*/*__*") if (p / "result.json").exists()),
                    key=lambda p: (p / "result.json").stat().st_mtime)
    if not trials:
        print("no finished trial under", job)
    for trial in trials:
        r = json.loads((trial / "result.json").read_text())
        name = r.get("trial_name", trial.name)
        if name in done:
            print(f"{name}: already recorded as {done[name]}")
            continue
        n = 1 + max([int(p.name.split("-")[1]) for p in results.glob(f"{prefix}-*")] or [0])
        dest = results / f"{prefix}-{n}"
        (dest / "verifier").mkdir(parents=True)

        sub = trial / "artifacts" / "app" / "extractor"
        if sub.is_dir():
            # The agent's own files; third-party packages it vendored (files over 1 MB or
            # under a vendor/ or site-packages/ folder) are listed, not copied.
            (dest / "submission").mkdir()
            skipped = []
            for f in sorted(p for p in sub.rglob("*") if p.is_file()):
                rel = f.relative_to(sub)
                if f.stat().st_size > 1 << 20 or {"vendor", "site-packages", "__pycache__"} & set(rel.parts):
                    skipped.append(str(rel))
                    continue
                (dest / "submission" / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(f, dest / "submission" / rel)
            if skipped:
                (dest / "submission-files-not-copied.txt").write_text("\n".join(skipped) + "\n")
        out = trial / "verifier" / "test-stdout.txt"
        text = out.read_text() if out.exists() else ""
        if text:
            (dest / "verifier" / "test-stdout.txt").write_text(text)
        msg = closing_message(trial)
        if msg:
            (dest / "agent_final_report.md").write_text(msg + "\n")

        failed = {m.group(1): float(m.group(2))
                  for m in re.finditer(r"AssertionError: (\S+): score ([0-9.]+) is below", text)}
        failed_tests = set(re.findall(r"^FAILED \S+test_held_out_report\[(\S+?)\]", text, re.M))
        ran = bool(re.search(r"\d+ (passed|failed)", text))
        exc = (r.get("exception_info") or {}).get("exception_type")
        ae, ar = r.get("agent_execution") or {}, r.get("agent_result") or {}
        minutes = None
        if ae.get("started_at") and ae.get("finished_at"):
            minutes = round((when(ae["finished_at"]) - when(ae["started_at"])).total_seconds() / 60)
        reward = ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        if exc:
            ended = f"INVALID: {exc} (not a model failure; repeat the trial)"
        elif not ran:
            ended = "INVALID: the verifier did not run"
        else:
            ended = "by itself"
        run = {
            "trial": n, "kind": kind, "agent": "Claude Code via Harbor",
            "model": ((r.get("agent_info") or {}).get("model_info") or {}).get("name") or "claude-opus-5-5",
            "reasoning_effort": "max", "harbor_trial": name,
            "agent_started_utc": ae.get("started_at"), "agent_finished_utc": ae.get("finished_at"),
            "minutes": minutes, "agent_limit_minutes": LIMIT_MIN,
            "n_input_tokens": ar.get("n_input_tokens"), "n_cache_tokens": ar.get("n_cache_tokens"),
            "n_output_tokens": ar.get("n_output_tokens"),
            "exception": exc, "reward": reward,
            "held_out_passed": TOTAL - len(failed_tests) if ran else None, "held_out_total": TOTAL,
            "failed": dict(sorted(failed.items())), "ended": ended,
        }
        (dest / "run.json").write_text(json.dumps(run, indent=1) + "\n")
        print(f"{name} -> {dest.name}: reward {reward}, "
              f"{run['held_out_passed']} of {TOTAL} passed, {minutes} min, ended {ended}")


if __name__ == "__main__":
    main()
