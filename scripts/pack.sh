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

command -v zip >/dev/null || { echo "zip(1) is required" >&2; exit 1; }
command -v gnome-extensions >/dev/null || { echo "gnome-extensions is required" >&2; exit 1; }

# The GNOME 50 packer compiles nothing and omits schemas/gschemas.compiled,
# without which getSettings() fails after install — compile first, pack, then
# append the freshly built cache. icons/ is loaded by absolute path at runtime
# (extension.path/icons/...), so it must ride along as an extra source.
glib-compile-schemas "$ROOT/extension/schemas"
rm -rf "$OUT"
mkdir -p "$OUT"
gnome-extensions pack --force --out-dir="$OUT" --extra-source=icons "$ROOT/extension"
(cd "$ROOT/extension" && zip -q "$OUT/$UUID.shell-extension.zip" schemas/gschemas.compiled)

echo "→ $OUT/$UUID.shell-extension.zip"
unzip -l "$OUT/$UUID.shell-extension.zip"
# Fail loudly if the cache went missing again.
unzip -l "$OUT/$UUID.shell-extension.zip" | grep -q schemas/gschemas.compiled \
    || { echo "ERROR: gschemas.compiled missing from the zip" >&2; exit 1; }
