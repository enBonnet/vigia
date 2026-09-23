#!/usr/bin/env bash
# Re-fetch the imported brand/status glyphs into extension/icons/.
#
# Sources, via the Iconify API (https://api.iconify.design):
#   simple-icons  — CC0 files; logos remain trademarks of their owners
#                   (nominative use: indicating which agent is running)
#   mdi           — Pictogrammers Material Design Icons (Apache-2.0)
#
# The resulting SVGs are committed to the repo, so installs never touch the
# network. Run this only to refresh them. The robot (vigia-generic) and the
# eye (vigia) are hand-drawn and are NOT touched by this script.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../extension/icons" && pwd)"

fetch() { # <out-name> <iconify-prefix> <icon-name>
    local out="$DIR/vigia-$1-symbolic.svg"
    curl -fsSL --max-time 30 "https://api.iconify.design/$2/$3.svg" -o "$out"
    # normalize: nominal 16px render size (the 24x24 viewBox is kept; St scales)
    sed -i -E 's/width="1em"|width="24"/width="16"/; s/height="1em"|height="24"/height="16"/' "$out"
    echo "✔ $out"
}

fetch oc        simple-icons opencode
fetch cc        simple-icons claude
fetch cx        simple-icons openai        # the ChatGPT knot (no separate chatgpt mark)
fetch gemini    simple-icons googlegemini
fetch cursor    simple-icons cursor
fetch copilot   simple-icons githubcopilot
fetch windsurf  simple-icons windsurf
fetch cline     simple-icons cline

fetch state-busy mdi   shovel              # working
fetch state-idle mdi   sleep               # idle — the zzz glyph
