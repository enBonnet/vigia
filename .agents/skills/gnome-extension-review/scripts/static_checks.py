#!/usr/bin/env python3
"""Static checks for GNOME Shell (45+) extension code review.

Produces CANDIDATE findings for a human/agent reviewer to verify in context —
grep heuristics cannot see cross-file cleanup or runtime behavior.

Usage:
    python3 static_checks.py <extension-dir> [--json]

Exit codes: 0 = no blocking candidates, 1 = blocking candidates found,
2 = usage/IO error.

Scope: official EGO review guidelines + gjs.guide best practices (see
references/ in this skill). Rules referenced below are R1-R35 plus the
version-compatibility rules C49-*/C50*. Rule semantics are cross-referenced
with EGO's Shexli static analyzer (EGO001-EGO037, EGO-C49-*, EGO-C50-*); see
references/review-guidelines.md for the mapping table.
"""

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

JS_EXTS = (".js", ".mjs")
SKIP_DIRS = {"node_modules", ".git", "locale", "build", "dist",
             "meson-subprojects", "__pycache__"}
BIN_EXTS = {".so", ".o", ".a", ".bin", ".exe", ".dll", ".pyc", ".wasm", ".node"}

SHELL_ONLY_GI = {"Gtk", "Gdk", "Adw"}          # R6: never in the shell process
PREFS_ONLY_GI = {"St", "Clutter", "Meta", "Shell"}  # R7: never in the prefs process
DEPRECATED_GI = {"Gtk"}  # nothing deprecated via gi://; legacy handled below

UUID_RE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$")
SESSION_MODES = {"user", "unlock-dialog", "gdm"}
DONATION_KEYS = {"buymeacoffee", "custom", "github", "kofi", "liberapay",
                 "opencollective", "patreon", "paypal"}

RE_GI_IMPORT = re.compile(r"gi://([A-Za-z]+)")
RE_REL_IMPORT = re.compile(r"""['"](\.{1,2}/[^'"]+?)['"]""")
RE_SHELL_RESOURCE = re.compile(r"resource:///org/gnome/shell/")
RE_LEGACY_IMPORTS = re.compile(r"imports\.(lang|mainloop|byteArray)\b", re.I)
RE_LEGACY_BIND = re.compile(r"\bLang\.bind\s*\(")
RE_MAINLOOP = re.compile(r"\bMainloop\.(timeout_add|idle_add|source_remove)")
RE_BYTEARRAY = re.compile(r"\bByteArray\.")
RE_RUN_DISPOSE = re.compile(r"\.run_dispose\s*\(")
RE_CONNECT = re.compile(r"\.connect\s*\(")
RE_DISCONNECT = re.compile(r"\.disconnect\s*\(")
RE_TIMEOUT_ADD = re.compile(r"\bGLib\.timeout_add(?:_seconds)?\s*\(")
RE_IDLE_ADD = re.compile(r"\bGLib\.idle_add\s*\(")
RE_SOURCE_REMOVE = re.compile(r"\bGLib\.(?:Source\.remove|source_remove)\s*\(")
RE_SET_TIMEOUT = re.compile(r"\bsetTimeout\s*\(")
RE_SET_INTERVAL = re.compile(r"\bsetInterval\s*\(")
RE_CLEAR_TIMEOUT = re.compile(r"\bclearTimeout\s*\(")
RE_CLEAR_INTERVAL = re.compile(r"\bclearInterval\s*\(")
RE_DEFAULT_EXPORT = re.compile(r"export\s+default\s+class\s+(\w+)\s+extends\s+(\w+)")
RE_EMPTY_LIFECYCLE = re.compile(r"\b(enable|disable)\s*\(\s*\)\s*\{\s*\}")
RE_OPT_CALL = re.compile(r"\?\.\s*\w+\s*\(")
RE_LIFECYCLE_FLAG = re.compile(r"\bthis\._(destroyed|enabled|disposed)\b")
RE_PROTO_PATCH = re.compile(r"\.prototype\s*\.\s*\w+\s*=")
RE_INJECT = re.compile(r"\.overrideMethod\s*\(")
RE_INJECT_CLEAR = re.compile(r"\.(restoreMethod|clear)\s*\(")
RE_GETSETTINGS_ARG = re.compile(r"\.getSettings\s*\(\s*['\"]")
RE_EMOJI = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
RE_CONSOLE_LOG = re.compile(r"\bconsole\.(log|debug|info)\s*\(")
RE_PRINT = re.compile(r"(?<![.\w])print\s*\(")

# Shexli-parity checks (R13/R14/R28-R35, C49/C50) — rule text and the full
# numeric-id cross-reference live in references/review-guidelines.md.
RE_SYNC_FILE_IO = re.compile(
    r"\bGLib\.file_get_contents\s*\("
    r"|\bGLib\.file_set_contents\s*\("
    r"|\.(?:load_contents|replace_contents)\s*\(")
RE_SYNC_SUBPROCESS = re.compile(
    r"\bGLib\.spawn_sync\s*\("
    r"|\bGLib\.spawn_command_line_sync\s*\("
    r"|\.(?:communicate|communicate_utf8|communicate_sync|communicate_utf8_sync)\s*\("
    r"|\.(?:wait_sync|wait_check_sync)\s*\(")
RE_CLIPBOARD = re.compile(r"\bSt\.Clipboard(?:Type)?\b")
RE_PRIVILEGED_SPAWN = re.compile(r"\b(?:pkexec|gksu|gksudo|sudo)\b")
RE_SPAWNISH = re.compile(r"spawn|Subprocess|pkexec|sudo|exec", re.I)
RE_IMPORTS_GI = re.compile(r"imports\._gi\b")
RE_GET_PREFS_WIDGET = re.compile(r"\bgetPreferencesWidget\s*\(")
RE_LOOKUP_HELPER = re.compile(r"\.\s*(?:lookupByURL|lookupByUUID)\s*\(")
RE_STYLESHEET = re.compile(r"\b(?:load_stylesheet|unload_stylesheet)\s*\(")
RE_CLOSE_REQUEST = re.compile(r"['\"]close-request['\"]")
RE_PREFS_FIELD_STORE = re.compile(r"\bthis\._\w+\s*=")
RE_SOUP_SESSION = re.compile(r"\bnew\s+Soup\.Session\b")
RE_SOUP_ABORT = re.compile(r"\.abort\s*\(")
# GNOME 49 removals (C49-*)
RE_DND_SWITCH = re.compile(r"\bDoNotDisturbSwitch\b")
RE_CLUTTER_ACTIONS = re.compile(r"\bClutter\.(?:ClickAction|TapAction)\b")
RE_GET_MAXIMIZED = re.compile(r"\.get_maximized\s*\(")
RE_SET_POINTER_VISIBLE = re.compile(r"\.set_pointer_visible\s*\(")
# GNOME 50 removals (C50-*)
RE_DISPLAY_RESTART_SIGNAL = re.compile(
    r"global\.display\.(?:connect|disconnect|emit)\s*\(\s*['\"]"
    r"(?:restart|show-restart-message)['\"]")
RE_RUN_DIALOG_RESTART = re.compile(r"\._restart\s*\(")
# R18/EGO-M-008: unlock-dialog disable() comment placement
RE_DISABLE_DEF = re.compile(r"(?<![\w.$])disable\s*\(\s*\)\s*\{")

SEV_ORDER = {"blocker": 0, "warning": 1, "info": 2}


class Finding:
    def __init__(self, severity, rule, message, file=None, line=None):
        self.severity = severity
        self.rule = rule
        self.message = message
        self.file = file
        self.line = line

    def as_dict(self):
        return {"severity": self.severity, "rule": self.rule,
                "message": self.message, "file": self.file, "line": self.line}


def load_files(root):
    js_files, other_files = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            ext = os.path.splitext(name)[1].lower()
            if ext in JS_EXTS:
                js_files.append(rel)
            else:
                other_files.append(rel)
    return js_files, other_files


def read_text(root, rel):
    with open(os.path.join(root, rel), "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def normalize(root, base_rel, spec):
    base_dir = os.path.dirname(os.path.join(root, base_rel))
    resolved = os.path.normpath(os.path.join(base_dir, spec))
    return os.path.relpath(resolved, root)


def classify_contexts(root, js_files):
    """BFS the relative-import graph from entry points to find modules
    reachable from prefs.js (prefs process), extension.js (shell process),
    or both (shared). Also returns the set of reachable files so callers can
    flag unreachable modules (Shexli EGO026)."""
    imports = {}
    for rel in js_files:
        text = read_text(root, rel)
        imports[rel] = {normalize(root, rel, s) for s in RE_REL_IMPORT.findall(text)}

    def reachable(entry):
        seen, stack = set(), [entry]
        while stack:
            cur = stack.pop()
            if cur in seen or cur not in imports:
                continue
            seen.add(cur)
            stack.extend(imports[cur])
        return seen

    ext_entry = "extension.js" if "extension.js" in js_files else None
    prefs_entry = "prefs.js" if "prefs.js" in js_files else None
    shell_reach = reachable(ext_entry) if ext_entry else set()
    prefs_reach = reachable(prefs_entry) if prefs_entry else set()

    contexts = {}
    for rel in js_files:
        if rel in shell_reach and rel in prefs_reach:
            contexts[rel] = "shared"
        elif rel in prefs_reach:
            contexts[rel] = "prefs"
        else:
            contexts[rel] = "shell"
    return contexts, shell_reach | prefs_reach


def load_metadata(root):
    path = os.path.join(root, "metadata.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def shell_targets(meta):
    """Major versions claimed in metadata shell-version, as a set of ints."""
    out = set()
    if not meta:
        return out
    sv = meta.get("shell-version") or []
    for v in sv if isinstance(sv, list) else []:
        m = re.match(r"^(\d+)", str(v))
        if m:
            out.add(int(m.group(1)))
    return out


def scan_js_file(root, rel, context, findings, hints, targets=None):
    text = read_text(root, rel)
    lines = text.splitlines()
    gi_libs = set(RE_GI_IMPORT.findall(text))

    def add(sev, rule, line_no, msg):
        findings.append(Finding(sev, rule, msg, rel, line_no + 1))

    # R5: deprecated modules / legacy import machinery
    for i, ln in enumerate(lines):
        if RE_LEGACY_IMPORTS.search(ln) or RE_LEGACY_BIND.search(ln) or RE_MAINLOOP.search(ln) or RE_BYTEARRAY.search(ln):
            if not any(f.file == rel and f.line == i + 1 and f.rule == "R5" for f in findings):
                add("blocker", "R5", i, "Legacy/deprecated module usage "
                    "(imports.lang/mainloop/byteArray, Lang.bind, Mainloop, ByteArray)")

    # R6/R7 + cross-process resource imports by context (per-line, real lines)
    for i, ln in enumerate(lines):
        bad = set(RE_GI_IMPORT.findall(ln)) & (
            SHELL_ONLY_GI if context == "shell" else
            PREFS_ONLY_GI if context == "prefs" else
            SHELL_ONLY_GI | PREFS_ONLY_GI if context == "shared" else set())
        if bad:
            rule = "R6" if (context == "shell" or (context == "shared" and bad & SHELL_ONLY_GI)) else "R7"
            add("blocker", rule, i, "Forbidden import in this process: " + ", ".join(sorted(bad)))
        if context in ("prefs", "shared") and RE_SHELL_RESOURCE.search(ln):
            add("blocker", "R7", i, "GNOME Shell resource import in prefs/shared module")

    # R11: run_dispose without an explanatory comment on the same line
    for i, ln in enumerate(lines):
        if RE_RUN_DISPOSE.search(ln) and "//" not in ln:
            add("warning", "R11", i, "run_dispose() without a justifying comment on the same line")

    # Best practices: empty lifecycle methods, defensive patterns, flags
    for i, ln in enumerate(lines):
        m = RE_EMPTY_LIFECYCLE.search(ln)
        if m:
            add("info", "best-practices", i, f"Empty {m.group(1)}() body (placeholder extension?)")
        if RE_OPT_CALL.search(ln):
            add("info", "best-practices", i, "Optional call `?.(` on a guaranteed API (anti-pattern)")
        if RE_LIFECYCLE_FLAG.search(ln):
            add("info", "best-practices", i, "Boolean lifecycle flag `_destroyed/_enabled` (anti-pattern)")
            for j in range(i + 1, len(lines)):
                if RE_LIFECYCLE_FLAG.search(lines[j]):
                    lines[j] = ""  # dedupe per file
        if RE_GETSETTINGS_ARG.search(ln):
            add("info", "best-practices", i, "getSettings(<schema-id>) — prefer settings-schema in metadata.json + getSettings()")
        if RE_PROTO_PATCH.search(ln):
            add("info", "lifecycle", i, "Prototype patching — must be restored in disable() (verify manually)")
        if len(ln) > 200:
            add("info", "best-practices", i, f"Line longer than 200 chars ({len(ln)})")
        if RE_EMOJI.search(ln):
            add("info", "best-practices", i, "Possible emoji in code — emojis must not be used as UI icons")

    # Shexli-parity checks: blocking IO, clipboard, prefs API, compat rules.
    # Candidates only — every hit needs the manual checklist from
    # references/review-guidelines.md before it goes in a report.
    for i, ln in enumerate(lines):
        m = RE_SYNC_FILE_IO.search(ln)
        if m:
            sev = "info" if context == "prefs" else "warning"
            add(sev, "R28", i, f"synchronous file IO `{m.group(0)[:-1].strip()}` blocks the shell "
                "main loop — use async Gio.File APIs "
                "(load_contents_async / replace_contents_bytes_async; EGO030)")
        m = RE_SYNC_SUBPROCESS.search(ln)
        if m:
            sev = "info" if context == "prefs" else "warning"
            add(sev, "R29", i, "synchronous subprocess call blocks the main loop — use "
                "Gio.Subprocess with the async/Promise APIs (EGO028)")
        if RE_CLIPBOARD.search(ln):
            add("warning", "R13", i, "direct clipboard access — reviewer scrutiny: description must "
                "declare it, no third-party sharing without explicit user action, "
                "no default shortcuts (EGO-A-005)")
        if RE_PRIVILEGED_SPAWN.search(ln) and RE_SPAWNISH.search(ln):
            sev = "info" if "pkexec" in ln else "warning"
            add(sev, "R14", i, "privileged subprocess — must run via pkexec and target a "
                "non-user-writable executable (EGO024)")
        if RE_IMPORTS_GI.search(ln):
            add("warning", "R30", i, "imports._gi used directly — use supported APIs such as "
                "InjectionManager (EGO031)")
        if context in ("prefs", "shared") and RE_GET_PREFS_WIDGET.search(ln):
            add("warning", "R31", i, "getPreferencesWidget() in 45+ prefs — use fillPreferencesWindow() "
                "(EGO032/EGO-C45-001)")
        if RE_LOOKUP_HELPER.search(ln):
            add("warning", "R32", i, "lookupByURL/lookupByUUID for the current extension — use `this`, "
                "`this.getSettings()` or `this.path` (EGO036)")
        if RE_STYLESHEET.search(ln):
            add("info", "R33", i, "manual stylesheet load/unload — Shell loads the packaged "
                "stylesheet.css automatically (EGO034)")

    if context == "prefs" and RE_PREFS_FIELD_STORE.search(text) \
            and not RE_CLOSE_REQUEST.search(text):
        add("info", "R34", 0, "prefs stores objects on instance fields without `close-request` "
            "cleanup in this file — verify window-scoped objects are released (EGO033)")

    n_soup = len(RE_SOUP_SESSION.findall(text))
    n_abort = len(RE_SOUP_ABORT.findall(text))
    if n_soup > n_abort:
        add("warning", "R35", 0, f"{n_soup} Soup.Session created vs {n_abort} .abort( — sessions "
            "should be aborted during cleanup (EGO037)")

    # Version-gated compatibility checks (GNOME 49 / 50 removals)
    if targets:
        for i, ln in enumerate(lines):
            if 49 in targets:
                if RE_DND_SWITCH.search(ln):
                    add("blocker", "C49-1", i, "DoNotDisturbSwitch (calendar.js) removed in GNOME 49 "
                        "(EGO-C49-001)")
                if RE_CLUTTER_ACTIONS.search(ln):
                    add("blocker", "C49-2", i, "Clutter.ClickAction/TapAction removed in GNOME 49 "
                        "(EGO-C49-002)")
                if re.search(r"\.(?:maximize|unmaximize)\s*\(", ln) \
                        and "Meta.MaximizeFlags" in ln:
                    add("blocker", "C49-3", i, "maximize/unmaximize no longer take Meta.MaximizeFlags "
                        "in GNOME 49 (EGO-C49-003)")
                if RE_GET_MAXIMIZED.search(ln):
                    add("blocker", "C49-4", i, "Meta.Window.get_maximized() removed in GNOME 49 "
                        "(EGO-C49-004)")
                if RE_SET_POINTER_VISIBLE.search(ln):
                    add("blocker", "C49-5", i, "Meta.CursorTracker.set_pointer_visible() removed in "
                        "GNOME 49 (EGO-C49-005)")
            if 50 in targets:
                if RE_DISPLAY_RESTART_SIGNAL.search(ln):
                    add("blocker", "C50-1", i, "global.display restart signals are no longer emitted "
                        "in GNOME 50 (EGO-C50-001)")
                if RE_RUN_DIALOG_RESTART.search(ln):
                    add("blocker", "C50-2", i, "RunDialog._restart() removed in GNOME 50 (EGO-C50-002)")

    if RE_INJECT.search(text) and not RE_INJECT_CLEAR.search(text):
        add("warning", "lifecycle", 0, "InjectionManager.overrideMethod without restoreMethod/clear in this file")

    # Count-mismatch heuristics (hints need manual verification)
    n_connect = len(RE_CONNECT.findall(text))
    n_disconnect = len(RE_DISCONNECT.findall(text))
    if n_connect > n_disconnect:
        add("warning", "R3", 0, f"{n_connect} .connect( vs {n_disconnect} .disconnect( — verify cleanup in disable()")
    src_created = len(RE_TIMEOUT_ADD.findall(text)) + len(RE_IDLE_ADD.findall(text))
    src_removed = len(RE_SOURCE_REMOVE.findall(text))
    if src_created > src_removed:
        add("warning", "R4", 0, f"{src_created} GLib timeout/idle sources created vs {src_removed} Source.remove — "
            "sources must be removed in disable() even if callbacks self-cancel")
    n_st = len(RE_SET_TIMEOUT.findall(text)) + len(RE_SET_INTERVAL.findall(text))
    n_ct = len(RE_CLEAR_TIMEOUT.findall(text)) + len(RE_CLEAR_INTERVAL.findall(text))
    if n_st > n_ct:
        add("warning", "R4", 0, f"{n_st} setTimeout/setInterval vs {n_ct} clearTimeout/clearInterval — verify cleanup")

    if RE_CONSOLE_LOG.search(text):
        add("info", "R10", 0, "console.log/debug/info present — keep logging minimal")
    if RE_PRINT.search(text):
        add("warning", "R10", 0, "print() used — prefer console/logger discipline")

    if "Generated with AI" in text:
        add("info", "R16", 0, "AI-generation notice present — author must remove before EGO upload")

    # Aggregate hints for the lifecycle audit
    for name, rx in (("new St.*", re.compile(r"\bnew\s+St\.\w+")),
                     ("new Gio/Soup/DBus", re.compile(r"\bnew\s+(?:Gio|Soup)\.\w+")),
                     ("Main.* mutation/registration", re.compile(r"\bMain\.(panel|layoutManager|uiGroup|sessionMode|messageTray|overview|wm)\b")),
                     ("overrideMethod", RE_INJECT)):
        n = len(rx.findall(text))
        if n:
            hints.setdefault(name, []).append(f"{rel} ({n})")


def check_metadata(root, findings, meta=None):
    if meta is None:
        path = os.path.join(root, "metadata.json")
        if not os.path.isfile(path):
            findings.append(Finding("blocker", "R17", "metadata.json missing"))
        else:
            findings.append(Finding("blocker", "R17", "metadata.json does not parse"))
        return
    uuid = meta.get("uuid")
    if not uuid or not UUID_RE.match(uuid):
        findings.append(Finding("blocker", "R17", "uuid missing or malformed (expected id@namespace)"))
    elif uuid.split("@", 1)[1].rstrip(".").endswith("gnome.org"):
        findings.append(Finding("blocker", "R17", "uuid namespace uses gnome.org (forbidden)"))

    for key in ("name", "description", "shell-version", "url"):
        if not meta.get(key):
            findings.append(Finding("warning" if key == "url" else "blocker", "R17",
                                    f"metadata field '{key}' missing/empty"))
    sv = meta.get("shell-version") or []
    for v in sv if isinstance(sv, list) else []:
        m = re.match(r"^(\d+)", str(v))
        if m and int(m.group(1)) < 45:
            findings.append(Finding("info", "scope", f"shell-version '{v}' predates GNOME 45 (out of this review's scope)"))
        elif m and int(m.group(1)) > 51:
            findings.append(Finding("info", "R17", f"shell-version '{v}' looks like a future release — "
                                    "EGO forbids claiming unreleased versions (verify against current GNOME schedule)"))
    if isinstance(sv, list):
        dev = [str(v) for v in sv if re.search(r"alpha|beta|rc|dev", str(v), re.I)]
        if len(dev) > 1:
            findings.append(Finding("warning", "R17", "more than one development release claimed "
                                    f"({', '.join(dev)}) — at most one allowed"))

    if "version" in meta:
        if not isinstance(meta["version"], int):
            findings.append(Finding("warning", "R17", "metadata 'version' must be a whole number if present"))
        findings.append(Finding("info", "R17", "metadata 'version' is EGO-controlled; developers should not set it"))

    sm = meta.get("session-modes")
    if sm is not None:
        if not isinstance(sm, list) or any(x not in SESSION_MODES for x in sm):
            findings.append(Finding("blocker", "R17/R18", f"invalid session-modes: {sm} (valid: user, unlock-dialog)"))
        elif sm == ["user"]:
            findings.append(Finding("warning", "R17", "session-modes ['user'] should be dropped (user is the default)"))
        if isinstance(sm, list) and "unlock-dialog" in sm:
            findings.append(Finding("info", "R18", "unlock-dialog mode: verify keyboard signals disconnected + "
                                    "justification comment in disable()"))

    don = meta.get("donations")
    if don is not None:
        if not isinstance(don, dict) or any(k not in DONATION_KEYS for k in don):
            findings.append(Finding("warning", "R17", "donations contains unknown keys"))
    schema = meta.get("settings-schema")
    if schema and not schema.startswith("org.gnome.shell.extensions."):
        findings.append(Finding("warning", "R19", "settings-schema should start with org.gnome.shell.extensions"))


def find_disable_bodies(lines):
    """Locate disable() method definitions; return (line_index, body_text).

    Naive brace matching (braces inside strings/comments are not tracked) —
    this stays a candidate generator, verified by the reviewer in context.
    """
    bodies, open_idx, depth = [], None, 0
    for i, ln in enumerate(lines):
        if open_idx is None:
            if RE_DISABLE_DEF.search(ln):
                open_idx = i
                depth = ln.count("{") - ln.count("}")
                if depth <= 0:
                    bodies.append((open_idx, ""))
                    open_idx = None
        else:
            depth += ln.count("{") - ln.count("}")
            if depth <= 0:
                bodies.append((open_idx, "\n".join(lines[open_idx + 1:i])))
                open_idx = None
    return bodies


def _comment_directly_above(lines, idx):
    found, j = False, idx - 1
    while j >= 0:
        s = lines[j].strip()
        if not s:
            break  # blank line ends the attached comment block
        if s.startswith("//") or s.startswith("/*") or s.startswith("*") or s.endswith("*/"):
            found = True
            j -= 1
            continue
        break  # code line above
    return found


def check_unlock_dialog(root, contexts, findings):
    """R18 / EGO-M-008: with 'unlock-dialog' in session-modes, disable() MUST
    contain a comment explaining the unlock-dialog usage — inside the function
    body; a rationale placed only above the method is what EGO's automated
    first-pass review (Shexli) flags."""
    path = os.path.join(root, "metadata.json")
    if not os.path.isfile(path):
        return
    try:
        meta = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return  # parse failure reported by check_metadata
    sm = meta.get("session-modes")
    if not isinstance(sm, list) or "unlock-dialog" not in sm:
        return
    for rel, ctx in sorted(contexts.items()):
        if ctx != "shell":
            continue
        lines = read_text(root, rel).splitlines()
        for idx, body in find_disable_bodies(lines):
            comments = re.findall(r"//[^\n]*|/\*.*?\*/", body, re.S)
            if "unlock" in " ".join(comments).lower():
                continue  # documented in the body
            if comments:
                msg = ("comment found in disable() but it does not mention "
                       "'unlock-dialog' — verify it explains the unlock-dialog "
                       "usage (R18/EGO-M-008)")
            elif _comment_directly_above(lines, idx):
                msg = ("unlock-dialog rationale found ABOVE disable() but not "
                       "inside the body — the comment MUST be inside the "
                       "disable() function; EGO automated review (Shexli "
                       "EGO-M-008) flags above-method placement")
            else:
                msg = ("unlock-dialog session mode: disable() has no comment "
                       "explaining why unlock-dialog is needed — the comment "
                       "MUST be inside the disable() body (EGO automated "
                       "review flags this as EGO-M-008)")
            findings.append(Finding("warning", "R18/EGO-M-008", msg, rel, idx + 1))


def check_schemas(root, findings, targets=None):
    sdir = os.path.join(root, "schemas")
    if not os.path.isdir(sdir):
        return
    compiled = os.path.join(sdir, "gschemas.compiled")
    if os.path.isfile(compiled) and targets and all(t >= 45 for t in targets):
        findings.append(Finding("warning", "R25", "schemas/gschemas.compiled shipped — compiled GSettings schemas "
                                "must not ship for 45+ packages (EGO-P-006, Shexli pre-review); exclude from the "
                                "distributed ZIP and git, keep only as a local build artifact (EGO compiles "
                                "schemas when serving the download)"))
    for name in sorted(os.listdir(sdir)):
        if not name.endswith(".gschema.xml"):
            continue
        path = os.path.join(sdir, name)
        try:
            tree = ET.parse(path)
        except (OSError, ET.ParseError) as e:
            findings.append(Finding("warning", "R19", f"schema {name} does not parse: {e}"))
            continue
        for sch in tree.getroot().iter("schema"):
            sid = sch.get("id", "")
            spath = sch.get("path")
            if not sid.startswith("org.gnome.shell.extensions."):
                findings.append(Finding("blocker", "R19", f"schema id '{sid}' must start with org.gnome.shell.extensions"))
            if spath and not spath.startswith("/org/gnome/shell/extensions/"):
                findings.append(Finding("blocker", "R19", f"schema path '{spath}' must start with /org/gnome/shell/extensions"))
            if sid and name != f"{sid}.gschema.xml":
                findings.append(Finding("warning", "R19", f"schema file '{name}' should be named '{sid}.gschema.xml'"))


def check_entry_points(root, js_files, findings):
    if "extension.js" not in js_files:
        findings.append(Finding("blocker", "R15", "extension.js missing (required)"))
        return
    text = read_text(root, "extension.js")
    m = RE_DEFAULT_EXPORT.search(text)
    if not m:
        findings.append(Finding("blocker", "R15", "extension.js must default-export a class extending Extension"))
    elif m.group(2) not in ("Extension",):
        findings.append(Finding("warning", "R15", f"entry class extends '{m.group(2)}' — expected 'Extension'"))
    if not re.search(r"\benable\s*\(", text) or not re.search(r"\bdisable\s*\(", text):
        findings.append(Finding("blocker", "R15", "Extension must implement enable() and disable()"))


def check_misc_files(root, other_files, findings):
    for rel in other_files:
        if os.path.splitext(rel)[1].lower() in BIN_EXTS:
            findings.append(Finding("blocker", "R12", f"binary file included: {rel}"))
        low = rel.lower()
        if low.endswith((".po", ".pot")):
            findings.append(Finding("info", "R25", f"translation source shipped: {rel} "
                                    "(.po/.pot are unnecessary translation artifacts — EGO-P-006)"))
        if "/locale/" in "/" + rel and not low.endswith(".mo"):
            findings.append(Finding("info", "R25", f"unexpected file in locale/: {rel}"))


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", help="extension directory")
    ap.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args(argv)

    root = args.path
    if not os.path.isdir(root):
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2

    js_files, other_files = load_files(root)
    findings, hints = [], {}

    meta = load_metadata(root)
    targets = shell_targets(meta)

    if not js_files:
        findings.append(Finding("blocker", "R15", f"no JavaScript files found under {root}"))
    contexts, reachable = classify_contexts(root, js_files) if js_files else ({}, set())

    has_entry = "extension.js" in js_files or "prefs.js" in js_files
    for rel in js_files:
        if has_entry and rel not in reachable:
            findings.append(Finding("warning", "R25", f"{rel}: JS module not reachable from "
                                    "extension.js/prefs.js — likely unnecessary file (EGO026)"))

    for rel in js_files:
        scan_js_file(root, rel, contexts.get(rel, "shell"), findings, hints, targets)

    check_metadata(root, findings, meta)
    check_schemas(root, findings, targets)
    check_entry_points(root, js_files, findings)
    check_misc_files(root, other_files, findings)

    # R18/EGO-M-008: unlock-dialog requires a comment inside the disable() body
    check_unlock_dialog(root, contexts, findings)

    # R13/EGO-A-005: clipboard usage must be declared in the metadata description
    if meta and any(f.rule == "R13" and f.file for f in findings):
        desc = meta.get("description") or ""
        if "clipboard" not in desc.lower():
            findings.append(Finding("info", "R13", "St.Clipboard used but the metadata description "
                                    "does not mention clipboard access — it must be declared "
                                    "(R13/EGO-A-005)"))

    findings.sort(key=lambda f: (SEV_ORDER[f.severity], f.rule, f.file or "", f.line or 0))

    if args.json:
        print(json.dumps({
            "path": root,
            "contexts": contexts,
            "findings": [f.as_dict() for f in findings],
            "lifecycle_hints": hints,
        }, indent=2))
    else:
        print(f"Extension: {root}")
        print(f"Modules: {len(js_files)} (contexts: prefs={sum(1 for c in contexts.values() if c == 'prefs')}, "
              f"shared={sum(1 for c in contexts.values() if c == 'shared')}, "
              f"shell={sum(1 for c in contexts.values() if c == 'shell')})")
        print()
        for sev, label in (("blocker", "BLOCKING CANDIDATES (verify)"),
                           ("warning", "WARNINGS (verify)"),
                           ("info", "INFO")):
            fs = [f for f in findings if f.severity == sev]
            print(f"== {label}: {len(fs)}")
            for f in fs:
                loc = f"{f.file}:{f.line}" if f.file else f.file
                print(f"  [{f.rule}] {loc}")
                print(f"      {f.message}")
            print()
        if hints:
            print("== LIFECYCLE AUDIT HINTS (creation sites found)")
            for name, locs in sorted(hints.items()):
                print(f"  {name}: {', '.join(locs)}")
            print()
        print("NOTE: these are heuristic candidates. Verify each in context and")
        print("complete the manual lifecycle audit (references/lifecycle-audit.md).")

    return 1 if any(f.severity == "blocker" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
