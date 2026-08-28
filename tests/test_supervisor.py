import json
import multiprocessing
import os

import pytest

from iterm_organizer import supervisor
from iterm_organizer.lock import AlreadyRunning, InstanceLock
from iterm_organizer.model import Store

PS_OUTPUT = """\
  459 /Applications/iTerm.app/Contents/MacOS/iTerm2
  652 /Users/you/Library/Application Support/iTerm2/iTermServer-3.6.11
  765 /Applications/iTerm.app/Contents/XPCServices/pidinfo.xpc/Contents/MacOS/pidinfo
 1200 /usr/bin/python3
"""


# --- pid detection --------------------------------------------------------


def test_parse_iterm_pid_finds_the_app_not_its_helpers():
    """iTermServer and pidinfo.xpc must not be mistaken for iTerm2 itself."""
    assert supervisor.parse_iterm_pid(PS_OUTPUT) == 459


def test_parse_iterm_pid_when_not_running():
    assert supervisor.parse_iterm_pid("  1200 /usr/bin/python3\n") is None


def test_parse_iterm_pid_ignores_malformed_lines():
    assert supervisor.parse_iterm_pid("garbage\n\n  x /MacOS/iTerm2\n") is None


# --- restore-once-per-instance -------------------------------------------


def test_first_run_after_install_adopts_the_running_instance(tmp_path):
    """Installing while you are working must not prompt to restore over you."""
    store = Store(str(tmp_path))
    store.ensure()
    assert supervisor.should_offer_restore(store, "pid:459") is False


def test_offers_restore_for_a_new_iterm_instance(tmp_path):
    store = Store(str(tmp_path))
    store.ensure()
    supervisor.mark_restore_done(store, "pid:100")  # a previous instance
    assert supervisor.should_offer_restore(store, "pid:459") is True


def test_does_not_offer_restore_twice_for_the_same_instance(tmp_path):
    """launchd restarts us whenever we exit; the user must not be re-prompted."""
    store = Store(str(tmp_path))
    store.ensure()
    supervisor.mark_restore_done(store, "pid:459")
    assert supervisor.should_offer_restore(store, "pid:459") is False


def test_offers_restore_again_after_iterm_restarts(tmp_path):
    store = Store(str(tmp_path))
    store.ensure()
    supervisor.mark_restore_done(store, "pid:459")
    assert supervisor.should_offer_restore(store, "pid:999") is True


def test_no_restore_when_iterm_is_not_running(tmp_path):
    store = Store(str(tmp_path))
    store.ensure()
    assert supervisor.should_offer_restore(store, None) is False


def test_state_survives_a_corrupt_file(tmp_path):
    store = Store(str(tmp_path))
    store.ensure()
    with open(supervisor.state_path(store), "w") as fh:
        fh.write("{not json")
    assert supervisor.read_state(store) == {}
    # Unreadable state is treated as a first run: adopt, do not prompt.
    assert supervisor.should_offer_restore(store, "pid:459") is False


def test_mark_restore_done_preserves_other_state(tmp_path):
    store = Store(str(tmp_path))
    store.ensure()
    supervisor.write_state(store, {"keep": "me"})
    supervisor.mark_restore_done(store, "pid:459")
    state = supervisor.read_state(store)
    assert state["keep"] == "me"
    assert state["restored_for_instance"] == "pid:459"


# --- instance lock --------------------------------------------------------


def _try_lock(path, queue):
    try:
        InstanceLock(path).acquire()
        queue.put("acquired")
    except AlreadyRunning:
        queue.put("blocked")
    except Exception as exc:  # noqa: BLE001
        queue.put(f"error: {exc}")


def test_lock_blocks_a_second_process(tmp_path):
    path = str(tmp_path / "daemon.lock")
    lock = InstanceLock(path)
    lock.acquire()
    try:
        queue = multiprocessing.get_context("spawn").Queue()
        proc = multiprocessing.get_context("spawn").Process(
            target=_try_lock, args=(path, queue)
        )
        proc.start()
        proc.join(20)
        assert queue.get(timeout=5) == "blocked"
    finally:
        lock.release()


def test_lock_is_available_after_release(tmp_path):
    path = str(tmp_path / "daemon.lock")
    first = InstanceLock(path)
    first.acquire()
    first.release()
    second = InstanceLock(path)
    second.acquire()
    second.release()


def test_lock_records_the_pid(tmp_path):
    path = str(tmp_path / "daemon.lock")
    with InstanceLock(path):
        assert open(path).read().strip() == str(os.getpid())


def test_lock_context_manager_releases(tmp_path):
    path = str(tmp_path / "daemon.lock")
    with InstanceLock(path):
        pass
    InstanceLock(path).acquire()  # would raise if still held


def test_release_is_idempotent(tmp_path):
    lock = InstanceLock(str(tmp_path / "daemon.lock"))
    lock.acquire()
    lock.release()
    lock.release()


def test_exit_code_signals_launchd_to_restart():
    """SuccessfulExit=false: 0 keeps us down, non-zero brings us back."""
    assert supervisor.EXIT_RECONNECT != 0


# --- restore decision timing ---------------------------------------------


def test_restore_decision_is_made_after_connecting(tmp_path, monkeypatch):
    """At login launchd starts us before iTerm2 exists, so the pid read before
    connecting is None. Deciding then would disable restore for the whole boot
    - exactly the case this tool exists for."""
    import asyncio

    import iterm2

    from iterm_organizer import daemon
    from iterm_organizer.model import Store as RealStore

    store = RealStore(str(tmp_path))
    store.ensure()
    supervisor.mark_restore_done(store, "pid:459")  # the pre-reboot iTerm2

    # First read happens before run_forever (iTerm2 not up yet), second one
    # inside the connected callback.
    monkeypatch.setattr(supervisor, "iterm_pid", lambda *a, **k: None)
    monkeypatch.setattr(supervisor, "instance_key", lambda: "pid:523")
    monkeypatch.setattr(supervisor, "Store", lambda: RealStore(str(tmp_path)))

    seen = {}

    async def fake_main(connection, store=None, config=None, allow_restore=True):
        seen["allow_restore"] = allow_restore

    monkeypatch.setattr(daemon, "main", fake_main)

    def fake_run_forever(coro, retry=False, debug=False):
        asyncio.run(coro("connection"))

    monkeypatch.setattr(iterm2, "run_forever", fake_run_forever)

    supervisor.run()

    assert seen["allow_restore"] is True
    assert supervisor.read_state(store)["restored_for_instance"] == "pid:523"


def test_no_restore_when_reconnecting_to_the_same_instance(tmp_path, monkeypatch):
    import asyncio

    import iterm2

    from iterm_organizer import daemon
    from iterm_organizer.model import Store as RealStore

    store = RealStore(str(tmp_path))
    store.ensure()
    supervisor.mark_restore_done(store, "pid:523")

    monkeypatch.setattr(supervisor, "iterm_pid", lambda *a, **k: 523)
    monkeypatch.setattr(supervisor, "instance_key", lambda: "pid:523")
    monkeypatch.setattr(supervisor, "Store", lambda: RealStore(str(tmp_path)))

    seen = {}

    async def fake_main(connection, store=None, config=None, allow_restore=True):
        seen["allow_restore"] = allow_restore

    monkeypatch.setattr(daemon, "main", fake_main)
    monkeypatch.setattr(
        iterm2, "run_forever", lambda coro, retry=False, debug=False: asyncio.run(coro("c"))
    )

    supervisor.run()
    assert seen["allow_restore"] is False


# --- instance identity ----------------------------------------------------


def test_iterm_pid_retries_when_ps_is_slow(monkeypatch):
    """At login the load average can be in the dozens; one timed-out ps must
    not be read as "iTerm2 is not running"."""
    calls = {"n": 0}

    def flaky(argv, capture_output=None, text=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise subprocess_timeout(argv, timeout)

        class R:
            stdout = "  523 /Applications/iTerm.app/Contents/MacOS/iTerm2\n"

        return R()

    import subprocess as sp

    def subprocess_timeout(argv, timeout):
        return sp.TimeoutExpired(argv, timeout or 1)

    monkeypatch.setattr(sp, "run", flaky)
    monkeypatch.setattr(supervisor.time, "sleep", lambda _s: None)
    assert supervisor.iterm_pid(attempts=3, timeout=1) == 523
    assert calls["n"] == 3


def test_iterm_pid_gives_up_after_attempts(monkeypatch):
    import subprocess as sp

    def always_fail(*a, **k):
        raise sp.TimeoutExpired("ps", 1)

    monkeypatch.setattr(sp, "run", always_fail)
    monkeypatch.setattr(supervisor.time, "sleep", lambda _s: None)
    assert supervisor.iterm_pid(attempts=2, timeout=1) is None


def test_instance_key_falls_back_to_the_socket(monkeypatch, tmp_path):
    """Being unable to name the instance must not read as "same as before"."""
    sock = tmp_path / "iterm2-daemon-1.socket"
    sock.write_text("")
    os.utime(sock, (1_700_000_000, 1_700_000_000))
    monkeypatch.setattr(supervisor, "iterm_pid", lambda *a, **k: None)
    monkeypatch.setattr(supervisor, "SOCKET_PATH", str(sock))
    assert supervisor.instance_key() == "sock:1700000000"


def test_instance_key_is_none_when_nothing_is_identifiable(monkeypatch):
    monkeypatch.setattr(supervisor, "iterm_pid", lambda *a, **k: None)
    monkeypatch.setattr(supervisor, "SOCKET_PATH", "/nonexistent/socket")
    assert supervisor.instance_key() is None


def test_state_migrates_from_the_old_pid_only_field(tmp_path):
    store = Store(str(tmp_path))
    store.ensure()
    supervisor.write_state(store, {"restored_for_pid": 459})
    assert supervisor.should_offer_restore(store, "pid:459") is False
    assert supervisor.should_offer_restore(store, "pid:523") is True


def test_socket_fallback_distinguishes_relaunches(tmp_path, monkeypatch):
    sock = tmp_path / "s.socket"
    sock.write_text("")
    monkeypatch.setattr(supervisor, "iterm_pid", lambda *a, **k: None)
    monkeypatch.setattr(supervisor, "SOCKET_PATH", str(sock))
    os.utime(sock, (1_700_000_000, 1_700_000_000))
    first = supervisor.instance_key()
    os.utime(sock, (1_700_009_999, 1_700_009_999))
    assert supervisor.instance_key() != first
