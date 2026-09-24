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
`disable()`.

### R3 🔴 Disconnect all signals
Any signal connections made by the extension **MUST** be disconnected in
`disable()`.

### R4 🔴 Remove main loop sources
Any main loop sources created **MUST** be removed in `disable()` — even if the
callback would eventually return `GLib.SOURCE_REMOVE` / `false`.

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

### R14 🔴 Privileged subprocesses
Spawning privileged subprocesses should be avoided at all costs. If absolutely
necessary: run with `pkexec`, and the executable/script **MUST NOT** be
user-writable.

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
a comment explaining the `unlock-dialog` usage. Extensions **MUST NOT** disable
selectively.

### R19 🔴 GSettings schemas
Schema ID **MUST** start with `org.gnome.shell.extensions`; path **MUST** start
with `/org/gnome/shell/extensions`; the schema XML **MUST** be inside the ZIP;
filename **MUST** be `<schema-id>.gschema.xml`.

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
No build/install scripts, `.po`/`.pot` files, or unused icons/media. Excessive
extra data may cause rejection.

### R26 🟢 Use a linter
ESLint with the [GNOME Shell rules](https://gitlab.gnome.org/GNOME/gnome-shell-extensions/tree/main/lint).

### R27 🟢 UI design
Preferences should follow the [GNOME HIG](https://developer.gnome.org/hig/).
