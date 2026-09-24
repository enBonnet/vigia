---
name: gnome-extension-review
description: >
  Review and audit GNOME Shell extensions (GJS) against the official
  extensions.gnome.org (EGO) review guidelines and gjs.guide best practices.
  Use whenever the user asks to review, audit, check, rate, or find problems in
  a GNOME extension, shell extension, extension.js, prefs.js, or metadata.json;
  when preparing an extension for extensions.gnome.org submission or EGO
  review; when hunting memory leaks, missing disable() cleanup, forbidden
  imports (Gtk in shell, St in prefs), orphaned signals/timeouts, or lifecycle
  violations in GJS code; and also when asked to fix or refactor GNOME
  extension code, even if the word "review" is not used.
---

# GNOME Shell Extension Review

Audit existing extension code against the official EGO review guidelines.
Scope: GNOME Shell **45+** (ESModules, `Extension` class). Pre-45 code: note it
is out of scope and point to the legacy docs; do not review it as-is.

Authoritative sources (all bundled in `references/`, plus live-verification
commands):

- `references/review-guidelines.md` — EGO review rules (R1–R27, severity-tagged)
- `references/best-practices.md` — official anti-pattern benchmark for AI code
- `references/lifecycle-audit.md` — enable/disable symmetry audit method
- `references/shell-ui-map.md` — js/ui module map + live API verification
- `references/metadata-and-schemas.md` — metadata.json / gschema / packaging

## Severity model

- **🔴 Blocking** — violates a MUST/MUST NOT rule → EGO rejection risk
- **🟡 Should fix** — SHOULD rules, case-by-case risks, anti-patterns
- **🟢 Nice-to-have** — recommendations (lint, HIG, file hygiene)

## Workflow

### 0. Identify the code and target version

Ask (or infer from code/metadata) which GNOME Shell version(s) the code
targets. Everything below assumes 45+; if `metadata.json` lists older versions
or the code uses `init()`/`imports.*`, flag the legacy pattern first and confirm
before reviewing against 45+ rules.

### 1. Discover the extension layout

Locate `extension.js`, `prefs.js`, `metadata.json`, `schemas/`, and JS modules
(recursive; ignore `node_modules/`, `.git/`, `locale/`, build dirs). If no
extension is present at the given path, report that and stop.

### 2. Run the static scanner (candidate findings)

```sh
python3 <skill-dir>/scripts/static_checks.py <extension-dir>
# add --json for machine-readable output; exit code 1 = blockers found
```

The scanner greps for rule violations: forbidden cross-process imports,
deprecated modules, metadata.json problems, schema problems, connect/disconnect
and timeout creation/removal count mismatches, `run_dispose`, lifecycle flags,
overlong lines, binaries, AI notice, and more.

**These are candidates, not verdicts.** For each finding, read the surrounding
code and confirm or reject it (e.g. a `connect()` may be cleaned up through a
helper class; a count mismatch may have a legitimate explanation). Never report
a finding you have not verified in context. Also inspect what greps cannot see:
constructor-time GObjects, `Main.*` mutations, partial `disable()` cleanup.

### 3. Lifecycle audit (the core)

Follow `references/lifecycle-audit.md` §2: inventory every resource created in
imports/`constructor()`/`enable()` (signals, sources, widgets, Shell
registrations, injections, settings bindings, D-Bus) and match each one to a
release in `disable()`/`destroy()`. Check:

- constructor is static-only (R1)
- `enable()`/`disable()` are adjacent and symmetric
- destroy order: sources → signals → refs → `super.destroy()` last
- timeout removal immediately before re-creation
- per-class cleanup ownership (no spaghetti cleanup)
- no `_destroyed`/`_enabled` flags, no defensive try-catch/`?.()` around
  guaranteed cleanup calls (best practices)
- `unlock-dialog` checklist if session-modes includes it

### 4. Verify uncertain APIs against live docs

Never trust memory for version-sensitive APIs. Use the commands in
`references/shell-ui-map.md`:

- gnome-shell raw source (pin with `ref=<tag>` for the target version) to
  confirm a `js/ui` API/property exists and how it is used upstream
- gjs-docs JSON API (`docs/<slug>~<version>/index.json` + `db.json`) for
  GLib/Gio/St/... signatures
- the gjs.guide porting guide for the target version when code claims support
  across versions

### 5. Check metadata, schemas, packaging, legal

Per `references/metadata-and-schemas.md`: uuid, shell-version plausibility,
session-modes, donations keys, settings-schema convention, gschema
id/path/filename, unnecessary files, binaries, licensing (GPL-compatible),
attribution, CoC/political/trademark content.

If the code carries the AI-generation notice ("Generated with AI..."), keep it
when handing the code back, and remind the author it must be removed before EGO
upload (R16 / best practices).

## Report format

Always finish with a report using exactly this structure:

```markdown
# Extension Review: <name or uuid>

Target: GNOME Shell <version(s)> · Files reviewed: <n>

## Verdict
🟡 Not ready for EGO submission / 🔴 Would be rejected / 🟢 Ready
One-paragraph summary.

## 🔴 Blocking
- **<Rule id> — <short title>** `path/file.js:LINE`
  What was found (verified in context).
  ```js
  // fix snippet or corrected code
  ```

## 🟡 Should fix
(same format)

## 🟢 Nice-to-have
(same format)

## Verified clean
Areas explicitly checked and found correct (lifecycle table summary,
metadata, imports) — so the user knows the coverage.
```

Keep every finding tied to a rule id from `references/review-guidelines.md`
(R1–R27) or an explicit "best-practices" tag. Include file:line for each. If
something is suspected but could not be verified (e.g. runtime-only leak), list
it under Should fix with the uncertainty stated.

## Handling fixes requested by the user

When the user asks to fix findings: apply minimal changes, keep the existing
code style, re-run the scanner after fixing, and re-verify the touched cleanup
paths. Do not restructure unrelated code.
