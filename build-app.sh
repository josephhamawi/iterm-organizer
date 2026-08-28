#!/bin/bash
# Build "iTerm Organizer.app" and its documentation folder.
#
#   ./build-app.sh [destination]      default: ~/Development/iTermOrganizer
#
# The bundle is self-contained: it carries its own copy of the package and its
# dependencies, and uses whatever Python 3.11+ is on the machine. No virtualenv
# and no iTerm2 Python runtime are involved.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${1:-$HOME/Development/iTermOrganizer}"
APP="$DEST/iTerm Organizer.app"
CONTENTS="$APP/Contents"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$REPO/src/iterm_organizer/__init__.py")"

if [ -x "$REPO/.venv/bin/pip" ]; then PIP="$REPO/.venv/bin/pip"; else PIP="python3 -m pip"; fi

echo "building iTerm Organizer $VERSION -> $DEST"
rm -rf "$APP"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources/app" "$DEST/docs"

# 1. The package itself.
cp -R "$REPO/src/iterm_organizer" "$CONTENTS/Resources/app/iterm_organizer"
find "$CONTENTS/Resources/app" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# 2. Vendored dependencies, pure Python so they are relocatable.
$PIP install --quiet --target "$CONTENTS/Resources/vendor" iterm2
find "$CONTENTS/Resources/vendor" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
rm -rf "$CONTENTS/Resources/vendor/bin"

# 3. Executable.
cp "$REPO/packaging/launcher.sh" "$CONTENTS/MacOS/iTermOrganizer"
chmod +x "$CONTENTS/MacOS/iTermOrganizer"

# 4. Icon.
python3 "$REPO/packaging/make_icon.py" "$CONTENTS/Resources/AppIcon.icns"

# 5. Info.plist.
VERSION="$VERSION" python3 - "$CONTENTS/Info.plist" <<'PYEOF'
import os, plistlib, sys
plist = {
    "CFBundleName": "iTerm Organizer",
    "CFBundleDisplayName": "iTerm Organizer",
    "CFBundleIdentifier": "com.rami.itermorganizer",
    "CFBundleVersion": os.environ["VERSION"],
    "CFBundleShortVersionString": os.environ["VERSION"],
    "CFBundleExecutable": "iTermOrganizer",
    "CFBundleIconFile": "AppIcon",
    "CFBundlePackageType": "APPL",
    "CFBundleInfoDictionaryVersion": "6.0",
    "LSMinimumSystemVersion": "12.0",
    # Background agent: no Dock icon, no menu bar.
    "LSUIElement": True,
    "NSHumanReadableCopyright": "Local tool. No network access, no telemetry.",
    "NSAppleEventsUsageDescription":
        "iTerm Organizer asks iTerm2 for a connection cookie so it can save "
        "and restore your terminal sessions.",
}
with open(sys.argv[1], "wb") as fh:
    plistlib.dump(plist, fh)
PYEOF

# 6. Finder-friendly installers and documentation.
cp "$REPO/packaging/Install.command" "$DEST/Install.command"
cp "$REPO/packaging/Uninstall.command" "$DEST/Uninstall.command"
chmod +x "$DEST/Install.command" "$DEST/Uninstall.command"
cp "$REPO/docs/app/README.md" "$DEST/README.md"
for doc in HOW-TO-USE CONFIGURATION TROUBLESHOOTING HOW-IT-WORKS; do
    cp "$REPO/docs/app/$doc.md" "$DEST/docs/$doc.md"
done

# Make Finder pick up the new icon rather than a cached generic one.
touch "$APP"

echo "built:"
echo "  $APP"
du -sh "$APP" | sed 's/^/  size: /'
