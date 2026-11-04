#!/usr/bin/env bash
# One Codex trial on the water-report-extraction task with the current CI settings
# (codex + openai/gpt-6.1-sol, reasoning_effort=xhigh), then a short report
# of the reward, the held-out document results, the token usage and the duration.
#
#   ./run_codex_trial.sh          # one standard trial
#   ./run_codex_trial.sh run 2    # two more standard trials
#   ./run_codex_trial.sh cheat    # one /cheat trial (CI hack prompt appended)
#   PAUSING=1 ./run_codex_trial.sh   # same, but wait out ChatGPT-plan usage limits and resume
#                                    # (configs/pausing_codex.py; only active time counts
#                                    #  against the 2-hour budget; may take a day or more)
#
# Sign-in, in this order:
#   1. OPENAI_API_KEY already exported in your shell, or
#   2. ChatGPT login in ~/.codex/auth.json (`npm i -g @openai/codex && codex login`), or
#   3. you are asked for an API key (typed input is hidden).
# The key is passed through the environment, never on the command line, so it
# does not show up in the process list or in Harbor's logs.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
TASK="$ROOT/tasks/water-report-extraction"
JOBS="${JOBS:-$HOME/ccr-jobs}"
MODEL="${MODEL:-openai/gpt-6.1-sol}"
KIND="${1:-run}"
N="${2:-1}"
export PATH="$HOME/.local/bin:$PATH"

command -v harbor >/dev/null || { echo "harbor not found (uv tool install harbor)"; exit 1; }
docker info >/dev/null 2>&1 || colima start
[[ "$JOBS" == "$HOME"/* ]] || { echo "JOBS must be under \$HOME (Colima only mounts your home folder)"; exit 1; }

if [ -n "${OPENAI_API_KEY:-}" ]; then
  echo "Auth: OPENAI_API_KEY from your shell"
elif [ -f "$HOME/.codex/auth.json" ]; then
  export CODEX_FORCE_AUTH_JSON=1
  echo "Auth: ChatGPT login (~/.codex/auth.json, last refreshed $(date -r "$HOME/.codex/auth.json" '+%b %d %H:%M'))"
  echo "      Each run rotates the login inside its container; run 'codex logout && codex login' before every run."
else
  read -rsp "OpenAI API key (input hidden): " OPENAI_API_KEY; echo
  export OPENAI_API_KEY
  echo "Auth: API key entered above"
fi

AGENT=(--agent codex)
if [ "${PAUSING:-0}" = "1" ]; then
  export PYTHONPATH="$ROOT/configs${PYTHONPATH:+:$PYTHONPATH}"
  AGENT=(--agent pausing_codex:PausingCodex --agent-timeout-multiplier 16)
  echo "Pausing mode: waits for usage-limit resets and resumes; log in agent/pause-log.txt"
fi

EXTRA=()
if [ "$KIND" = "cheat" ]; then
  EXTRA=(--extra-instruction-path "$ROOT/configs/hack-trial-prompt.md")
fi
OUTDIR="$JOBS/${KIND}-codex-$(date +%Y%m%d_%H%M%S)"
echo "Running $N $KIND trial(s), one at a time: codex / $MODEL / xhigh  ->  $OUTDIR"
echo "Started $(date '+%H:%M'); each trial may take up to 2 hours."

caffeinate -dims harbor run -p "$TASK" "${AGENT[@]}" --model "$MODEL" --ak reasoning_effort=xhigh \
  -k "$N" --n-concurrent 1 --env docker --yes --agent-setup-timeout-multiplier 4 ${EXTRA[@]+"${EXTRA[@]}"} -o "$OUTDIR" || true

echo; echo "=== Report ($(date '+%H:%M'))"
python3 - "$OUTDIR" <<'EOF'
import json, sys, pathlib, re
root = pathlib.Path(sys.argv[1])
for trial in sorted(p for p in root.glob("*/*__*") if p.is_dir()):
    r = json.loads((trial / "result.json").read_text())
    vr = (r.get("verifier_result") or {}).get("rewards") or {}
    ar = r.get("agent_result") or {}
    exc = (r.get("exception_info") or {}).get("exception_type")
    print("trial:", trial.name)
    print("reward:", vr.get("reward"), "| exception:", exc or "none")
    for k in ("n_input_tokens", "n_cache_tokens", "n_output_tokens", "cost_usd"):
        if ar.get(k) is not None:
            print(f"{k}: {ar[k]}")
    ae = r.get("agent_execution") or {}
    if ae.get("started_at") and ae.get("finished_at"):
        from datetime import datetime
        f = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
        print("agent minutes:", round((f(ae["finished_at"]) - f(ae["started_at"])).total_seconds() / 60, 1))
    pl = trial / "agent" / "pause-log.txt"
    if pl.exists():
        print("pause log:"); print("  " + "\n  ".join(pl.read_text().splitlines()[-8:]))
    out = trial / "verifier" / "test-stdout.txt"
    if out.exists():
        for line in out.read_text().splitlines():
            if re.match(r"^h\d_\w+: ", line):
                print(" ", line)
EOF
