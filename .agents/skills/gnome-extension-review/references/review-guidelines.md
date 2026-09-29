# EGO Review Guidelines — Checkable Rules

> Source: [gjs.guide — Review Guidelines](https://gjs.guide/extensions/review-guidelines/review-guidelines.html)
> (GJS Guide, MIT licensed). Curated 2026-09; scoped to GNOME Shell 45+.
> Check the live page when reviewing for a very recent GNOME release.

Severity mapping used in review reports:

- `MUST` / `MUST NOT` rules → **🔴 Blocking** (EGO rejection risk)
- `SHOULD` / case-by-case rules → **🟡 Should fix**
- Recommendations → **🟢 Nice-to-have**

## 1. Lifecycle

### R1 🔴 Initialization must be static-only
Extensions **MUST NOT** create objects, connect signals, add main loop sources,
or modify GNOME Shell during initialization. In GNOME 45+, initialization is the
`extension.js` module import and `Extension` subclass construction (`constructor()`).
Static data structures and built-in JS objects (`RegExp()`, `Map()`) are allowed,
but all dynamically stored memory must be cleared in `disable()` (e.g.
`Map.prototype.clear()`). GObject instances (`Gio.Settings`, `St.Widget`, …) are
**disallowed** at init time.
*Detection hint: any `new St.*`, `new Gio.*`, `connect(`, `timeout_add`, or
`Main.panel` mutation inside `constructor()` is a violation. Gettext init
(`initTranslations()`) is fine in the constructor.*

### R2 🔴 Destroy all objects
Any objects or widgets created by the extension **MUST** be destroyed in
`disable()`. Owned references should also be **released** afterwards
(`this._widget = null;`) — reviewers expect the null-release pattern so stale
state cannot survive re-enable cycles (Shexli EGO027 / EGO-L-005).

### R3 🔴 Disconnect all signals
Any signal connections made by the extension **MUST** be disconnected in
`disable()`.

### R4 🔴 Remove main loop sources
Any main loop sources created **MUST** be removed in `disable()` — even if the
callback would eventually return `GLib.SOURCE_REMOVE` / `false`. In repeatable
code paths the old source **MUST** be removed *immediately before* creating the
new one, so the stored id is never overwritten (Shexli EGO035).

## 2. Forbidden modules and libraries

### R5 🔴 No deprecated modules
| Deprecated | Replacement |
| --- | --- |
| `ByteArray` | `TextDecoder` / `TextEncoder` |
| `Lang` | ES6 classes + `Function.prototype.bind()` |
| `Mainloop` | `GLib.timeout_add()`, `setTimeout()`, etc. |

*Detection hints: `imports.lang`, `imports.mainloop`, `imports.byteArray`,
`const Lang = imports.lang`, `Lang.bind(`.*

### R6 🔴 No GTK libraries in GNOME Shell
`Gdk`, `Gtk`, `Adw` **MUST NOT** be imported in the GNOME Shell process
(`extension.js` and its modules). They conflict with `Clutter`/Shell libraries.

### R7 🔴 No GNOME Shell libraries in Preferences
`Clutter`, `Meta`, `St`, `Shell` **MUST NOT** be imported in the preferences
process (`prefs.js` and its modules). They conflict with `Gtk`/`Adw`.

## 3. Code integrity

### R8 🟡 Don't interfere with the extension system
Extensions that modify, reload, or interact with other extensions or the
extension system are generally discouraged; reviewed case-by-case and may be
rejected.

### R9 🔴 No obfuscated code
Code **MUST** be readable, reviewable JavaScript — never minified or obfuscated.
TypeScript must be transpiled to well-formatted JavaScript. A specific style is
not enforced, but unreadable code may be rejected.

### R10 🔴 No excessive logging
The log is for important messages and errors only. Excessive logging → rejected.

### R11 🔴 No forced GObject disposal
`GObject.Object.run_dispose()` **SHOULD NOT** be called. If absolutely necessary
each call **MUST** have a comment explaining the real-world situation.

### R12 🟡 Scripts and binaries
- **🔴** No binary executables or libraries included.
- Processes **MUST** spawn carefully and exit cleanly.
- Scripts **MUST** be GJS unless truly necessary; script deps **MUST** be
  OSI-approved licensed.
- pip/npm/yarn module installs **MUST** require explicit user action
  (e.g. a prefs button with instructions).

### R13 🔴 Clipboard access rules
- Clipboard access **MUST** be declared in the extension description.
- **MUST NOT** share clipboard data with a third party without explicit user
  interaction (button click, user-defined shortcut).
- **MUST NOT** ship default keyboard shortcuts for clipboard interaction.

*Detection hints: `St.Clipboard.get_default()`, `St.ClipboardType.CLIPBOARD/
PRIMARY`, clipboard `.get_text(` / `.set_text(`.*

Reviewer checklist whenever clipboard access is found (manual review — EGO's
Shexli analyzer flags it as **EGO-A-005**):
1. `metadata.json` description declares clipboard access (read **or** write).
2. Clipboard data is only shared externally after explicit user interaction.
3. No default keyboard shortcuts for clipboard interaction.
4. Nothing reads the clipboard covertly (background polling, monitors).

### R14 🔴 Privileged subprocesses
Spawning privileged subprocesses should be avoided at all costs. If absolutely
necessary: run with `pkexec`, and the executable/script **MUST NOT** be
user-writable.

*Detection hints: `sudo`/`gksu`/`gksudo` in spawn arguments is a red flag;
`pkexec` is the only acceptable wrapper but its target still needs the
non-user-writable check (Shexli EGO024).*

### R15 🟡 Must be functional
Fundamentally broken extensions (or with inoperable prefs windows) are rejected
when tested. Extensions with no real functionality are rejected.

### R16 🔴 AI-generated submissions
Developers must be able to explain their code. Rejection indicators: large
amounts of unnecessary code, inconsistent style, imaginary API usage, LLM-prompt
comments. See `best-practices.md` for the required AI-generated notice and its
removal rule before EGO upload.

## 4. Metadata, session modes, settings

### R17 🔴 metadata.json well-formed
No unnecessary keys. Full field rules → `metadata-and-schemas.md`. Key points:
`uuid` format and no `gnome.org` namespace; `shell-version` only stable releases
plus at most one development release (no future versions); `version` is
EGO-internal (deprecated for developers); `session-modes` dropped when only
`user` is used; `donations` contains only used keys.

### R18 🔴 Session modes
`unlock-dialog` is approved only if: it is necessary for correct operation; all
keyboard-event signals are disconnected during `unlock-dialog`; `disable()` has
a comment explaining the `unlock-dialog` usage — the comment **MUST** be inside
the `disable()` body; a rationale placed only above the method is flagged by
EGO's automated first-pass review (Shexli finding **EGO-M-008**, severity
"warning"). Extensions **MUST NOT** disable selectively.

### R19 🔴 GSettings schemas
Schema ID **MUST** start with `org.gnome.shell.extensions`; path **MUST** start
with `/org/gnome/shell/extensions`; the schema XML **MUST** be inside the ZIP;
filename **MUST** be `<schema-id>.gschema.xml`. The compiled
`schemas/gschemas.compiled` **SHOULD NOT** ship — see R25 / EGO-P-006.

### R20 🔴 No telemetry
No tools that track users or share user data online.

## 5. Legal

### R21 🔴 Code of Conduct
Name, description, icons, emojis, media and screenshots distributed from GNOME
infrastructure **MUST NOT** violate the GNOME CoC.

### R22 🔴 No political statements
No national or international political agenda promotion.

### R23 🔴 Licensing
Extensions are derived works of GNOME Shell (GPL-2.0-or-later) and **MUST** be
distributed under compatible terms. Code taken from other extensions **MUST**
include attribution in the distributed files.

### R24 🔴 Copyrights and trademarks
No copyrighted/trademarked brand names, logos, artwork or media without express
permission.

## 6. Recommendations

### R25 🟡 No unnecessary files
No build/install scripts, `.po`/`.pot` files, unused icons/media, or build
artifacts — notably the compiled GSettings schema (`schemas/gschemas.compiled`).
Excessive extra data may cause rejection. For 45+ targets the compiled schema
**MUST NOT** ship: EGO compiles the schema when serving the download, so ship
only the XML and keep `gschemas.compiled` as a gitignored local build artifact
for source-tree installs. JS modules should be reachable from
`extension.js`/`prefs.js`; unreachable modules are usually dead weight (Shexli
EGO026).

*Enforced by EGO's automated pre-review (Shexli) as **EGO-P-006** —
"unnecessary build and translation artifacts should not be shipped; compiled
GSettings schemas should not be shipped for 45+ packages" (severity: warning;
numeric id EGO025).*

### R26 🟢 Use a linter
ESLint with the [GNOME Shell rules](https://gitlab.gnome.org/GNOME/gnome-shell-extensions/tree/main/lint).

### R27 🟢 UI design
Preferences should follow the [GNOME HIG](https://developer.gnome.org/hig/).

### R28 🟡 Avoid synchronous file IO in shell code
Synchronous file APIs block the GNOME Shell main loop and can freeze the whole
session. Shell-process code **SHOULD** use async `Gio.File` APIs instead.

Avoid in the shell process: `GLib.file_get_contents()`,
`GLib.file_set_contents()`, `Gio.File.load_contents()`,
`Gio.File.replace_contents()` and other non-`_async` file calls.

Replacements ([gjs.guide — File Operations](https://gjs.guide/guides/gio/file-operations.html)):
`file.load_contents_async(null)` (Promise; GJS ≥ 1.72 auto-promisifies
`*_async` methods), `file.replace_contents_bytes_async()`,
`file.delete_async()`, `file.create_async()`. `GLib.mkdir_with_parents()` /
`file.make_directory_with_parents()` have no async variant and are accepted —
note that in a comment.

*Shexli: EGO030, displayed on review pages as EGO-X-004 (warning).*

### R29 🟡 Avoid synchronous subprocess APIs in shell code
Same blocking concern as R28. Avoid `GLib.spawn_sync()`,
`GLib.spawn_command_line_sync()` and the blocking `communicate()` /
`communicate_utf8()` / `wait*()` variants in shell code. Use
`Gio.Subprocess.new()` and read output via the `*_async`/Promise forms, or
offload heavy work to a separate app talking over D-Bus (best practices).
*Shexli: EGO028; source: [gjs.guide — Subprocesses](https://gjs.guide/guides/gio/subprocesses.html).*

### R30 🟡 No direct `imports._gi` usage
`imports._gi` reaches into GJS internals. Use supported APIs instead — e.g.
`InjectionManager` for overriding shell methods on 45+.
*Shexli: EGO031; source: [gjs.guide — Extension/InjectionManager](https://gjs.guide/extensions/topics/extension.html#injectionmanager).*

### R31 🟡 45+ preferences use `fillPreferencesWindow`
`getPreferencesWidget()` is the legacy (pre-45) prefs entry point. GNOME 45+
code implements `fillPreferencesWindow(window)` and builds
`Adw.PreferencesPage` / `PreferencesGroup` content.
*Shexli: EGO032, displayed as EGO-C45-001; source: [gjs.guide — GNOME 45 porting, Preferences](https://gjs.guide/extensions/upgrading/gnome-shell-45.html#preferences).*

### R32 🟡 No lookup helpers for the current extension
`lookupByUUID()` / `lookupByURL()` on the extension manager to reach *your own*
extension is the legacy pattern. Inside `Extension` / `ExtensionPreferences`
use `this`, `this.getSettings()`, `this.metadata` and `this.path`.
*Shexli: EGO036; source: [gjs.guide — GNOME 45 porting, ExtensionUtils](https://gjs.guide/extensions/upgrading/gnome-shell-45.html#extensionutils).*

### R33 🟢 Don't load/unload the default `stylesheet.css` manually
GNOME Shell loads the packaged `stylesheet.css` automatically; manual
`load_stylesheet()`/`unload_stylesheet()` calls for it are unnecessary and
reviewable. Only non-default theme manipulation needs explicit handling — and
must be reverted in `disable()`.
*Shexli: EGO034; source: [gjs.guide — Anatomy, stylesheet.css](https://gjs.guide/extensions/overview/anatomy.html#stylesheet-css).*

### R34 🟡 Preferences: release window-scoped objects on close
`fillPreferencesWindow()` runs per window. Objects stored on the exported
prefs class instance (`this._…`) outlive the window unless a `close-request`
handler releases them — store nothing on the instance, or clean up on window
close. *Shexli: EGO033; source: R2.*

### R35 🟡 Abort Soup sessions during cleanup
A `Soup.Session` that lives across `enable()`/`disable()` cycles **SHOULD** be
`abort()`ed in `disable()`/`destroy()` so in-flight requests stop, then the
reference released. *Shexli: EGO037;
[source: Soup.Session.abort](https://gjs-docs.gnome.org/soup30~3.0/soup.session#method-abort).*

## 7. Version compatibility (removed-API checks)

Applied only when the corresponding major version is listed in
`metadata.json` → `shell-version`, mirroring how EGO's Shexli analyzer gates
them (severity there: error).

### C49 — GNOME Shell 49 removals
([porting guide](https://gjs.guide/extensions/upgrading/gnome-shell-49.html))
- **C49-1 🔴** `DoNotDisturbSwitch` from `ui/calendar.js` is removed. (EGO-C49-001)
- **C49-2 🔴** `Clutter.ClickAction()` / `Clutter.TapAction()` are removed. (EGO-C49-002)
- **C49-3 🔴** `Meta.Window.maximize()`/`unmaximize()` no longer take
  `Meta.MaximizeFlags`. (EGO-C49-003)
- **C49-4 🔴** `Meta.Window.get_maximized()` is removed. (EGO-C49-004)
- **C49-5 🔴** `Meta.CursorTracker.set_pointer_visible()` is removed. (EGO-C49-005)

### C50 — GNOME Shell 50 removals
([porting guide](https://gjs.guide/extensions/upgrading/gnome-shell-50.html))
- **C50-1 🔴** `global.display` signals `restart` and `show-restart-message`
  are no longer emitted. (EGO-C50-001)
- **C50-2 🔴** `RunDialog._restart()` is removed (X11 support dropped). (EGO-C50-002)

## Appendix — Shexli (EGO static analyzer) cross-reference

EGO runs [Shexli](https://gitlab.gnome.org/Infrastructure/extensions-web)
(experimental) on every upload; review pages group its numeric rules under
display ids. Numeric ids are from its published rule spec; display ids are
what submitters see on extensions.gnome.org review pages.

| This skill | Shexli numeric | Shexli display | Rule |
| --- | --- | --- | --- |
| R1 | EGO013 | — | static-only initialization |
| R2 | EGO014 | EGO-L-002 | destroy objects |
| R2 | EGO027 | EGO-L-005 | release owned references |
| R3 | EGO015 | EGO-L-003 | disconnect signals |
| R4 | EGO016 | EGO-L-004 | remove sources |
| R4 | EGO035 | — | remove before re-create |
| R5 | EGO017 | — | deprecated modules |
| R6 | EGO018 | — | GTK in shell |
| R7 | EGO019 | — | Shell libs in prefs |
| R9 / R10 / R20 | EGO020 / EGO021 / EGO023 | — | manual-review only (obfuscation, logging, telemetry) |
| R11 | EGO029 | — | run_dispose |
| R12 | EGO022 | — | binaries |
| R13 | — | EGO-A-005 | clipboard scrutiny (manual review) |
| R14 | EGO024 | — | privileged subprocesses |
| R17 / R18 / R19 | EGO001–EGO008, EGO009–EGO012 | EGO-M-008 (unlock-dialog comment, R18) | metadata, session modes, schemas |
| R25 | EGO025, EGO026 | EGO-P-006 (compiled schemas) | unnecessary artifacts, unreachable modules |
| R28 | EGO030 | EGO-X-004 | synchronous file IO |
| R29 | EGO028 | — | synchronous subprocesses |
| R30 | EGO031 | — | `imports._gi` |
| R31 | EGO032 | EGO-C45-001 | `fillPreferencesWindow` |
| R32 | EGO036 | — | lookup helpers |
| R33 | EGO034 | — | manual stylesheet |
| R34 | EGO033 | — | prefs close-request cleanup |
| R35 | EGO037 | — | Soup.Session abort |
| C49-1…5 | EGO-C49-001…005 | — | GNOME 49 removals |
| C50-1…2 | EGO-C50-001…002 | — | GNOME 50 removals |

Shexli is AGPL-3.0; this skill implements its *rule semantics* only, sourced
from the MIT-licensed gjs.guide pages and public EGO review output. No Shexli
code is used here.
