#!/usr/bin/env bash
# Vigía installer: extension, schemas, hook script, Claude hook merge, daemon.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UUID="vigia@enbonnet.github.com"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
EXT_DIR="$DATA/gnome-shell/extensions/$UUID"
SCHEMA_DIR="$DATA/glib-2.0/schemas"
BIN_DIR="$HOME/.local/bin"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

echo "→ Installing GNOME extension to $EXT_DIR"
mkdir -p "$EXT_DIR" "$SCHEMA_DIR" "$BIN_DIR" "$UNIT_DIR"
cp -r "$ROOT/extension/." "$EXT_DIR/"
glib-compile-schemas "$EXT_DIR/schemas"

echo "→ Registering schema for the daemon"
cp "$ROOT/extension/schemas/org.gnome.shell.extensions.vigia.gschema.xml" "$SCHEMA_DIR/"
glib-compile-schemas "$SCHEMA_DIR"

echo "→ Installing hook reporter to $BIN_DIR/vigia-claude.sh"
install -m 755 "$ROOT/hooks/vigia-claude.sh" "$BIN_DIR/vigia-claude.sh"

echo "→ Merging hooks into Claude Code settings (additive, backup kept)"
python3 "$ROOT/scripts/claude_hooks.py" merge

echo "→ Installing and starting the vigia daemon"
sed "s|%ROOT%|$ROOT|g" "$ROOT/daemon/vigia.service" > "$UNIT_DIR/vigia.service"
systemctl --user daemon-reload
systemctl --user enable --now vigia.service

echo "→ Enabling GNOME extension"
gnome-extensions enable "$UUID" 2>/dev/null || true
# gnome-extensions can't enable what the running shell hasn't scanned yet —
# persist it in gsettings so it loads on the next login regardless.
python3 - "$UUID" <<'PYEOF'
import ast, subprocess, sys
uuid = sys.argv[1]
out = subprocess.run(['gsettings','get','org.gnome.shell','enabled-extensions'],
                     capture_output=True, text=True).stdout.strip()
exts = ast.literal_eval(out) if out else []
if uuid not in exts:
    exts.append(uuid)
    subprocess.run(['gsettings','set','org.gnome.shell','enabled-extensions',
                    '[' + ', '.join(f"'{e}'" for e in exts) + ']'], check=True)
PYEOF

cat <<EOF

✔ Vigía installed.

  Daemon:        systemctl --user status vigia
  Live state:    busctl --user call org.vigia.Watcher /org/vigia/Watcher org.vigia.Watcher List

  If the top-bar icon does not appear yet, restart GNOME Shell:
    - X11:     Alt+F2, type r, Enter
    - Wayland: log out and back in
EOF
