#!/bin/bash
# Double-click to stop and remove the background agent.
# Your snapshots and config in ~/.iterm-organizer are kept.
cd "$(dirname "$0")"
APP="$PWD/iTerm Organizer.app/Contents/MacOS/iTermOrganizer"
if [ -x "$APP" ]; then
    "$APP" --uninstall
else
    /bin/launchctl bootout "gui/$UID/com.rami.iterm-organizer" 2>/dev/null
    rm -f "$HOME/Library/LaunchAgents/com.rami.iterm-organizer.plist"
    echo "agent stopped and removed"
fi
echo
echo "Snapshots and settings are still in ~/.iterm-organizer"
read -n 1 -s -r -p "Press any key to close."
