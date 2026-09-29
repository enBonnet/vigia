# metadata.json, GSettings Schemas and Packaging

## metadata.json — required fields

| Field | Rules |
| --- | --- |
| `uuid` | `extension-id@namespace`; each part only letters, numbers, `.`, `_`, `-`. **MUST NOT** use `gnome.org` as namespace (registered domains/usernames OK, e.g. `user.github.io`). Install dir name must equal the uuid. |
| `name` | Short, descriptive; forks **MUST** have a unique name. |
| `description` | Reasonable length; `\n` escapes or `*` bullets allowed. Clipboard-using extensions **MUST** declare it here (R13). |
| `shell-version` | Array, ≥1 entry; major-only since GNOME 40 (`"45"`, `"46"`…); **only stable releases + at most one dev release**; never future versions. |
| `url` | Git repo / issues page. Required for EGO submissions. |

## Optional fields

| Field | Rules |
| --- | --- |
| `gettext-domain` | Unique; convention = the uuid. |
| `settings-schema` | Recommended. Lets `this.getSettings()` be called with **no arguments**. Convention: `org.gnome.shell.extensions.<id>`. |
| `session-modes` | Only `user` / `unlock-dialog` valid for EGO submissions (`gdm` is for system extensions and is rejected by the EGO analyzer). **MUST be dropped if only `user` is used** (the default). |
| `version` | EGO-internal; **MUST** be a whole number if present; developers **SHOULD NOT** set it. See [EGO versioning and uploads](#ego-versioning-and-uploads). |
| `version-name` | User-visible version displayed on the extension page; regex `^(?!^[. ]+$)[a-zA-Z0-9 .]{1,16}$`. Use it to publish the project's own semver (e.g. `"1.1"`) alongside the internal counter, and bump it per release. |
| `donations` | Keys only from: `buymeacoffee`, `custom`, `github`, `kofi`, `liberapay`, `opencollective`, `patreon`, `paypal`. Values: string or array (max 3). `custom` = full URL; others = user handle. Drop if unused. |

Minimal example:

```json
{
    "uuid": "color-button@my-account.github.io",
    "name": "ColorButton",
    "description": "ColorButton adds a colored button to the panel.",
    "shell-version": [ "45", "46", "47" ],
    "url": "https://github.com/my-account/color-button"
}
```

Review checklist: no unnecessary keys (R17); JSON parses; uuid regex
`^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$` and no `gnome.org` namespace; shell-version
plausible (no future versions); `version` not a string/semver; session-modes
valid; `donations` keys valid; `version-name` (if present) matches the
intended release.

## EGO versioning and uploads

How the version numbers on an extension page actually work (matters when
auditing "wrong version" reports and when advising on re-submission):

- The internal `version` is a **per-extension upload counter assigned by the
  site** — it increments on every submission, whether approved, rejected, or
  still unreviewed. Developers cannot choose it and should not put it in
  metadata.json.
- What users see as the version on the page is `version-name`. Ship the
  project's own semver there (e.g. `"1.1"`) so the page does not show a bare
  upload counter; the two numbering schemes are independent and never need to
  match.
- **A new upload supersedes a pending review.** Uploading is allowed while the
  previous version is "Unreviewed"; reviewers only review the latest upload,
  so the superseded one stays in the table but is never reviewed. Leave a
  note on the review thread saying the newest upload replaces it, and
  summarize what changed since the last look — reviewers prioritize small
  diffs.
- Rejected and superseded versions remain listed forever; the extension stays
  unpublished until some version is approved. When EGO serves an approved
  download it injects the internal `version` into the packaged
  metadata.json — so at runtime `this.metadata.version` (prefs and extension)
  holds the site counter, while `version-name` keeps the human version.

## GSettings schemas

- Schema ID **MUST** start with `org.gnome.shell.extensions`.
- Path **MUST** start with `/org/gnome/shell/extensions`.
- XML file **MUST** be inside the extension ZIP, at `schemas/<schema-id>.gschema.xml`
  (basename = schema ID).
- The compiled `schemas/gschemas.compiled` **MUST NOT** be shipped in the EGO
  ZIP (R25 / EGO-P-006 — warning from Shexli, EGO's automated reviewer; human
  reviewers echo it). EGO compiles the schema when serving the download, so
  only the XML source belongs in the package. Keep the compiled file solely as
  a gitignored local build artifact for source-tree installs.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<schemalist>
  <schema id="org.gnome.shell.extensions.example"
          path="/org/gnome/shell/extensions/example/">
    <key name="show-indicator" type="b">
      <default>true</default>
      <summary>Show the panel indicator</summary>
    </key>
  </schema>
</schemalist>
```

Usage check (best practice): with `settings-schema` in metadata, code calls
`this.getSettings()` argument-less — flag hardcoded `SCHEMA_ID` constants.

## Packaging

ZIP layout (dir name = uuid):

```
example@gjs.guide.zip
    locale/…/example.mo          (compiled translations only)
    schemas/org.gnome.shell.extensions.example.gschema.xml
    extension.js                 (required)
    metadata.json                (required)
    prefs.js                     (optional — GTK4/Adw process)
    stylesheet.css               (optional — Shell CSS only)
```

Do not ship: build/install scripts, `.po`/`.pot` sources, unused icons/media,
node_modules, transpiler output maps (R25). Binaries are forbidden (R12).

## Process isolation recap (packaging-relevant)

- `extension.js` runs **inside gnome-shell**: `St`/`Clutter`/`Meta`/`Shell` OK,
  `Gtk`/`Gdk`/`Adw` forbidden (R6).
- `prefs.js` runs in a separate GTK4/Adw process: `Gtk`/`Adw`/`Gdk` OK,
  `St`/`Clutter`/`Meta`/`Shell` forbidden (R7).
- Modules shared by both processes must import neither set (best practices).
