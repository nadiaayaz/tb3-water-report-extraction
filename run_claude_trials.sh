#!/usr/bin/env bash
# One-shot runner: every check and Claude Code trial for the water-report-extraction task, then a results table.
#
#   ./run_claude_trials.sh
#
# What it does, in order (logs everything to $OUT/run_<timestamp>.log):
#   1. gets a Claude Code token (runs `claude setup-token` if CLAUDE_CODE_OAUTH_TOKEN is not set)
#   2. makes sure Docker (Colima) is running
#   3. static checks + implementation rubric review (Sonnet 5 reviewer, as in CI)
#   4. standard trials: 3 x Claude Code / Fable 5.1 and 3 x Claude Code / Opus 5.5 (max effort)
#   5. cheat trials:    1 x each model, with CI's hack prompt appended
#   6. trial analysis, then $OUT/trials/summary.md
# The Mac is kept awake for the whole run (caffeinate). Re-running is safe: finished
# steps are skipped unless FORCE=1. Codex trials are not included (no Codex CLI here).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"

# Keep the Mac awake for the whole run.
if [ -z "${UNDER_CAFFEINATE:-}" ]; then
  export UNDER_CAFFEINATE=1
  exec caffeinate -dims "$0" "$@"
fi

TASK="${TASK:-$ROOT/tasks/water-report-extraction}"
JOBS="${JOBS:-$HOME/ccr-jobs}"
export OUT="${OUT:-$ROOT/results}"   # where logs, checks and the trial summary go
TB="${TB:-$HOME/terminal-bench}"
CONCURRENT="${CONCURRENT:-1}"   # parallel trials per job; >1 slows agent installs and risks rate limits
mkdir -p "$ROOT/results" "$JOBS"
LOG="$OUT/run_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1
step() { echo; echo "=== $(date '+%H:%M:%S') $*"; }

# 1. Claude Code token
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  step "Claude Code token"
  echo "A browser window will open to authorise a long-lived token for these trials."
  claude setup-token
  echo
  read -rsp "Paste the token printed above (input hidden), then press Enter: " CLAUDE_CODE_OAUTH_TOKEN
  echo
  [ -n "$CLAUDE_CODE_OAUTH_TOKEN" ] || { echo "No token given; stopping."; exit 1; }
  export CLAUDE_CODE_OAUTH_TOKEN
fi
AUTH=(--ae CLAUDE_FORCE_OAUTH=1 --ae "CLAUDE_CODE_OAUTH_TOKEN=$CLAUDE_CODE_OAUTH_TOKEN")

# 2. Docker
step "Docker"
docker info >/dev/null 2>&1 || colima start
docker info --format 'engine {{.ServerVersion}} ({{.Architecture}})' || { echo "Docker is not available"; exit 1; }
command -v harbor >/dev/null || { echo "Harbor not found (uv tool install harbor)"; exit 1; }

# A step counts as done only if it has trial results and none of them ended in an exception.
done_job() {
  [ -z "${FORCE:-}" ] || return 1
  ls "$JOBS/$1"/*/*/result.json >/dev/null 2>&1 || return 1
  ! grep -lE '"exception_type": *"[A-Za-z]' "$JOBS/$1"/*/*/result.json >/dev/null 2>&1
}

# 3. Static checks + rubric review
step "Static checks and rubric review"
if done_job rubric; then echo "rubric review already done (FORCE=1 to redo)"
else rm -rf "$JOBS/rubric"; JOBS="$JOBS" TB="$TB" configs/run_trials.sh checks || echo "WARN: checks step reported a problem; continuing"
fi

# 4-5. Trials
declare -a MODELS=("fable:anthropic/claude-fable-5-1" "opus:anthropic/claude-opus-5-5")
for entry in "${MODELS[@]}"; do
  tag="${entry%%:*}"; model="${entry#*:}"
  common=(--agent claude-code --model "$model" --ak reasoning_effort=max
          --ae CLAUDE_CODE_MAX_OUTPUT_TOKENS=128000 "${AUTH[@]}" --env docker --yes --n-concurrent "$CONCURRENT"
          --agent-setup-timeout-multiplier 4)   # local agent installs can exceed the 6-minute default
  step "Standard trials: 3 x $model"
  if done_job "run-claude-$tag"; then echo "already done"
  else rm -rf "$JOBS/run-claude-$tag"; harbor run -p "$TASK" "${common[@]}" -k 3 -o "$JOBS/run-claude-$tag" || echo "WARN: harbor run exited non-zero"
  fi
  step "Cheat trial: 1 x $model"
  if done_job "cheat-claude-$tag"; then echo "already done"
  else rm -rf "$JOBS/cheat-claude-$tag"; harbor run -p "$TASK" "${common[@]}" -k 1 -o "$JOBS/cheat-claude-$tag" \
         --extra-instruction-path "$ROOT/configs/hack-trial-prompt.md" || echo "WARN: harbor run exited non-zero"
  fi
done

# 6. Analysis + summary
step "Trial analysis"
for d in "$JOBS"/run-claude-* "$JOBS"/cheat-claude-*; do
  for job in "$d"/*/; do
    [ -f "$job/analysis.txt" ] && [ -z "${FORCE:-}" ] && continue
    harbor analyze "$job" -m anthropic/claude-sonnet-5 -r "$ROOT/configs/trial-analysis.toml" \
      "${AUTH[@]}" > "$job/analysis.txt" 2>&1 \
      && echo "analysed $job" || echo "WARN: analysis failed for $job (see $job/analysis.txt)"
  done
done

step "Summary"
python3 configs/summarize_trials.py "$JOBS"
echo
echo "Done. Log: $LOG"
echo "Results table: $OUT/trials/summary.md   Raw jobs: $JOBS"
