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


# --- duplicate installs ---------------------------------------------------

TWO_COPIES = """\
  476 /Users/you/Applications/iTerm.app/Contents/MacOS/iTerm2
 1073 /Users/you/Library/Application Support/iTerm2/iTermServer-3.6.11
 2026 /Applications/iTerm.app/Contents/MacOS/iTerm2
"""


def test_running_bundles_lists_each_distinct_iterm_copy():
    assert supervisor.running_bundles(TWO_COPIES) == [
        "/Users/you/Applications/iTerm.app",
        "/Applications/iTerm.app",
    ]


def test_running_bundles_ignores_helpers_and_duplicte_pids():
    assert supervisor.running_bundles(PS_OUTPUT) == ["/Applications/iTerm.app"]


def test_duplicate_warning_names_both_copies_and_the_one_that_wins():
    """Two installed copies is the failure that hides the restore prompt: the
    cookie request resolves the bundle id through LaunchServices, so the agent
    connects to a copy the user may not be using, and the alert opens there."""
    warning = supervisor.duplicate_warning(
        ["/Users/you/Applications/iTerm.app", "/Applications/iTerm.app"],
        resolved="/Applications/iTerm.app",
    )
    assert warning
    assert "/Users/you/Applications/iTerm.app" in warning
    assert "/Applications/iTerm.app" in warning


def test_no_duplicate_warning_for_a_single_copy():
    assert supervisor.duplicate_warning(
        ["/Applications/iTerm.app"], resolved="/Applications/iTerm.app"
    ) == ""


# --- pending restore offer ------------------------------------------------


def test_offer_round_trips_through_state(tmp_path):
    """An alert the user never answered must leave a trace. Without it the
    offer dies with the dialog and cannot be asked for later."""
    store = Store(str(tmp_path))
    store.ensure()
    assert supervisor.read_offer(store) is None
    supervisor.record_offer(store, windows=6, panes=9, created_at=1234.0)
    offer = supervisor.read_offer(store)
    assert offer["windows"] == 6
    assert offer["panes"] == 9
    assert offer["created_at"] == 1234.0


def test_clearing_the_offer_keeps_the_rest_of_state(tmp_path):
    store = Store(str(tmp_path))
    store.ensure()
    supervisor.mark_restore_done(store, "pid:1")
    supervisor.record_offer(store, windows=2, panes=2, created_at=5.0)
    supervisor.clear_offer(store)
    assert supervisor.read_offer(store) is None
    assert supervisor.read_state(store)["restored_for_instance"] == "pid:1"


def test_clear_offer_is_a_no_op_without_one(tmp_path):
    """--restore clears the offer on the way out; it must not rewrite state
    when there was nothing pending."""
    store = Store(str(tmp_path))
    store.ensure()
    supervisor.clear_offer(store)
    assert not os.path.exists(supervisor.state_path(store))


def test_preferred_bundle_is_the_first_running_copy():
    """ps lists by ascending pid, so the copy started at login comes first -
    the one whose windows the user is looking at, rather than one launched
    on demand by the cookie request."""
    assert supervisor.preferred_bundle(TWO_COPIES) == "/Users/you/Applications/iTerm.app"


def test_preferred_bundle_is_none_when_iterm_is_not_running():
    assert supervisor.preferred_bundle("1200 /usr/bin/python3\n") is None


def test_app_target_env_points_the_cookie_request_at_that_copy(monkeypatch):
    """Without IT2_APP_PATH the iterm2 library asks for application "iTerm2",
    which LaunchServices resolves to one specific copy and launches it. With
    two copies installed that is how the agent ends up connected to - and
    prompting in - an iTerm2 the user is not using."""
    monkeypatch.delenv("IT2_APP_PATH", raising=False)
    assert supervisor.target_iterm_app("/Users/you/Applications/iTerm.app") is True
    assert os.environ["IT2_APP_PATH"] == "/Users/you/Applications/iTerm.app"


def test_app_target_env_left_alone_when_nothing_is_running(monkeypatch):
    monkeypatch.delenv("IT2_APP_PATH", raising=False)
    assert supervisor.target_iterm_app(None) is False
    assert "IT2_APP_PATH" not in os.environ


def test_connect_does_not_retry_inside_the_process(monkeypatch, tmp_path):
    """Each connection attempt must start from a fresh process.

    The library's own retry loop never returns, so IT2_APP_PATH - read once at
    startup from ps - would stay pinned to an iTerm2 that has since quit. Let
    the attempt fail, exit, and let launchd start us again (ThrottleInterval
    gives the backoff); the new process re-reads ps and re-aims.
    """
    calls = {}

    class FakeIterm2:
        @staticmethod
        def run_forever(coro, retry=False):
            calls["retry"] = retry

    monkeypatch.setattr(supervisor, "_run_forever", FakeIterm2.run_forever)
    supervisor.connect_forever(lambda connection: None)
    assert calls["retry"] is False
