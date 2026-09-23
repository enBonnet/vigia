#!/usr/bin/env bash
# VigIA reporter for Claude Code hooks.
# Reads the hook JSON from stdin and forwards the state to vigiad over D-Bus.
# Best-effort by design: never fails, never blocks longer than ~3s.

EVENT_JSON=$(cat 2>/dev/null) || exit 0
[ -z "${EVENT_JSON}" ] && exit 0

FIELDS=$(printf '%s' "${EVENT_JSON}" | timeout 2 /usr/bin/python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
ev = d.get("hook_event_name", "")
m = {
    "SessionStart": "idle",
    "UserPromptSubmit": "busy",
    "PreToolUse": "busy",
    "PostToolUse": "busy",
    "PermissionRequest": "question",
    "Notification": "question",
    "Stop": "done",
    "SessionEnd": "gone",
}
state = m.get(ev)
if not state:
    sys.exit(0)
fields = [state, str(d.get("session_id") or ""), str(d.get("cwd") or ""), str(d.get("message") or "")]
print("\x1f".join(fields))
' 2>/dev/null) || exit 0
[ -z "${FIELDS}" ] && exit 0

IFS=$'\x1f' read -r STATE SID CWD MSG <<< "${FIELDS}"
[ -z "${STATE}" ] && exit 0
[ -z "${SID}" ] && SID="unknown-$$"
TITLE=$(basename "${CWD:-unknown}" 2>/dev/null || echo unknown)

# remember the claude CLI process (this script's parent / grandparent) so
# vigiad can interrupt it on battery-critical emergency and detect a dead
# session. Join only the non-empty ones (a bare "0" would be parsed as a
# real PID by the daemon — and os.kill(0) would signal a whole process group)
P1=$(ps -o ppid= -p $$ 2>/dev/null | tr -d ' ')
P2=$(ps -o ppid= -p "${P1:-0}" 2>/dev/null | tr -d ' ')
PIDS="${P1:-}"
[ -n "${P2}" ] && PIDS="${PIDS:+${PIDS},}${P2}"

timeout 3 /usr/bin/busctl --user call org.vigia.Watcher /org/vigia/Watcher \
  org.vigia.Watcher Report ssssss claude "${SID}" "${STATE}" "${TITLE}" "${MSG}" "${PIDS}" \
  >/dev/null 2>&1

exit 0
