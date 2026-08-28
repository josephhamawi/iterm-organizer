#!/bin/bash
# Stop and remove the launchd agent and the CLI. Snapshots and config stay.
set -euo pipefail
LABEL="com.rami.iterm-organizer"
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
rm -f "$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm_organizer.py"
rm -f "$HOME/.local/bin/iterm-organizer"
echo "Stopped and removed. Snapshots and config remain in ~/.iterm-organizer"
