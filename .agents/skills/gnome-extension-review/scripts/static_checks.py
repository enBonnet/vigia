#!/usr/bin/env python3
"""Static checks for GNOME Shell (45+) extension code review.

Produces CANDIDATE findings for a human/agent reviewer to verify in context —
grep heuristics cannot see cross-file cleanup or runtime behavior.

Usage:
    python3 static_checks.py <extension-dir> [--json]

Exit codes: 0 = no blocking candidates, 1 = blocking candidates found,
2 = usage/IO error.

Scope: official EGO review guidelines + gjs.guide best practices (see
references/ in this skill). Rules referenced below are R1-R27.
"""

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

JS_EXTS = (".js", ".mjs")
SKIP_DIRS = {"node_modules", ".git", "locale", "build", "dist", "meson-subprojects"}
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
RE_SOURCE_REMOVE = re.compile(r"\bGLib\.Source\.remove\s*\(")
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
    or both (shared)."""
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
    return contexts


def scan_js_file(root, rel, context, findings, hints):
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


def check_metadata(root, findings):
    path = os.path.join(root, "metadata.json")
    if not os.path.isfile(path):
        findings.append(Finding("blocker", "R17", "metadata.json missing"))
        return
    try:
        meta = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError) as e:
        findings.append(Finding("blocker", "R17", f"metadata.json does not parse: {e}"))
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


def check_schemas(root, findings):
    sdir = os.path.join(root, "schemas")
    if not os.path.isdir(sdir):
        return
    compiled = os.path.join(sdir, "gschemas.compiled")
    if not os.path.isfile(compiled):
        findings.append(Finding("info", "R19", "schemas/gschemas.compiled missing (expected in the distributed ZIP; "
                                "normal for a source tree)"))
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
            findings.append(Finding("info", "R25", f"translation source shipped: {rel} (.po/.pot should stay out of the ZIP)"))
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

    if not js_files:
        findings.append(Finding("blocker", "R15", f"no JavaScript files found under {root}"))
    contexts = classify_contexts(root, js_files) if js_files else {}

    for rel in js_files:
        scan_js_file(root, rel, contexts.get(rel, "shell"), findings, hints)

    check_metadata(root, findings)
    check_schemas(root, findings)
    check_entry_points(root, js_files, findings)
    check_misc_files(root, other_files, findings)

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
