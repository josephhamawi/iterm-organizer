"""Tests for the iTerm2 glue, driven by fake API objects.

These exercise the tree walking and the send-text sequencing without a running
iTerm2. They are the only coverage the glue layer can get automatically.
"""

import asyncio

import pytest

from iterm_organizer import daemon, model
from iterm_organizer.config import Config
from iterm_organizer.model import Pane, Snapshot, Split, Tab, Window
from iterm_organizer.plan import build_plan

HOME = "/Users/tester"


def run(coro):
    return asyncio.run(coro)


# --- fakes ----------------------------------------------------------------


class FakeProfile:
    def __init__(self, name):
        self.name = name


class FakeSession:
    def __init__(self, session_id, variables, profile="Default"):
        self.session_id = session_id
        self._vars = dict(variables)
        self._vars.setdefault("profileName", profile)
        self._profile = profile
        self.sent = []
        self.names = []
        self.children = None  # marks this as a leaf for capture_node
        self.split_calls = []
        self._next_id = 0

    async def async_get_variable(self, name):
        return self._vars.get(name)

    async def async_get_profile(self):
        raise AssertionError(
            "async_get_profile returns the session name, not the profile name"
        )

    async def async_send_text(self, text):
        self.sent.append(text)

    async def async_set_name(self, name):
        self.names.append(name)

    async def async_split_pane(self, vertical=False, profile=None):
        self._next_id += 1
        new = FakeSession(f"{self.session_id}.{self._next_id}", {})
        self.split_calls.append(vertical)
        return new


class FakeSplitter:
    def __init__(self, vertical, children):
        self.vertical = vertical
        self.children = children


def leaf(session_id="s1", **variables):
    variables.setdefault("path", f"{HOME}/Development/app")
    return FakeSession(session_id, variables)


# --- capture --------------------------------------------------------------


def test_capture_pane_reads_variables(monkeypatch, tmp_path):
    monkeypatch.setattr(model, "read_git_branch", lambda cwd: "main")
    session = leaf(
        "abc", autoName="app", jobName="node", jobPid="4242", pid="100",
        path=str(tmp_path),
    )
    table = {100: (1, "login -fp mac"), 200: (100, "-zsh"), 4242: (200, "npm run dev")}
    pane = run(daemon.capture_pane(session, table))
    assert pane.session_id == "abc"
    assert pane.cwd == str(tmp_path)
    assert pane.title == "app"
    assert pane.job_pid == 4242
    assert pane.argv == "npm run dev"
    assert pane.profile == "Default"
    assert pane.git_branch == "main"


def test_capture_pane_prefers_shallowest_command_over_job_pid():
    """iTerm2's jobPid points at the deepest descendant. For a Claude Code pane
    that is one of the MCP helpers it spawned, not the command the user ran."""
    session = leaf("s", jobName="mcp-server-darwi", jobPid="900", pid="100")
    table = {
        100: (1, "login -fp mac"),
        200: (100, "-zsh"),
        300: (200, "claude"),
        900: (300, "/Users/you/.local/share/lsp/out/lsp-server-darwin-x64 --app code"),
    }
    pane = run(daemon.capture_pane(session, table))
    assert pane.argv == "claude"


def test_capture_pane_idle_shell_has_no_command():
    session = leaf("s", jobName="zsh", jobPid="200", pid="100")
    table = {100: (1, "login -fp mac"), 200: (100, "-zsh")}
    pane = run(daemon.capture_pane(session, table))
    assert pane.argv == ""


def test_capture_pane_falls_back_to_job_pid_when_tree_walk_finds_nothing():
    session = leaf("s", jobName="vim", jobPid="500", pid="")
    pane = run(daemon.capture_pane(session, {500: (200, "vim notes.md")}))
    assert pane.argv == "vim notes.md"


def test_capture_pane_survives_a_missing_variable():
    class Broken(FakeSession):
        async def async_get_variable(self, name):
            raise RuntimeError("api hiccup")

    pane = run(daemon.capture_pane(Broken("s", {}), {}))
    assert pane.cwd == ""
    assert pane.argv == ""


def test_capture_node_builds_the_split_tree():
    tree = FakeSplitter(True, [leaf("a"), FakeSplitter(False, [leaf("b"), leaf("c")])])
    node = run(daemon.capture_node(tree, {}))
    assert isinstance(node, Split)
    assert node.vertical is True
    assert isinstance(node.children[1], Split)
    assert node.children[1].vertical is False
    assert [p.session_id for p in node.children[1].children] == ["b", "c"]


def test_capture_node_unwraps_single_session_splitter():
    """iTerm2 wraps even a one-pane tab in a Splitter; the snapshot should not."""
    node = run(daemon.capture_node(FakeSplitter(False, [leaf("only")]), {}))
    assert isinstance(node, Pane)
    assert node.session_id == "only"


def test_capture_node_unwraps_nested_single_child_splitters():
    nested = FakeSplitter(True, [FakeSplitter(False, [leaf("deep")])])
    node = run(daemon.capture_node(nested, {}))
    assert isinstance(node, Pane)
    assert node.session_id == "deep"


def test_capture_pane_reads_the_profile_from_profile_name():
    """async_get_profile().name becomes the SESSION name once a pane is renamed,
    which would lose the real profile and break splits on restore."""
    session = leaf("s", profileName="Solarized", autoName="my pane", pid="1")
    pane = run(daemon.capture_pane(session, {}))
    assert pane.profile == "Solarized"
    assert pane.title == "my pane"


def test_capture_pane_drops_a_title_that_is_only_the_profile_name():
    session = leaf("s", profileName="Default", autoName="Default", pid="1")
    assert run(daemon.capture_pane(session, {})).title == ""


# --- realize --------------------------------------------------------------


def make_plan(root, **cfg_kw):
    snap = Snapshot(created_at=1.0, windows=[Window("w", None, [Tab("t", root)])])
    return build_plan(snap, Config(**cfg_kw), home=HOME, isdir=lambda _: True)


def pane_node(**kw):
    base = dict(session_id="s", cwd=f"{HOME}/Development/app", profile="Default")
    base.update(kw)
    return Pane(**base)


def test_realize_sends_cd_banner_and_command():
    plan = make_plan(pane_node(argv="npm run dev", git_branch="main", title="app"))
    session = FakeSession("root", {})
    errors = run(daemon._realize_node(plan.windows[0].tabs[0].root, session, []))
    assert errors is None
    assert session.sent[0] == f"cd {HOME}/Development/app\n"
    assert session.sent[1].startswith("printf '%s\\n' ")
    assert session.sent[2] == "npm run dev\n"
    assert session.names == ["app"]


def test_realize_types_unknown_command_without_newline():
    plan = make_plan(pane_node(argv="./deploy.sh"))
    session = FakeSession("root", {})
    run(daemon._realize_node(plan.windows[0].tabs[0].root, session, []))
    assert session.sent[-1] == "./deploy.sh"
    assert not session.sent[-1].endswith("\n")


def test_realize_splits_in_the_recorded_direction():
    tree = Split(vertical=True, children=[pane_node(session_id="a"), pane_node(session_id="b")])
    plan = make_plan(tree)
    session = FakeSession("root", {})
    run(daemon._realize_node(plan.windows[0].tabs[0].root, session, []))
    assert session.split_calls == [True]


def test_realize_collects_errors_instead_of_aborting():
    class FailingSplit(FakeSession):
        async def async_split_pane(self, vertical=False, profile=None):
            raise RuntimeError("no room")

    tree = Split(vertical=False, children=[pane_node(session_id="a"), pane_node(session_id="b")])
    plan = make_plan(tree)
    session = FailingSplit("root", {})
    errors = []
    run(daemon._realize_node(plan.windows[0].tabs[0].root, session, errors))
    assert errors and "no room" in errors[0]
    # The first pane still got set up despite the failure.
    assert session.sent


def test_first_profile_walks_into_splits():
    from iterm_organizer.plan import PlanPane, PlanSplit

    tree = PlanSplit(False, [PlanSplit(True, [PlanPane(setup=None, profile="Deep")])])
    assert daemon._first_profile(tree) == "Deep"


# --- helpers --------------------------------------------------------------


@pytest.mark.parametrize(
    "seconds,expected",
    [(30, "moments ago"), (600, "10 minutes ago"), (7300, "2 hours ago"), (200000, "2 days ago")],
)
def test_describe_age(seconds, expected):
    assert daemon._describe_age(seconds) == expected


def test_window_summary_dedupes_and_truncates(monkeypatch):
    monkeypatch.setattr("os.path.expanduser", lambda p: HOME if p == "~" else p)
    window = Window(
        "w",
        None,
        [
            Tab("t", Split(False, [pane_node(cwd=f"{HOME}/a"), pane_node(cwd=f"{HOME}/a")])),
            Tab("t2", pane_node(cwd=f"{HOME}/b")),
            Tab("t3", pane_node(cwd=f"{HOME}/c")),
            Tab("t4", pane_node(cwd=f"{HOME}/d")),
        ],
    )
    summary = daemon._window_summary(window)
    assert summary == "~/a, ~/b, ~/c (+1 more)"


# --- tab session selection ------------------------------------------------


class FakeTab:
    def __init__(self, sessions, current=None):
        self.sessions = sessions
        self.current_session = current


def test_first_session_prefers_the_session_list():
    a, b = FakeSession("a", {}), FakeSession("b", {})
    assert daemon._first_session(FakeTab([a, b], current=b)) is a


def test_first_session_when_current_session_is_none():
    """iTerm2 leaves current_session None on a freshly created window, which is
    exactly when restore needs it."""
    a = FakeSession("a", {})
    assert daemon._first_session(FakeTab([a], current=None)) is a


def test_first_session_with_no_sessions_at_all():
    assert daemon._first_session(FakeTab([], current=None)) is None


def test_realize_attaches_to_the_app_before_splitting(monkeypatch):
    """Session.async_split_pane asserts on a delegate that only async_get_app
    installs, so realize must fetch the app first."""
    calls = []

    async def fake_get_app(connection):
        calls.append(connection)
        return object()

    monkeypatch.setattr(daemon.iterm2, "async_get_app", fake_get_app)

    async def fake_create(connection, profile=None):
        return None  # stop after the attach; window creation is not under test

    monkeypatch.setattr(daemon.iterm2.Window, "async_create", staticmethod(fake_create))
    plan = make_plan(pane_node())
    errors = run(daemon.realize("conn", plan))
    assert calls == ["conn"]
    assert any("could not create window" in e for e in errors)


def test_realize_reports_a_failed_attach(monkeypatch):
    async def boom(connection):
        raise RuntimeError("no connection")

    monkeypatch.setattr(daemon.iterm2, "async_get_app", boom)
    errors = run(daemon.realize("conn", make_plan(pane_node())))
    assert errors == ["could not attach to iTerm2: no connection"]
