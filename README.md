# Vigía

*The lookout for your AI agents.* A GNOME Shell extension + tiny daemon that shows
in the top bar **which** AI coding agent is working, **which** one needs you, and
**which** one just finished — and keeps the machine awake while they work.

```
Top bar:   ⌁ ✳! ⬡✓          ⌁ OpenCode working (pulsing)
             │ │ └─ Codex done (green ✓, fades)   ✳ Claude Code waiting on you (yellow !)
             │ └─── ...                           ⬡ Codex done
             └───── OpenCode busy
```

## What it detects

| Agent | Working | Waiting on you | Done | How |
|---|---|---|---|---|
| OpenCode | ✅ | ✅ (permission prompts) | ✅ | Background service API (`session.list` / `permission.request.list`) |
| Claude Code | ✅ | ✅ (`Notification`, `PermissionRequest` hooks) | ✅ (`Stop` hook) | Hook reporter pushed over D-Bus |
| Codex, aider, goose, … | ✅ | — | — | `/proc` scan of process names |

**Keep awake:** while any agent is *working* or *waiting on you*, Vigía holds a
GNOME session inhibitor (same mechanism as Caffeine) so the screen doesn't
blank and the machine doesn't suspend. Released automatically when everything
goes idle. Toggleable in the menu.

## Install

```bash
cd ~/projects/personal/vigia
./scripts/install.sh
```

Then restart GNOME Shell (X11: `Alt+F2` → `r` → Enter · Wayland: log out/in).
The installer:

1. installs the extension to `~/.local/share/gnome-shell/extensions/vigia@enbonnet/`
2. registers the settings schema
3. installs the hook reporter to `~/.local/bin/vigia-claude.sh`
4. **additively** merges hooks into `~/.claude/settings.json`
   (backup at `~/.claude/settings.json.vigia.bak`; existing hooks, e.g. Orca's, untouched)
5. installs and starts the `vigia` systemd user service

Uninstall: `./scripts/uninstall.sh` (removes only its own hook entries).

## Architecture

```
┌──────────────────────┐  D-Bus signals   ┌─────────────────────────────┐
│  vigiad (systemd     │ ───────────────▶ │  Vigía GNOME Shell          │
│  --user service)     │  AgentChanged /  │  extension                  │
│                      │  AgentGone       │                             │
│  • OpenCode poller   │                  │  • per-agent panel icons    │
│    (opencode api)    │                  │  • popup menu w/ details    │
│  • Claude hook       │                  │  • keep-awake inhibitor     │
│    ingest (Report)   │                  │    (org.gnome.SessionManager)│
│  • /proc scan        │                  │                             │
└──────────────────────┘                  └─────────────────────────────┘
        ▲                                           ▲
        │ stdin JSON → busctl                        │ gsettings
┌───────┴─────────────┐                  ┌──────────┴──────────┐
│ vigia-claude.sh     │                  │ prefs window        │
│ (Claude Code hooks) │                  │ (source toggles,    │
└─────────────────────┘                  │  poll, keep-awake)  │
                                         └─────────────────────┘
```

The extension never does blocking I/O inside GNOME Shell — all watching happens
in the daemon; the extension only listens to D-Bus signals and manages the
inhibitor.

### D-Bus API (`org.vigia.Watcher`, `/org/vigia/Watcher`)

```bash
# live snapshot
busctl --user call org.vigia.Watcher /org/vigia/Watcher org.vigia.Watcher List

# simulate a Claude Code hook (what vigia-claude.sh does)
busctl --user call org.vigia.Watcher /org/vigia/Watcher org.vigia.Watcher Report \
  sssss claude test-session busy myproject ""
```

Agent ids: `opencode:<sessionID>`, `claude:<sessionID>`, `proc:<name>`.
States: `idle` · `busy` · `question` · `done`.

### State rules

- **OpenCode**: busy while `time.updated > time.idle` on the session; pending
  rows in `permission.request.list` → `question`; finished sessions → `done`
  flash, then idle.
- **Claude Code**: `UserPromptSubmit`/`Pre*ToolUse` → busy · `Notification`,
  `PermissionRequest` → question · `Stop` → done · `SessionEnd` → removed.
  Crash-safe: stale busy decays to idle after 30 min.
- **Generic**: busy while a matching process exists.

## Settings

Preferences (extension menu → *Vigía Preferences…*): per-source toggles,
generic process list, OpenCode poll interval, done-badge fade seconds,
show-when-idle, keep-awake. Stored in
`org.gnome.shell.extensions.vigia` (the daemon reads the same keys live).

## Troubleshooting

```bash
systemctl --user status vigia                 # daemon running?
journalctl --user -u vigia -f                 # daemon logs
python3 scripts/vigia-status.py               # human-readable live state
python3 scripts/vigia-status.py --watch       # follow state changes live
opencode api session.list | head              # OpenCode source feed
python3 scripts/claude_hooks.py status        # hook wiring
```

Extension logs: `journalctl --user -u org.gnome.Shell@wayland.service | grep vigia`
(or the X11 equivalent).

## Icon language

| Glyph | Agent |
|---|---|
| `>_` chevron+underscore | OpenCode |
| ✳ six-arm sparkle | Claude Code |
| ⬡ hexagon dot | Codex |
| robot head | any other agent |
| eye | Vigía itself (idle/brand) |

States: pulsing = working · yellow `!` = needs you · green `✓` = done (dims
after N seconds) · dimmed = idle.
