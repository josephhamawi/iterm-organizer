#!/bin/bash
# Install iterm-organizer as a launchd agent that starts at login and keeps
# itself running across iTerm2 restarts.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STORE="$HOME/.iterm-organizer"
LABEL="com.rami.iterm-organizer"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
AUTOLAUNCH="$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm_organizer.py"
BIN="$HOME/.local/bin"
VENV="$REPO/.venv"

mkdir -p "$STORE/sessions" "$HOME/Library/LaunchAgents" "$BIN"

# 1. Private virtualenv. Everything runs from here, so the organizer never
#    depends on iTerm2's bundled Python runtime.
if [ ! -x "$VENV/bin/python3" ]; then
  echo "creating venv..."
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet iterm2
echo "venv ready: $($VENV/bin/python3 -V)"

# 2. Starter config, never overwriting an existing one.
if [ ! -f "$STORE/config.toml" ]; then
  PYTHONPATH="$REPO/src" "$VENV/bin/python3" -c \
    'from iterm_organizer.config import default_config_text; print(default_config_text(), end="")' \
    > "$STORE/config.toml"
  echo "wrote $STORE/config.toml"
else
  echo "kept existing $STORE/config.toml"
fi

# 3. Retire the old iTerm2 AutoLaunch shim if a previous version installed one.
#    The launchd agent replaces it and avoids the runtime download prompt.
if [ -f "$AUTOLAUNCH" ]; then
  rm -f "$AUTOLAUNCH"
  echo "removed old AutoLaunch shim"
fi

# 4. launchd agent. KeepAlive/SuccessfulExit=false means: restart on a non-zero
#    exit (iTerm2 quit, so reconnect), stay down on exit 0 (another instance
#    already holds the lock).
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV/bin/python3</string>
        <string>-m</string>
        <string>iterm_organizer.supervisor</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>$REPO/src</string>
    </dict>
    <!-- Without this the agent's AppleScript cookie request blocks forever on
         a TCC Automation prompt that never surfaces for a launchd job.
         Attributing the agent to iTerm2's bundle lets the request through. -->
    <key>AssociatedBundleIdentifiers</key>
    <array>
        <string>com.googlecode.iterm2</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>20</integer>
    <key>ProcessType</key>
    <string>Background</string>
    <key>StandardOutPath</key>
    <string>$STORE/launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>$STORE/launchd.err.log</string>
</dict>
</plist>
PLISTEOF
echo "wrote $PLIST"

# 5. CLI wrapper.
cat > "$BIN/iterm-organizer" <<BINEOF
#!/bin/bash
exec env PYTHONPATH="$REPO/src" "$VENV/bin/python3" -m iterm_organizer "\$@"
BINEOF
chmod +x "$BIN/iterm-organizer"
echo "wrote $BIN/iterm-organizer"

# 6. (Re)load the agent.
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST"
launchctl enable "gui/$UID/$LABEL" 2>/dev/null || true
echo "loaded $LABEL"

sleep 2
if launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
  echo "agent is registered and running"
else
  echo "WARNING: agent did not register; check $STORE/launchd.err.log" >&2
fi

cat <<'MSG'

Running now, and at every login. It does not need iTerm2 to be restarted.

  iterm-organizer --dry-run    see what would be restored
  tail -f ~/.iterm-organizer/organizer.log

The first snapshot lands within 60 seconds.
MSG
