#!/usr/bin/env bash
# Run the Terminal-Bench 3 CI-equivalent checks and trials for this task locally.
#
#   configs/run_trials.sh doctor      # check prerequisites (Docker, Harbor, Claude token, Codex login)
#   configs/run_trials.sh checks      # static checks + implementation rubric review
#   configs/run_trials.sh validate    # Docker build, oracle, nop
#   configs/run_trials.sh run         # standard trials: 3 x Claude Code, 3 x Codex
#   configs/run_trials.sh cheat       # adversarial trials: 1 x Claude Code, 1 x Codex
#   configs/run_trials.sh analyze     # harbor analyze on every trial job
#   configs/run_trials.sh summarize   # write $OUT/trials/summary.md from the job outputs
#   configs/run_trials.sh all         # everything above, in order
#
# Defaults follow .github/harbor-run-defaults.yml of terminal-bench at commit 1dcda871
# (snapshot: configs/harbor-run-defaults.snapshot.yml). MODELS=task-md uses the pair
# named in the assignment brief instead.
#
# Auth (subscriptions, as described in the brief):
#   Claude Code: run `claude setup-token`, then export CLAUDE_CODE_OAUTH_TOKEN=<token>
#   Codex:       npm i -g @openai/codex && codex login   (agent reads ~/.codex/auth.json)
# With Colima, JOBS must be under $HOME so the verifier's log mount reaches the host.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TASK="${TASK:-$ROOT/tasks/water-report-extraction}"
JOBS="${JOBS:-$HOME/ccr-jobs}"
export OUT="${OUT:-$ROOT/results}"   # where logs, checks and the trial summary go
TB="${TB:-$HOME/terminal-bench}"   # terminal-bench checkout, for the static check scripts
export PATH="$HOME/.local/bin:$PATH"

if [ "${MODELS:-ci}" = "task-md" ]; then
  CLAUDE_MODEL="anthropic/claude-opus-5-5"
  CODEX_MODEL="openai/gpt-6-sol"
else
  CLAUDE_MODEL="anthropic/claude-fable-5-1"
  CODEX_MODEL="openai/gpt-6-astra"
fi
REVIEW_MODEL="anthropic/claude-sonnet-5"   # review_model in harbor-run-defaults.yml

need_token() { : "${CLAUDE_CODE_OAUTH_TOKEN:?run 'claude setup-token' and export CLAUDE_CODE_OAUTH_TOKEN}"; }
claude_auth() { echo --ae CLAUDE_FORCE_OAUTH=1 --ae "CLAUDE_CODE_OAUTH_TOKEN=$CLAUDE_CODE_OAUTH_TOKEN"; }
claude_args() {
  need_token
  echo --agent claude-code --model "$CLAUDE_MODEL" --ak reasoning_effort=max \
    --ae CLAUDE_CODE_MAX_OUTPUT_TOKENS=128000 $(claude_auth) --agent-setup-timeout-multiplier 4
}
codex_args() {
  echo --agent codex --model "$CODEX_MODEL" --ak reasoning_effort=xhigh --ae CODEX_FORCE_AUTH_JSON=1 \
    --agent-setup-timeout-multiplier 4
}
py311() {
  # The static check scripts need Python >= 3.11 (tomllib) as python3 on PATH.
  local py; py="$(uv python find 3.13 2>/dev/null || true)"
  [ -z "$py" ] && { uv python install 3.13 >/dev/null; py="$(uv python find 3.13)"; }
  mkdir -p "$JOBS/.py"; ln -sf "$py" "$JOBS/.py/python3"; ln -sf "$py" "$JOBS/.py/python"
  echo "$JOBS/.py"
}

do_doctor() {
  local ok=1
  docker info >/dev/null 2>&1 && echo "OK   docker engine" || { echo "FAIL docker engine (run: colima start)"; ok=0; }
  docker compose version >/dev/null 2>&1 && echo "OK   docker compose" || { echo "FAIL docker compose plugin"; ok=0; }
  command -v harbor >/dev/null && echo "OK   harbor $(harbor --version)" || { echo "FAIL harbor (uv tool install harbor)"; ok=0; }
  [ -d "$TB/scripts/checks" ] && echo "OK   terminal-bench checkout at $TB" || { echo "FAIL terminal-bench checkout (set TB=...)"; ok=0; }
  [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && echo "OK   CLAUDE_CODE_OAUTH_TOKEN set" || { echo "FAIL CLAUDE_CODE_OAUTH_TOKEN (claude setup-token)"; ok=0; }
  command -v codex >/dev/null && echo "OK   codex CLI" || { echo "FAIL codex CLI (npm i -g @openai/codex)"; ok=0; }
  [ -f "$HOME/.codex/auth.json" ] && echo "OK   codex login" || { echo "FAIL codex login (codex login)"; ok=0; }
  case "$JOBS" in "$HOME"/*) echo "OK   jobs dir $JOBS";; *) echo "WARN jobs dir outside \$HOME breaks Colima mounts";; esac
  [ "$ok" = 1 ]
}

do_checks() {
  local shim; shim="$(py311)"
  mkdir -p "$OUT/checks/static"
  for c in "$TB"/scripts/checks/check-*.sh; do
    printf '%-36s ' "$(basename "$c" .sh)"
    if PATH="$shim:$PATH" bash "$c" "$TASK" >"$OUT/checks/static/$(basename "$c" .sh).log" 2>&1; then
      echo PASS; else echo FAIL; fi
  done
  need_token
  # shellcheck disable=SC2046
  # Installing Claude Code in the container can exceed Harbor's 6-minute setup limit locally.
  harbor check "$TASK" -r "$ROOT/configs/task-implementation.toml" -m "$REVIEW_MODEL" \
    -c "$ROOT/configs/check-job.json" $(claude_auth) -o "$JOBS/rubric" | tee "$OUT/checks/rubric-review.txt"
}

do_validate() { for a in oracle nop; do harbor run -p "$TASK" --agent "$a" --env docker --yes -o "$JOBS/$a"; done; }

do_run() {
  # shellcheck disable=SC2046
  harbor run -p "$TASK" $(claude_args) -k 3 --env docker --yes -o "$JOBS/run-claude"
  # shellcheck disable=SC2046
  harbor run -p "$TASK" $(codex_args) -k 3 --env docker --yes -o "$JOBS/run-codex"
}

do_cheat() {
  # The hack prompt is appended after the task instruction, as in CI's /cheat.
  # shellcheck disable=SC2046
  harbor run -p "$TASK" $(claude_args) -k 1 --env docker --yes -o "$JOBS/cheat-claude" \
    --extra-instruction-path "$ROOT/configs/hack-trial-prompt.md"
  # shellcheck disable=SC2046
  harbor run -p "$TASK" $(codex_args) -k 1 --env docker --yes -o "$JOBS/cheat-codex" \
    --extra-instruction-path "$ROOT/configs/hack-trial-prompt.md"
}

do_analyze() {
  for d in "$JOBS"/run-* "$JOBS"/cheat-*; do
    for job in "$d"/*/; do
      need_token
      # shellcheck disable=SC2046
      harbor analyze "$job" -m "$REVIEW_MODEL" -r "$ROOT/configs/trial-analysis.toml" \
        $(claude_auth) | tee "$job/analysis.txt"
    done
  done
}

do_summarize() { python3 "$ROOT/configs/summarize_trials.py" "$JOBS"; }

case "${1:-}" in
  doctor) do_doctor ;;
  checks) do_checks ;;
  validate) do_validate ;;
  run) do_run ;;
  cheat) do_cheat ;;
  analyze) do_analyze ;;
  summarize) do_summarize ;;
  all) do_doctor && do_checks && do_validate && do_run && do_cheat && do_analyze && do_summarize ;;
  *) sed -n '2,22p' "$0"; exit 1 ;;
esac
