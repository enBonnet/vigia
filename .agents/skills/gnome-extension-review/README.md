# GNOME Extension Review — Agent Skill

An AI agent skill that reviews and audits GNOME Shell extensions written in GJS
against the official [extensions.gnome.org](https://extensions.gnome.org) (EGO)
review guidelines and [gjs.guide](https://gjs.guide) best practices.

It bundles the authoritative EGO review rules (R1–R27), a lifecycle-audit
method, a `js/ui` API map, and a dependency-free Python static scanner — so any
compatible agent can find rejection risks (memory leaks, forbidden imports,
missing `disable()` cleanup, metadata/schema problems) before you submit.

**Scope:** GNOME Shell **45+** (ESModules, `Extension` class). Pre-45 code is
flagged as legacy, not reviewed.

## Requirements

- An agent harness that supports skills (Claude Code or OpenCode)
- [Python 3](https://www.python.org/) — for the static scanner (stdlib only,
  no pip packages)

## Install

### Claude Code

Clone the skill into your personal skills directory:

```sh
git clone https://github.com/enBonnet/gnome-extension-review.git \
  ~/.claude/skills/gnome-extension-review
```

### OpenCode

Clone it globally…

```sh
git clone https://github.com/enBonnet/gnome-extension-review.git \
  ~/.config/opencode/skill/gnome-extension-review
```

…or into a single project:

```sh
git clone https://github.com/enBonnet/gnome-extension-review.git \
  .opencode/skill/gnome-extension-review
```

### Update

```sh
git -C ~/.claude/skills/gnome-extension-review pull
```

(Adjust the path if you installed it elsewhere.)

### Uninstall

```sh
rm -rf ~/.claude/skills/gnome-extension-review
```

## Usage

Once installed, just ask your agent in natural language:

> Review the GNOME extension in ~/projects/my-extension against the EGO guidelines

The skill triggers on words like *review*, *audit*, *check*, or *rate* for
GNOME Shell extensions, and also when asking to *fix* extension code.

### Standalone scanner

The static scanner works without an agent and has no dependencies:

```sh
python3 scripts/static_checks.py /path/to/your-extension
```

- `--json` — machine-readable output
- Exit code `0` = no blocking candidates, `1` = blocking candidates found,
  `2` = usage/IO error

> Findings are **candidates, not verdicts** — verify each one in context before
> acting on it.

## What it checks

| Area | Examples |
|---|---|
| Lifecycle | constructor hygiene, `enable()`/`disable()` symmetry, leak-free cleanup order |
| Signals & sources | connect/disconnect balance, timeout creation/removal, orphaned sources |
| Forbidden imports | `Gtk`/`Gdk`/`Adw` in the shell process, `St`/`Clutter` in prefs (R6/R7) |
| Legacy patterns | `imports.*`, `Lang.bind`, `Mainloop` |
| Metadata & schemas | uuid, `shell-version`, gschema id/path/filename, session-modes |
| Packaging & legal | binaries in the zip, unnecessary files, license, AI notice |

Findings are severity-tagged:

- 🔴 **Blocking** — would likely be rejected by EGO
- 🟡 **Should fix** — SHOULD rules and anti-patterns
- 🟢 **Nice-to-have** — recommendations

## Repository layout

```
├── SKILL.md                     # Skill definition and review workflow
├── references/
│   ├── review-guidelines.md     # EGO review rules (R1–R27)
│   ├── best-practices.md        # gjs.guide anti-pattern benchmark
│   ├── lifecycle-audit.md       # enable/disable symmetry method
│   ├── shell-ui-map.md          # js/ui module map + live API verification
│   └── metadata-and-schemas.md  # metadata.json / gschema / packaging
└── scripts/
    └── static_checks.py         # Candidate-findings scanner
```

## License

[MIT](LICENSE) © Ender Bonnet
