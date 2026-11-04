"""Harbor's Codex agent, made to survive ChatGPT-plan usage limits.

Identical to `harbor.agents.installed.codex.Codex` (same install, auth, model,
reasoning effort and flags), except that the single `codex exec` call is
wrapped in a loop running inside the task container:

  * Codex runs as usual. If it stops without completing its turn (for example
    because the plan's 5-hour or weekly limit was reached), the loop reads the
    reset time Codex itself recorded in its session log, waits until then, and
    resumes the same session with `codex exec resume --last` and a neutral note.
  * Only active time counts; waiting for a reset does not. The agent is told the
    task's budget (7200 s, unchanged in the instruction) and resume notes count
    down from it, but the hard stop is 16200 s of active time: a 90-minute grace
    so the agent finishes on its own clock instead of being cut off near the limit.
    Each segment runs under `timeout` for the remaining hard budget.
  * Every segment and pause is logged to /logs/agent/pause-log.txt.

Run with Harbor's wall-clock agent limit raised, e.g. --agent-timeout-multiplier 16.
"""

import os
import shlex

from harbor.agents.installed.codex import Codex

STATED_SEC = int(os.environ.get("CODEX_STATED_SEC", "7200"))   # what the instruction tells the agent
HARD_SEC = int(os.environ.get("CODEX_HARD_SEC", str(STATED_SEC + 5400)))  # hard stop on active time
RESUME_NOTE = (
    "Your previous turn was interrupted by an API usage limit, not by anything you did. "
    "Continue the task from where you left off. About {remaining} seconds of your time budget remain."
)

STATUS_PY = r'''
import glob, json, os, sys, time
off = int(sys.argv[1])
out = "/logs/agent/codex.txt"
events = []
try:
    with open(out, "rb") as f:
        f.seek(off)
        for line in f.read().decode("utf-8", "replace").splitlines():
            if line.startswith("{"):
                try: events.append(json.loads(line))
                except Exception: pass
except FileNotFoundError:
    pass
ends = [e for e in events if e.get("type") in ("turn.completed", "turn.failed", "error")]
if ends and ends[-1].get("type") == "turn.completed":
    print("done"); sys.exit()
err = ""
for e in reversed(events):
    if e.get("type") in ("error", "turn.failed"):
        err = json.dumps(e)[:300]; break
resets = []
files = sorted(glob.glob(os.path.expandvars("$CODEX_HOME/sessions/**/*.jsonl"), recursive=True), key=os.path.getmtime)
if files:
    for line in reversed(open(files[-1], encoding="utf-8", errors="replace").read().splitlines()):
        if '"rate_limits"' not in line: continue
        try: rl = json.loads(line)["payload"]["rate_limits"]
        except Exception: continue
        for k in ("primary", "secondary"):
            w = rl.get(k) or {}
            if (w.get("used_percent") or 0) >= 100 and w.get("resets_at"):
                resets.append(int(w["resets_at"]))
        break
now = int(time.time())
if any(s in err.lower() for s in ("401", "unauthorized", "invalid_grant", "refresh token")):
    print(f"fatal 0 {err}"); sys.exit()
limit_text = any(s in err.lower() for s in ("limit", "quota", "429", "credits"))
if resets or limit_text:
    target = max(resets) if resets else now + 900
    wait = max(60, min(target - now + 120, 1800))   # re-check at least every 30 min
    print(f"limit {wait} {err}")
else:
    print(f"error 600 {err}")
'''


class PausingCodex(Codex):
    @staticmethod
    def name() -> str:
        return "codex-pausing"

    async def exec_as_agent(self, environment, command, env=None, cwd=None, timeout_sec=None):
        if "codex exec " in command and " --json " in command and "resume --last" not in command:
            command = self._wrap(command)
        return await super().exec_as_agent(environment, command, env=env, cwd=cwd, timeout_sec=timeout_sec)

    @staticmethod
    def _wrap(command: str) -> str:
        head, sep, rest = command.partition(" -- ")
        if not sep:
            return command
        timed = 'timeout --signal=INT --kill-after=60 "$REM" codex exec '
        first = head.replace("codex exec ", timed, 1) + " -- " + rest
        resume = (head.replace("codex exec ", timed + "resume --last ", 1)
                  + ' -- "$MSG" 2>&1 </dev/null | tee -a /logs/agent/codex.txt')
        note = shlex.quote(RESUME_NOTE.replace("{remaining}", "__REM__"))
        return f"""
cat > /tmp/pause_status.py <<'PYEOF'
{STATUS_PY}
PYEOF
LOG=/logs/agent/pause-log.txt
log() {{ echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$LOG" >&2; }}
BUDGET={HARD_SEC}; STATED={STATED_SEC}; USED=0; SEG=0; ERRORS=0; WAITED=0
while :; do
  REM=$((BUDGET - USED))
  if [ "$REM" -le 30 ]; then log "time budget used up (active $USED s)"; break; fi
  OFF=$(stat -c %s /logs/agent/codex.txt 2>/dev/null || echo 0)
  SEG=$((SEG + 1)); T0=$(date +%s)
  if [ "$SEG" -eq 1 ]; then
    log "segment 1 start (stated budget $STATED s, hard stop $BUDGET s active)"
    {first}
  else
    LEFT=$((STATED - USED)); [ "$LEFT" -lt 0 ] && LEFT=0
    MSG=$(echo {note} | sed "s/__REM__/$LEFT/")
    log "segment $SEG resume (remaining $REM s)"
    {resume}
  fi
  T1=$(date +%s); USED=$((USED + T1 - T0))
  STATUS=$(python3 /tmp/pause_status.py "$OFF")
  KIND=${{STATUS%% *}}; REST=${{STATUS#* }}; WAIT=${{REST%% *}}
  log "segment $SEG ended after $((T1 - T0)) s (active total $USED s): $STATUS"
  case "$KIND" in
    done) break ;;
    fatal) log "authentication failed - stopping (re-run codex login on the host)"; break ;;
    limit) ;;
    error) ERRORS=$((ERRORS + 1)); if [ "$ERRORS" -gt 5 ]; then log "too many non-limit errors, stopping"; break; fi ;;
  esac
  if [ "$WAITED" -gt 259200 ]; then log "waited over 72 h in total, stopping"; break; fi
  log "waiting $WAIT s before resuming"
  sleep "$WAIT"; WAITED=$((WAITED + WAIT))
done
log "finished: $SEG segment(s), active $USED s, waited $WAITED s"
"""
