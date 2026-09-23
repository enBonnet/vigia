# VigIA

*Vig-**IA**: the IA that keeps watch.* A GNOME Shell extension + tiny daemon that shows
in the top bar **which** AI coding agent is working, **which** one needs you, and
**which** one just finished — and keeps the machine awake while they work.

```
Top bar:   ▣ ✳? ⌾✓          ▣ OpenCode working (pulsing)
             │ │ └─ Codex done (green ✓, fades)   ✳ Claude Code needs you (yellow ?)
             │ └─── ...                           ⌾ Codex done
             └───── OpenCode busy
```

## What it detects

| Agent | Working | Waiting on you | Done | How |
|---|---|---|---|---|
| OpenCode | ✅ | ✅ (permission prompts) | ✅ | Background service API (`session.list` / `permission.request.list`) |
| Claude Code | ✅ | ✅ (`Notification`, `PermissionRequest` hooks) | ✅ (`Stop` hook) | Hook reporter pushed over D-Bus |
| Codex, aider, goose, … | ✅ | — | — | `/proc` scan of process names |

**Keep awake:** while any agent is *working* or *waiting on you*, VigIA holds a
logind `idle:sleep:handle-lid-switch` **block inhibitor** (enforced by
systemd-logind, works on AC and battery alike — GNOME 50 removed the old
session-manager inhibit) and pauses screen blanking for the duration,
restoring your original settings when everything goes idle. The lid is
irrelevant: closed or open, the machine stays on. Toggleable in the menu.

**Critical battery — the only surrender:** when the battery hits the
*critically low* level (watched via UPower), VigIA stops your agents for you —
OpenCode turns are interrupted via its API, Claude Code sessions get a graceful
SIGINT (their `Stop` hooks report normally), generic agents are terminated —
releases the keep-awake hold and shows a notification explaining exactly what
was stopped. Plug the charger back in and keep working.

## Install

```bash
cd ~/projects/personal/vigia
./scripts/install.sh
```

Then restart GNOME Shell (X11: `Alt+F2` → `r` → Enter · Wayland: log out/in).
The installer:

1. installs the extension to `~/.local/share/gnome-shell/extensions/vigia@enbonnet.github.com/`
2. registers the settings schema
3. installs the hook reporter to `~/.local/bin/vigia-claude.sh`
4. **additively** merges hooks into `~/.claude/settings.json`
   (backup at `~/.claude/settings.json.vigia.bak`; existing hooks, e.g. Orca's, untouched)
5. installs and starts the `vigia` systemd user service

Uninstall: `./scripts/uninstall.sh` (removes only its own hook entries).

## Architecture

```
┌──────────────────────┐  D-Bus signals   ┌─────────────────────────────┐
│  vigiad (systemd     │ ───────────────▶ │  VigIA GNOME Shell          │
│  --user service)     │  AgentChanged /  │  extension                  │
│                      │  AgentGone       │                             │
│  • OpenCode poller   │                  │  • per-agent panel icons    │
│    (opencode api)    │                  │  • popup menu w/ details    │
│  • Claude hook       │                  │  • keep-awake: logind block  │
│    ingest (Report)   │                  │    inhibitor + blank hold    │
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

All watching happens in the daemon; the extension only listens to D-Bus
signals and manages the inhibitor. Its own D-Bus calls are synchronous but
short and bounded (the daemon answers `List` immediately; inhibit/UPower setup
happens once per state change).

### D-Bus API (`org.vigia.Watcher`, `/org/vigia/Watcher`)

```bash
# live snapshot
busctl --user call org.vigia.Watcher /org/vigia/Watcher org.vigia.Watcher List

# simulate a Claude Code hook (what vigia-claude.sh does)
busctl --user call org.vigia.Watcher /org/vigia/Watcher org.vigia.Watcher Report \
  ssssss claude test-session busy myproject "" ""
```

Agent ids: `opencode:<sessionID>`, `claude:<sessionID>`, `proc:<name>`.
States: `idle` · `busy` · `question` · `done`.

### State rules

- **OpenCode**: busy comes straight from the server's own `session.active`
  list — a session is working exactly while it has a run in flight, so dead
  or aborted sessions never linger. When a run ends, a working session
  flashes green `done` and is dropped; it never sits as an idle row. Pending
  rows in `permission.request.list` → `question`. Subagent sessions
  (`@explore`/`@review`…) only appear while busy or asking.
- **Claude Code**: `UserPromptSubmit`/`Pre*ToolUse` → busy · `Notification`,
  `PermissionRequest` → question · `Stop` → done · `SessionEnd` → removed.
  Crash-safe twice over: if every PID the hook reported is gone, the entry is
  dropped within seconds (any state); stale busy decays to idle after 30 min
  as a fallback for reports without PIDs. `done` flashes green then drops,
  like OpenCode.
- **Generic**: busy while a matching process exists.

## Settings

Preferences (extension menu → *VigIA Preferences…*): per-source toggles,
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

Agent glyphs are the real brand marks, tinted by the state color:

| Glyph | Agent | Source |
|---|---|---|
| ▣ square frame | OpenCode | simple-icons `opencode` |
| ✳ starburst | Claude Code | simple-icons `claude` |
| ⌾ knot | Codex | simple-icons `openai` — the ChatGPT mark |
| ✦ ▲ ⧉ 〜 ✳︎ | gemini · cursor(-agent) · copilot · windsurf · cline | simple-icons |
| robot head | any other agent (fallback) | hand-drawn |
| eye | VigIA itself (idle/brand) | hand-drawn |

States: pulsing = working · yellow `?` = needs you · green `✓` = done (dims
after N seconds) · dimmed = idle. Menu rows show a status glyph instead of
text: shovel = working · `?` = needs you · `✓` = done · `zzz` = idle.

## Credits

- Agent marks from [Simple Icons](https://simpleicons.org) (CC0). The logos
  remain trademarks of their owners; used here to indicate which agent is
  running.
- `shovel` and `sleep` status glyphs from
  [Pictogrammers Material Design Icons](https://pictogrammers.com)
  (Apache-2.0).
- Refresh the imported glyphs with `./scripts/fetch-icons.sh`; the robot and
  the eye are hand-drawn and untouched by it.
