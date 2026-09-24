#!/usr/bin/env bash
# Pack the VigIA extension into a zip ready for extensions.gnome.org upload
# or a manual `gnome-extensions install`.
#
#   ./scripts/pack.sh [output-dir]     (default: ./pack)
#
# Only extension/ ships: metadata.json, extension.js, prefs.js,
# stylesheet.css, icons/ and schemas/. The daemon and hook reporter are
# installed separately by install.sh and are not part of the extension zip.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UUID="vigia@enbonnet.github.com"
OUT="${1:-$ROOT/pack}"

command -v unzip >/dev/null || { echo "unzip(1) is required" >&2; exit 1; }
command -v gnome-extensions >/dev/null || { echo "gnome-extensions is required" >&2; exit 1; }

# Compile as a schema syntax check and to refresh the local dev cache; the
# packer itself only picks up *.gschema.xml. GNOME 45+ compiles schemas at
# install time — gnome-extensions install, extensions.gnome.org installs and
# auto-updates all run glib-compile-schemas on the extracted schemas/ — so
# the zip must carry the XML only: EGO rejects packages that ship
# schemas/gschemas.compiled (EGO-P-006). icons/ is loaded by absolute path
# at runtime (extension.path/icons/...), so it must ride along as an extra
# source.
glib-compile-schemas "$ROOT/extension/schemas"
rm -rf "$OUT"
mkdir -p "$OUT"
gnome-extensions pack --force --out-dir="$OUT" --extra-source=icons "$ROOT/extension"

echo "→ $OUT/$UUID.shell-extension.zip"
unzip -l "$OUT/$UUID.shell-extension.zip"
# Fail loudly if the build artifact snuck back into the zip.
if unzip -l "$OUT/$UUID.shell-extension.zip" | grep -q schemas/gschemas.compiled; then
    echo "ERROR: gschemas.compiled must not ship in the zip (EGO-P-006)" >&2
    exit 1
fi
