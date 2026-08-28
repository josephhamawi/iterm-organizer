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
    assert supervisor.should_offer_restore(store, 459) is False


def test_offers_restore_for_a_new_iterm_instance(tmp_path):
    store = Store(str(tmp_path))
    store.ensure()
    supervisor.mark_restore_done(store, 100)  # a previous instance
    assert supervisor.should_offer_restore(store, 459) is True


def test_does_not_offer_restore_twice_for_the_same_instance(tmp_path):
    """launchd restarts us whenever we exit; the user must not be re-prompted."""
    store = Store(str(tmp_path))
    store.ensure()
    supervisor.mark_restore_done(store, 459)
    assert supervisor.should_offer_restore(store, 459) is False


def test_offers_restore_again_after_iterm_restarts(tmp_path):
    store = Store(str(tmp_path))
    store.ensure()
    supervisor.mark_restore_done(store, 459)
    assert supervisor.should_offer_restore(store, 999) is True


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
    assert supervisor.should_offer_restore(store, 459) is False


def test_mark_restore_done_preserves_other_state(tmp_path):
    store = Store(str(tmp_path))
    store.ensure()
    supervisor.write_state(store, {"keep": "me"})
    supervisor.mark_restore_done(store, 459)
    state = supervisor.read_state(store)
    assert state["keep"] == "me"
    assert state["restored_for_pid"] == 459


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
