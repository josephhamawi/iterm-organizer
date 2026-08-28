#!/bin/bash
# Install iterm-organizer: AutoLaunch shim, starter config, CLI wrapper.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STORE="$HOME/.iterm-organizer"
AUTOLAUNCH="$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch"
BIN="$HOME/.local/bin"

mkdir -p "$STORE/sessions" "$AUTOLAUNCH" "$BIN"

# 1. Starter config (never overwrite an existing one).
if [ ! -f "$STORE/config.toml" ]; then
  PYTHONPATH="$REPO/src" python3 -c \
    'from iterm_organizer.config import default_config_text; print(default_config_text(), end="")' \
    > "$STORE/config.toml"
  echo "wrote $STORE/config.toml"
else
  echo "kept existing $STORE/config.toml"
fi

# 2. AutoLaunch shim. iTerm2 starts this and injects an auth cookie, so no
#    connection-permission dialog appears.
cat > "$AUTOLAUNCH/iterm_organizer.py" <<SHIM
#!/usr/bin/env python3
"""iTerm2 AutoLaunch shim for iterm-organizer. Real code lives in the repo."""
import os
import sys

REPO = os.path.expanduser("$REPO")
VENV = os.path.join(REPO, ".venv", "bin", "python3")

# iTerm2 runs this under its own bundled Python. If that interpreter predates
# tomllib, re-exec into the repo venv: it inherits ITERM2_COOKIE and
# ITERM2_KEY from the environment, so it still connects without a dialog.
if sys.version_info < (3, 11) and os.path.exists(VENV):
    os.execv(VENV, [VENV, os.path.abspath(__file__)])

sys.path.insert(0, os.path.join(REPO, "src"))

from iterm_organizer.autolaunch import run

run()
SHIM
echo "wrote $AUTOLAUNCH/iterm_organizer.py"

# 3. CLI wrapper, using the repo venv if one exists.
PY_BIN="python3"
[ -x "$REPO/.venv/bin/python" ] && PY_BIN="$REPO/.venv/bin/python"
cat > "$BIN/iterm-organizer" <<PY
#!/bin/bash
exec env PYTHONPATH="$REPO/src" "$PY_BIN" -m iterm_organizer "\$@"
PY
chmod +x "$BIN/iterm-organizer"
echo "wrote $BIN/iterm-organizer"

cat <<'MSG'

Installed. Next:
  1. Restart iTerm2. It will offer to download its Python runtime the first
     time - accept it. The organizer starts automatically after that.
  2. Confirm it is running:   tail -f ~/.iterm-organizer/organizer.log
  3. Inspect what it captured: iterm-organizer --dry-run

Nothing is restored until you have a snapshot and you approve the prompt.
Make sure ~/.local/bin is on your PATH.
MSG
