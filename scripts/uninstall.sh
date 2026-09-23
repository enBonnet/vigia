#!/usr/bin/env bash
# Vigía uninstaller — removes everything install.sh added.
set -euo pipefail

UUID="vigia@enbonnet.github.com"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
EXT_DIR="$DATA/gnome-shell/extensions/$UUID"
SCHEMA_DIR="$DATA/glib-2.0/schemas"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "→ Removing Claude Code hooks (only vigia entries)"
python3 "$ROOT/scripts/claude_hooks.py" unmerge

echo "→ Stopping the daemon"
systemctl --user disable --now vigia.service 2>/dev/null || true
rm -f "$UNIT_DIR/vigia.service"
systemctl --user daemon-reload

echo "→ Removing GNOME extension"
gnome-extensions disable "$UUID" 2>/dev/null || true
rm -rf "$EXT_DIR"
rm -f "$SCHEMA_DIR/org.gnome.shell.extensions.vigia.gschema.xml"
glib-compile-schemas "$SCHEMA_DIR" 2>/dev/null || true

rm -f "$HOME/.local/bin/vigia-claude.sh"

echo "✔ Vigía uninstalled. Restart GNOME Shell to drop the indicator."
echo "  (The Claude Code backup is kept at ~/.claude/settings.json.vigia.bak)"
