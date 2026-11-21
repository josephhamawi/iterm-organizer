"""Process-level entry point: hold the lock, decide about restore, connect.

Run under launchd with KeepAlive. When iTerm2 quits, the websocket closes and
this process exits; launchd restarts it and it waits for iTerm2 to come back.
That loop is the crash-recovery path.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import subprocess
import sys
import time

from .config import Config, ConfigError, load_config
from .lock import AlreadyRunning, InstanceLock
from .model import Store, write_atomic

log = logging.getLogger("iterm_organizer")

ITERM_SUFFIX = "/MacOS/iTerm2"
ITERM_BUNDLE_ID = "com.googlecode.iterm2"

# launchd KeepAlive is configured with SuccessfulExit=false: exit 0 means
# "stay down", anything else means "bring me back".
EXIT_RECONNECT = 75


def parse_iterm_pid(ps_output: str) -> int | None:
    """Find iTerm2's own pid in ``ps -axo pid=,comm=`` output.

    Matched on the executable path so the helper processes iTerm2 spawns
    (iTermServer, pidinfo.xpc) are not mistaken for the app.
    """
    for line in ps_output.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pid, comm = parts
        if comm.strip().endswith(ITERM_SUFFIX):
            try:
                return int(pid)
            except ValueError:
                continue
    return None


def _ps_output(timeout: float = 15.0) -> str:
    try:
        return subprocess.run(
            ["/bin/ps", "-axo", "pid=,comm="],
            capture_output=True,
            text=True,
            timeout=timeout,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def iterm_pid(attempts: int = 3, timeout: float = 15.0) -> int | None:
    """iTerm2's pid, retrying because login is the worst moment to ask.

    At boot the load average can be in the dozens and a 5 second ps timeout
    was enough to come back empty, which silently disabled restore for the
    whole session.
    """
    for attempt in range(attempts):
        try:
            out = subprocess.run(
                ["/bin/ps", "-axo", "pid=,comm="],
                capture_output=True,
                text=True,
                timeout=timeout,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        pid = parse_iterm_pid(out)
        if pid is not None:
            return pid
        if attempt + 1 < attempts:
            time.sleep(1.0)
    return None


def running_bundles(ps_output: str) -> list[str]:
    """Every distinct iTerm2 .app bundle with a running main process.

    Order is the order ``ps`` reported them. Helper processes (iTermServer,
    pidinfo.xpc) are excluded by matching the main executable path.
    """
    bundles: list[str] = []
    for line in ps_output.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        comm = parts[1].strip()
        if not comm.endswith(ITERM_SUFFIX):
            continue
        bundle = comm[: -len("/Contents" + ITERM_SUFFIX)]
        if bundle and bundle not in bundles:
            bundles.append(bundle)
    return bundles


def duplicate_warning(bundles: list[str], resolved: str | None) -> str:
    """A warning when more than one copy of iTerm2 is running.

    This is the failure that hides the restore prompt. The API cookie is
    requested over AppleScript against the bundle id, which LaunchServices
    resolves to one specific copy - launching it if it is not already up. The
    agent then connects to that copy and shows its alert there, which may not
    be the copy whose windows the user is looking at.
    """
    if len(bundles) < 2:
        return ""
    listed = ", ".join(bundles)
    target = resolved or "unknown"
    return (
        f"{len(bundles)} copies of iTerm2 are running ({listed}). "
        f"The connection and the restore prompt go to {target}, which may not "
        f"be the one you use. Keep one copy installed."
    )


def preferred_bundle(ps_output: str) -> str | None:
    """The iTerm2 copy to talk to: the first one ``ps`` reports.

    ps lists in ascending pid order, so this is the copy that started
    earliest - the one launched at login, with the user's windows in it -
    rather than one that the cookie request itself launched on demand.
    """
    bundles = running_bundles(ps_output)
    return bundles[0] if bundles else None


def target_iterm_app(bundle: str | None) -> bool:
    """Aim the cookie request at ``bundle`` by exporting IT2_APP_PATH.

    The iterm2 library otherwise asks AppleScript for application "iTerm2",
    which LaunchServices resolves by bundle id to one installed copy and
    launches it if needed. With two copies installed that routinely picks the
    copy the user is not using: the connection, and the restore prompt with
    it, then land in an app with no windows and no way to be noticed.
    """
    if not bundle:
        return False
    os.environ["IT2_APP_PATH"] = bundle
    return True


def resolved_bundle() -> str | None:
    """The iTerm2 copy LaunchServices resolves the bundle id to."""
    try:
        out = subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                'tell application "Finder" to return POSIX path of '
                f'(application file id "{ITERM_BUNDLE_ID}" as alias)',
            ],
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out.rstrip("/") or None


SOCKET_PATH = os.path.expanduser(
    "~/Library/Application Support/iTerm2/iterm2-daemon-1.socket"
)


def socket_instance() -> str | None:
    """Fallback instance identity: iTerm2 recreates its API socket at launch."""
    try:
        return "sock:%d" % int(os.stat(SOCKET_PATH).st_mtime)
    except OSError:
        return None


def instance_key() -> str | None:
    """Identify the running iTerm2 instance.

    Prefers the pid. Falls back to the API socket's mtime so that a ps failure
    cannot be mistaken for "same instance as before" - being unable to name the
    instance must not silently cancel a restore.
    """
    pid = iterm_pid()
    if pid is not None:
        return "pid:%d" % pid
    return socket_instance()


def state_path(store: Store) -> str:
    return os.path.join(store.root, "state.json")


def read_state(store: Store) -> dict:
    try:
        with open(state_path(store), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(store: Store, state: dict) -> None:
    try:
        write_atomic(state_path(store), json.dumps(state, indent=2))
    except OSError as exc:
        log.warning("could not persist state: %s", exc)


def _recorded_instance(state: dict) -> str | None:
    if "restored_for_instance" in state:
        return state["restored_for_instance"]
    # Migrate the older pid-only field written by earlier versions.
    if "restored_for_pid" in state:
        return "pid:%s" % state["restored_for_pid"]
    return None


def should_offer_restore(store: Store, instance: str | None) -> bool:
    """Offer a restore once per iTerm2 instance, not once per daemon start.

    launchd restarts this process whenever it exits, so without this the user
    would be prompted again every time the daemon reconnected.

    The very first run after installation adopts whatever iTerm2 is already
    running instead of offering: the user has their windows open right now and
    does not want a restore prompt on top of them.
    """
    if instance is None:
        return False
    recorded = _recorded_instance(read_state(store))
    if recorded is None:
        return False
    return recorded != instance


def mark_restore_done(store: Store, instance: str | None) -> None:
    if instance is None:
        return
    state = read_state(store)
    state["restored_for_instance"] = instance
    state.pop("restored_for_pid", None)
    write_state(store, state)


def record_offer(store: Store, *, windows: int, panes: int, created_at: float) -> None:
    """Remember that a restore was offered but not yet answered."""
    state = read_state(store)
    state["pending_offer"] = {
        "windows": windows,
        "panes": panes,
        "created_at": created_at,
        "offered_at": time.time(),
    }
    write_state(store, state)


def read_offer(store: Store) -> dict | None:
    offer = read_state(store).get("pending_offer")
    return offer if isinstance(offer, dict) else None


def clear_offer(store: Store) -> None:
    state = read_state(store)
    if state.pop("pending_offer", None) is not None:
        write_state(store, state)


def setup_logging(store: Store) -> None:
    store.ensure()
    handler = logging.handlers.RotatingFileHandler(
        store.log, maxBytes=1_000_000, backupCount=2
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    root = logging.getLogger("iterm_organizer")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def load_config_or_defaults(store: Store) -> Config:
    try:
        return load_config(store.config)
    except ConfigError as exc:
        log.error("config error, using defaults: %s", exc)
        return Config()


def run() -> int:
    # Logging first: importing iterm2 pulls in a large dependency tree, and if
    # anything goes wrong there we still want a diagnosable log line.
    store = Store()
    setup_logging(store)
    log.info("process start (pid=%s, python=%s)", os.getpid(), sys.version.split()[0])

    import iterm2

    from . import daemon

    log.info("iterm2 module loaded")

    lock = InstanceLock(os.path.join(store.root, "daemon.lock"))
    try:
        lock.acquire()
    except AlreadyRunning as exc:
        # Exit 0 so launchd's SuccessfulExit=false KeepAlive does NOT respawn
        # us in a tight loop behind an instance that is already healthy.
        log.info("not starting: %s", exc)
        return 0

    config = load_config_or_defaults(store)
    ps_output = _ps_output()
    bundle = preferred_bundle(ps_output)
    if target_iterm_app(bundle):
        log.info("talking to %s", bundle)
    warning = duplicate_warning(running_bundles(ps_output), resolved_bundle())
    if warning:
        log.warning(warning)
    log.info(
        "starting (interval=%ss, iterm2 pid=%s)",
        config.snapshot_interval,
        iterm_pid(attempts=1, timeout=5.0),
    )

    async def entry(connection):
        # Decide about restore HERE, not before run_forever. At login launchd
        # usually starts us before iTerm2 exists, so a pid read up front is
        # None and would permanently disable restore for this boot - the exact
        # case the tool is for. By the time this runs we are connected, so
        # iTerm2 is definitely up and its pid is readable.
        live = instance_key()
        allow = should_offer_restore(store, live)
        # Adopt this iTerm2 instance either way, so a daemon restart against
        # the same instance never re-prompts.
        mark_restore_done(store, live)
        log.info(
            "connected (iterm2 instance=%s, restore=%s)",
            live,
            "offered" if allow else "already handled",
        )
        await daemon.main(connection, store=store, config=config, allow_restore=allow)

    try:
        iterm2.run_forever(entry, retry=True)
    except SystemExit:
        log.info("iTerm2 connection closed")
    except Exception as exc:  # noqa: BLE001
        log.error("daemon exited: %s", exc)
    finally:
        lock.release()
    # Non-zero so launchd restarts us and we wait for iTerm2 to come back.
    # This is the normal path every time iTerm2 quits or crashes.
    return EXIT_RECONNECT


if __name__ == "__main__":
    raise SystemExit(run())
