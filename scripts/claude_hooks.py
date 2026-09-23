#!/usr/bin/env python3
"""
Merge/unmerge VigIA hooks into Claude Code's ~/.claude/settings.json.

Strictly additive: existing entries (e.g. Orca's hooks) are never touched.
A timestamped backup is written on first merge; writes are atomic.
"""

import json
import os
import shutil
import sys
import tempfile
import time

SETTINGS = os.path.expanduser("~/.claude/settings.json")
HOOK_CMD = os.path.expanduser("~/.local/bin/vigia-claude.sh")
MARKER = "vigia-claude.sh"
EVENTS = [
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PermissionRequest",
    "Notification",
    "Stop",
    "SessionEnd",
]


def load():
    if not os.path.exists(SETTINGS):
        return {}
    with open(SETTINGS, encoding="utf-8") as f:
        return json.load(f)


def save(data):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    backup = f"{SETTINGS}.vigia.bak"
    if not os.path.exists(backup):
        shutil.copy2(SETTINGS, backup)
        print(f"  backup: {backup}")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(SETTINGS), prefix=".settings.json.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, SETTINGS)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _commands(entries):
    for grp in entries if isinstance(entries, list) else []:
        if not isinstance(grp, dict):
            continue
        for h in grp.get("hooks", []) if isinstance(grp.get("hooks"), list) else []:
            if isinstance(h, dict):
                yield str(h.get("command", ""))


def has_vigia_hook(entries):
    return any(MARKER in cmd for cmd in _commands(entries))


def merge():
    data = load()
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        sys.exit("settings.json 'hooks' has an unexpected shape; aborting without changes")
    added = []
    for ev in EVENTS:
        entries = hooks.setdefault(ev, [])
        if not isinstance(entries, list):
            print(f"  skip {ev}: unexpected shape")
            continue
        if has_vigia_hook(entries):
            continue
        entries.append(
            {
                "matcher": "*",
                "hooks": [
                    {"type": "command", "command": HOOK_CMD, "timeout": 10}
                ],
            }
        )
        added.append(ev)
    save(data)
    print(f"  added: {', '.join(added) if added else 'none (already present)'}")


def unmerge():
    if not os.path.exists(SETTINGS):
        print("  no settings file, nothing to do")
        return
    data = load()
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        print("  no hooks present")
        return
    removed = set()
    for ev in list(hooks.keys()):
        entries = hooks[ev]
        if not isinstance(entries, list):
            continue
        kept = []
        for grp in entries:
            if isinstance(grp, dict) and any(
                MARKER in cmd for cmd in _commands([grp])
            ):
                removed.add(ev)
                continue
            kept.append(grp)
        if kept:
            hooks[ev] = kept
        else:
            del hooks[ev]
    if removed:
        save(data)
    print(f"  removed: {', '.join(sorted(removed)) if removed else 'none'}")


def status():
    data = load()
    hooks = data.get("hooks", {}) if isinstance(data, dict) else {}
    for ev in EVENTS:
        entries = hooks.get(ev, []) if isinstance(hooks, dict) else []
        mark = "vigia ✓" if has_vigia_hook(entries) else "-"
        print(f"  {ev:20} {mark}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "merge":
        merge()
    elif cmd == "unmerge":
        unmerge()
    else:
        status()
