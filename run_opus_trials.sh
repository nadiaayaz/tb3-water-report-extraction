#!/usr/bin/env bash
# Standard Harbor trials of the water-report-extraction task with Claude Code and
# Claude Opus 5.5 at max effort, one at a time, then a short report and a
# record of each trial under results/trials/opus-5.5-harbor/.
#
#   ./run_opus_trials.sh            # three standard trials
#   ./run_opus_trials.sh run 1      # one standard trial
#   ./run_opus_trials.sh cheat      # one /cheat trial (CI hack prompt appended)
#   ./run_opus_trials.sh record ~/ccr-jobs/run-opus-<stamp>   # record a finished job again
#
# Sign-in, in this order:
#   1. CLAUDE_CODE_OAUTH_TOKEN exported in your shell (subscription; `claude setup-token`), or
#   2. ANTHROPIC_API_KEY exported in your shell (API credits), or
#   3. you are asked for a token (typed input is hidden).
# The credential is passed through the environment, never on the command line,
# so it does not show up in the process list or in Harbor's job config.
#
# Each trial has the task's 2-hour agent limit. A trial that ends in a timeout,
# an API or usage-limit error or a container failure is not a model failure:
# the record marks it INVALID and it has to be repeated.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
TASK="$ROOT/tasks/water-report-extraction"
JOBS="${JOBS:-$HOME/ccr-jobs}"
MODEL="${MODEL:-anthropic/claude-opus-5-5}"   # hyphens: the dotted name is rejected by the API
KIND="${1:-run}"
export PATH="$HOME/.local/bin:$PATH"

record() {  # $1 = Harbor job folder, $2 = standard | cheat
  python3 "$ROOT/configs/record_trials.py" "$1" "$ROOT/results/trials/opus-5.5-harbor" "$2"
}

if [ "$KIND" = "record" ]; then
  record "${2:?job folder}" "${3:-standard}"; exit 0
fi
if [ "$KIND" = "cheat" ]; then N="${2:-1}"; else N="${2:-3}"; fi

command -v harbor >/dev/null || { echo "harbor not found (uv tool install harbor)"; exit 1; }
if ! docker info >/dev/null 2>&1; then
  if command -v colima >/dev/null; then colima start; else echo "Docker is not running"; exit 1; fi
fi
if command -v colima >/dev/null; then
  [[ "$JOBS" == "$HOME"/* ]] || { echo "JOBS must be under \$HOME (Colima only mounts your home folder)"; exit 1; }
fi

if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  export CLAUDE_FORCE_OAUTH=1
  echo "Auth: CLAUDE_CODE_OAUTH_TOKEN from your shell (subscription)"
elif [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "Auth: ANTHROPIC_API_KEY from your shell (API credits)"
else
  read -rsp "Claude Code OAuth token from 'claude setup-token' (input hidden): " CLAUDE_CODE_OAUTH_TOKEN; echo
  [ -n "$CLAUDE_CODE_OAUTH_TOKEN" ] || { echo "no token given"; exit 1; }
  export CLAUDE_CODE_OAUTH_TOKEN CLAUDE_FORCE_OAUTH=1
  echo "Auth: token entered above"
fi
# Claude Code's default output cap is lower than the model's limit (as in the CI defaults).
export CLAUDE_CODE_MAX_OUTPUT_TOKENS=128000

EXTRA=()
if [ "$KIND" = "cheat" ]; then
  EXTRA=(--extra-instruction-path "$ROOT/configs/hack-trial-prompt.md")
fi
OUTDIR="$JOBS/${KIND}-opus-$(date +%Y%m%d_%H%M%S)"
echo "Running $N $KIND trial(s), one at a time: claude-code / $MODEL / max  ->  $OUTDIR"
echo "Started $(date '+%H:%M'); each trial may take up to 2 hours plus set-up."

AWAKE=(); command -v caffeinate >/dev/null && AWAKE=(caffeinate -dims)
${AWAKE[@]+"${AWAKE[@]}"} harbor run -p "$TASK" --agent claude-code --model "$MODEL" --ak reasoning_effort=max \
  -k "$N" --n-concurrent 1 --env docker --yes --agent-setup-timeout-multiplier 4 \
  ${EXTRA[@]+"${EXTRA[@]}"} -o "$OUTDIR" || true

echo; echo "=== Report ($(date '+%H:%M'))"
if [ "$KIND" = "cheat" ]; then record "$OUTDIR" cheat; else record "$OUTDIR" standard; fi
