# Official Best Practices — Code Quality Benchmark

> Source: [gjs.guide — GNOME Extension Best Practices](https://gjs.guide/extensions/review-guidelines/best-practices.html)
> (GJS Guide, MIT licensed). This page is *written by the EGO team specifically
> as a reference and benchmark for LLMs that generate or review extension code*.
> Curated 2026-09. Use it as the primary anti-pattern checklist during review.

## AI maintainer notice

Publishing on EGO is an agreement to maintain the extension. Generated files
**must** include this notice:

```js
// Generated with AI for personal use.
// Do NOT upload to extensions.gnome.org (EGO) unless you understand JavaScript
// and can maintain this code.
```

If submitted files still contain these comments they will be **flagged**, because
it suggests the author didn't read the code. In a review: the notice must be
present in AI-generated code, and must be removed by the author before EGO upload.

## Avoid unnecessary try-catch wrappers

`destroy()`, `connect()`, `disconnect()`, `abort()`, `GLib.Source.remove()` do
not throw unhandled exceptions.

Bad:
```js
if (this._sourceId) {
    try {
        GLib.Source.remove(this._sourceId);
    } catch (e) {
    }
    this._sourceId = null;
}
```
Correct:
```js
if (this._sourceId) {
    GLib.Source.remove(this._sourceId);
    this._sourceId = null;
}
```

## Avoid unnecessary checks

No `?.()` optional calls or `typeof fn === 'function'` guards for guaranteed
methods/built-ins. Generate clean code for a single targeted GNOME Shell version;
if multi-version support is truly needed use the official port guides.

Bad:
```js
if (typeof TextDecoder === 'function')
    this._textDecoder = new TextDecoder('utf-8');
```
```js
boop() {
    if (typeof this.beep === 'function') {
        this.beep();
    }
}
pop() {
    this.beep?.();
}
```
Correct:
```js
this._textDecoder = new TextDecoder('utf-8');
boop() { this.beep(); }
pop() { this.beep(); }
```

## Lifecycle and destruction state

No `_destroyed`/`_enabled` boolean flags to guard lifecycle races — after
`destroy()` the instance is nulled out and never used again.

Custom `destroy()` order:
1. Remove active timeouts and GLib sources
2. Disconnect all signal handlers
3. Release child references and resources
4. `super.destroy()` as the **final** step

Bad:
```js
destroy() {
    if (this._destroyed)
        return;
    this._destroyed = true;
    ...
}
```

## UI elements: icons vs. emojis

- Icons: `Gtk.Image` in prefs, `St.Icon` (or `icon_name` properties) in shell
  UI. Unicode emojis are not icons.
- Progress: use `St.BarLevel` or custom widgets, not ASCII (`█░░`).

## Formatting

Maximum line length **200 characters** (avoids horizontal scrolling in the EGO
review UI).

## Comments

Self-explanatory code; no comments explaining basic JS syntax, trivial
operations, or line-by-line translation into natural language.

## Subprocesses and D-Bus

Avoid external shell commands. Prefer D-Bus for system services. Offload heavy
work to a separate app communicating via D-Bus, keeping the Shell process light.

## Helper functions instead of duplication

No copy-pasted identical code blocks. Shared utility modules used by both
`extension.js` and `prefs.js` must never import `St`, `Clutter`, `Gtk`, `Gdk`,
`Adw` (process isolation).

## Process isolation

Keep UI modules strictly per process; put prefs-only modules in a `prefs/`
directory so isolation is obvious to reviewers.

## Keep the entry point small

No thousands of lines in `extension.js`; split logic into modules so cleanup is
reviewable.

## Keep `enable()` and `disable()` close

Adjacent in the class definition so reviewers can verify cleanup; avoid method
aliasing without a strong structural reason.

## Modules are better than a single file

Single huge files are hard to maintain and can even lag the EGO review page.
Modular single-responsibility files speed up review.

## No incomplete or placeholder extensions

Empty lifecycle stubs with "nothing here" code are rejected; logic must be
complete and functional.

## No spaghetti cleanup

Every class manages its own resources: the class that connects a signal, adds a
timeout, or creates a Soup session / `Gio.Cancellable` also cleans it up.
Cleanup in a different class than initialization makes leaks near-impossible to
review.

## Timeout removal next to creation

If a function can be called repeatedly and creates a timer, remove any existing
source **immediately before** creating the new one:

```js
// the source removed before the timeout creation
if (this._sourceId) {
    GLib.Source.remove(this._sourceId);
    this._sourceId = null;
}
this._sourceId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 5, () => {
    // ...
    return GLib.SOURCE_CONTINUE;
});
```

## Settings schema ID in metadata

Define `settings-schema` in `metadata.json` and call `this.getSettings()` with
no arguments — no repeated schema-ID constants in file scope:

```json
{ "settings-schema": "org.gnome.shell.extensions.my-id" }
```
```js
enable()  { this._settings = this.getSettings(); }
disable() { this._settings = null; }
```
