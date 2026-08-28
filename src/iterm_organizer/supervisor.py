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
