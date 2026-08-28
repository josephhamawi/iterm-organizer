#!/bin/bash
# Double-click to install. Starts the background agent and sets it to run at login.
cd "$(dirname "$0")"
APP="$PWD/iTerm Organizer.app/Contents/MacOS/iTermOrganizer"
if [ ! -x "$APP" ]; then
    echo "Cannot find 'iTerm Organizer.app' next to this installer."
    echo "Keep Install.command in the same folder as the app."
    read -n 1 -s -r -p "Press any key to close."
    exit 1
fi
"$APP" --install && echo && "$APP" --status
echo
echo "Done. iTerm Organizer now runs at login and saves your terminals every 60 seconds."
read -n 1 -s -r -p "Press any key to close."
