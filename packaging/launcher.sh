#!/bin/bash
# iTerm Organizer - app bundle entry point.
#
# Three roles in one executable:
#   (no args)    opened from Finder: ensure the agent runs, then report
#   --daemon     run by launchd: the snapshot/restore daemon itself
#   anything     passed through to the command line interface
set -euo pipefail

# Resolve to a clean absolute path: it is written into the launchd job, so
# "./iTerm Organizer.app/..." must not end up baked in.
HERE="$(cd -P "$(dirname "$0")" && pwd)"
SELF="$HERE/$(basename "$0")"
RES="$(cd -P "$HERE/../Resources" && pwd)"

export PYTHONPATH="$RES/app:$RES/vendor"

# The vendored dependencies are pure Python, so any modern interpreter works.
# 3.11 is the floor: tomllib is needed to read config.toml.
find_python() {
    local candidate resolved
    for candidate in \
        /usr/local/bin/python3 \
        /opt/homebrew/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
        /usr/bin/python3
    do
        [ -x "$candidate" ] || continue
        if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            echo "$candidate"
            return 0
        fi
    done
    resolved="$(command -v python3 2>/dev/null || true)"
    if [ -n "$resolved" ] && "$resolved" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        echo "$resolved"
        return 0
    fi
    return 1
}

if ! PY="$(find_python)"; then
    /usr/bin/osascript -e 'display alert "iTerm Organizer" message "Python 3.11 or newer is required, and none was found.

Install it with:  brew install python" as critical' >/dev/null 2>&1 || true
    exit 1
fi

case "${1:-}" in
    "")           exec "$PY" -m iterm_organizer --app "$SELF" ;;
    --daemon)     exec "$PY" -m iterm_organizer --daemon ;;
    --install)    exec "$PY" -m iterm_organizer --install "$SELF" ;;
    *)            exec "$PY" -m iterm_organizer "$@" ;;
esac
