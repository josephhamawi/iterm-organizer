import os

import pytest

from iterm_organizer import plan as plan_mod
from iterm_organizer.config import Config
from iterm_organizer.model import Pane, Snapshot, Split, Tab, Window
from iterm_organizer.plan import (
    PlanPane,
    PlanSplit,
    build_plan,
    build_setup,
    is_trivial,
    render_plan,
    should_prompt,
    tilde,
)

HOME = "/Users/tester"


@pytest.fixture
def cfg():
    return Config()


def pane(**kw):
    base = dict(session_id="s", cwd=f"{HOME}/Development/app", profile="Default")
    base.update(kw)
    return Pane(**base)


def snap(*windows, created_at=1000.0):
    return Snapshot(created_at=created_at, windows=list(windows))


def one(pane_obj, frame=None, title="t"):
    return snap(Window("w", frame, [Tab(title, pane_obj)]))


def always(_):
    return True


def never(_):
    return False


# --- tilde ----------------------------------------------------------------


def test_tilde():
    assert tilde(f"{HOME}/Development/app", HOME) == "~/Development/app"
    assert tilde(HOME, HOME) == "~"
    assert tilde("/opt/thing", HOME) == "/opt/thing"
    assert tilde("", HOME) == ""


# --- pane setup -----------------------------------------------------------


def test_setup_runs_allowlisted_command(cfg):
    setup = build_setup(pane(argv="npm run dev", git_branch="main"), cfg, HOME, always)
    assert setup.command == "npm run dev"
    assert setup.execute is True
    assert "main" in setup.banner
    assert "was: npm run dev" in setup.banner


def test_setup_types_unknown_command_without_executing(cfg):
    setup = build_setup(pane(argv="./deploy.sh --prod"), cfg, HOME, always)
    assert setup.command == "./deploy.sh --prod"
    assert setup.execute is False


def test_setup_never_command_is_banner_only(cfg):
    setup = build_setup(pane(argv="ssh prod"), cfg, HOME, always)
    assert setup.command == ""
    assert "not restored" in setup.banner


def test_setup_idle_pane_has_no_command(cfg):
    setup = build_setup(pane(argv=""), cfg, HOME, always)
    assert setup.command == ""
    assert setup.banner == "~/Development/app"


def test_setup_claude_is_resumed(cfg):
    setup = build_setup(pane(argv="claude"), cfg, HOME, always)
    assert setup.command == "claude --continue"
    assert setup.execute is True


def test_missing_cwd_falls_back_to_home_and_says_so(cfg):
    setup = build_setup(pane(argv="npm run dev"), cfg, HOME, never)
    assert setup.cwd == HOME
    assert setup.cwd_missing is True
    assert "is gone" in setup.banner


def test_missing_cwd_never_executes_a_command(cfg):
    """Running a project command in the wrong directory is worse than not running it."""
    setup = build_setup(pane(argv="npm run dev"), cfg, HOME, never)
    assert setup.command == ""
    assert setup.execute is False
    assert "not restored" in setup.banner


# --- shell lines ----------------------------------------------------------


def test_shell_lines_order_and_quoting(cfg):
    setup = build_setup(
        pane(cwd=f"{HOME}/my projects/app", argv="npm run dev", git_branch="main"),
        cfg,
        HOME,
        always,
    )
    lines = setup.shell_lines()
    assert lines[0][0] == f"cd '{HOME}/my projects/app'"
    assert lines[0][1] is True
    assert lines[1][0].startswith("printf '%s\\n' ")
    assert lines[2] == ("npm run dev", True)


def test_shell_lines_typed_command_is_not_entered(cfg):
    setup = build_setup(pane(argv="./deploy.sh"), cfg, HOME, always)
    assert setup.shell_lines()[-1] == ("./deploy.sh", False)


def test_banner_is_quoted_so_it_cannot_execute(cfg):
    setup = build_setup(
        pane(cwd=f"{HOME}/a", git_branch="main", argv="npm run dev"), cfg, HOME, always
    )
    banner_line = setup.shell_lines()[1][0]
    assert banner_line.startswith("printf '%s\\n' '")
    assert banner_line.endswith("'")


# --- plan building --------------------------------------------------------


def test_plan_preserves_split_tree(cfg):
    tree = Split(
        vertical=True,
        children=[
            pane(session_id="a"),
            Split(vertical=False, children=[pane(session_id="b"), pane(session_id="c")]),
        ],
    )
    plan = build_plan(snap(Window("w", None, [Tab("t", tree)])), cfg, home=HOME, isdir=always)
    root = plan.windows[0].tabs[0].root
    assert isinstance(root, PlanSplit)
    assert root.vertical is True
    assert isinstance(root.children[0], PlanPane)
    assert isinstance(root.children[1], PlanSplit)
    assert root.children[1].vertical is False
    assert plan.pane_count == 3


def test_plan_budget_drops_whole_tabs_and_warns(cfg):
    cfg = Config(max_restore_panes=3)
    tabs = [Tab(f"t{i}", Split(False, [pane(), pane()])) for i in range(3)]
    plan = build_plan(snap(Window("w", None, tabs)), cfg, home=HOME, isdir=always)
    assert plan.pane_count == 2
    assert plan.skipped_panes == 4
    assert plan.warnings and "max_restore_panes" in plan.warnings[0]


def test_plan_only_windows_filter(cfg):
    windows = [Window(f"w{i}", None, [Tab("t", pane())]) for i in range(3)]
    plan = build_plan(snap(*windows), cfg, home=HOME, isdir=always, only_windows=[0, 2])
    assert len(plan.windows) == 2
    assert plan.pane_count == 2
    assert plan.skipped_panes == 1


def test_plan_of_empty_snapshot_is_empty(cfg):
    plan = build_plan(snap(), cfg, home=HOME, isdir=always)
    assert plan.is_empty


def test_render_plan_is_readable(cfg):
    plan = build_plan(one(pane(argv="npm run dev", git_branch="main")), cfg, home=HOME, isdir=always)
    text = render_plan(plan)
    assert "Window 1" in text
    assert "RUN  npm run dev" in text
    assert "Total: 1 window(s), 1 pane(s)" in text


def test_render_plan_marks_typed_commands(cfg):
    plan = build_plan(one(pane(argv="./deploy.sh")), cfg, home=HOME, isdir=always)
    assert "TYPE ./deploy.sh" in render_plan(plan)


# --- prompt gating --------------------------------------------------------


def test_should_prompt_for_real_work(cfg):
    s = one(pane(argv="npm run dev"))
    assert should_prompt(s, cfg, now=1000.0 + 60, home=HOME) is True


def test_no_prompt_for_no_snapshot(cfg):
    assert should_prompt(None, cfg, now=1000.0, home=HOME) is False


def test_no_prompt_when_stale(cfg):
    s = one(pane(argv="npm run dev"))
    assert should_prompt(s, cfg, now=1000.0 + 8 * 86400, home=HOME) is False


def test_no_prompt_for_lone_home_shell(cfg):
    s = one(pane(cwd=HOME, argv=""))
    assert should_prompt(s, cfg, now=1000.0 + 60, home=HOME) is False


def test_lone_home_shell_with_a_job_is_not_trivial(cfg):
    s = one(pane(cwd=HOME, argv="npm run dev"))
    assert is_trivial(s, home=HOME) is False


def test_two_panes_in_home_is_not_trivial(cfg):
    s = one(Split(False, [pane(cwd=HOME), pane(cwd=HOME)]))
    assert is_trivial(s, home=HOME) is False


def test_restore_prompt_off_disables_prompt(cfg):
    s = one(pane(argv="npm run dev"))
    assert should_prompt(s, Config(restore_prompt="off"), now=1060.0, home=HOME) is False


# --- tab titles -----------------------------------------------------------

from iterm_organizer.plan import clean_tab_title


def test_clean_tab_title_drops_itermss_auto_composed_name():
    """iTerm2 names a tab "<pane title> (<job>)"; restoring that pins a stale job."""
    assert clean_tab_title("my session (npm)", {"my session"}, {"npm"}) == ""


def test_clean_tab_title_drops_bare_shell():
    assert clean_tab_title("-zsh", set(), {"zsh"}) == ""
    assert clean_tab_title("bash", set(), set()) == ""


def test_clean_tab_title_drops_bare_job_name():
    assert clean_tab_title("mcp-server-darwi", set(), {"mcp-server-darwi"}) == ""


def test_clean_tab_title_keeps_a_user_chosen_name():
    assert clean_tab_title("backend", {"pane one"}, {"npm"}) == "backend"


def test_clean_tab_title_keeps_user_name_but_strips_job_suffix():
    assert clean_tab_title("backend (npm)", {"pane one"}, {"npm"}) == "backend"


def test_clean_tab_title_empty():
    assert clean_tab_title("", set(), set()) == ""
    assert clean_tab_title("(npm)", set(), set()) == ""


def test_build_plan_cleans_tab_titles(cfg):
    p = pane(title="my session", job_name="npm", argv="npm run dev")
    plan = build_plan(
        one(p, title="my session (npm)"), cfg, home=HOME, isdir=always
    )
    assert plan.windows[0].tabs[0].title == ""
