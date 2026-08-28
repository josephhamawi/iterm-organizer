#!/bin/bash
# Remove the AutoLaunch shim and CLI wrapper. Leaves ~/.iterm-organizer alone.
set -euo pipefail
rm -f "$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm_organizer.py"
rm -f "$HOME/.local/bin/iterm-organizer"
echo "Removed. Snapshots and config are still in ~/.iterm-organizer (delete manually if you want)."
