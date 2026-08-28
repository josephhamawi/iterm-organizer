import plistlib

import pytest

from iterm_organizer import agent


def test_plist_runs_the_program_in_daemon_mode():
    d = agent.plist_dict("/Applications/X.app/Contents/MacOS/X", "/tmp/store")
    assert d["ProgramArguments"] == ["/Applications/X.app/Contents/MacOS/X", "--daemon"]


def test_plist_keeps_the_agent_alive_only_on_failure():
    """Exit 0 means another instance holds the lock: do not respawn in a loop."""
    d = agent.plist_dict("/x", "/tmp/store")
    assert d["KeepAlive"] == {"SuccessfulExit": False}
    assert d["RunAtLoad"] is True
    assert d["ThrottleInterval"] >= 1


def test_plist_attributes_the_agent_to_iterm2():
    """Without this the AppleScript cookie request blocks on a TCC prompt
    that never surfaces for a launchd job."""
    d = agent.plist_dict("/x", "/tmp/store")
    assert d["AssociatedBundleIdentifiers"] == ["com.googlecode.iterm2"]


def test_plist_logs_into_the_store():
    d = agent.plist_dict("/x", "/tmp/store")
    assert d["StandardOutPath"] == "/tmp/store/launchd.out.log"
    assert d["StandardErrorPath"] == "/tmp/store/launchd.err.log"


def test_plist_xml_round_trips():
    xml = agent.plist_xml("/Applications/My App.app/Contents/MacOS/My App", "/tmp/s")
    parsed = plistlib.loads(xml)
    assert parsed["ProgramArguments"][0].endswith("/MacOS/My App")
    assert parsed["Label"] == agent.LABEL


def test_domain_is_a_gui_domain():
    assert agent.domain("com.example.x").startswith("gui/")
    assert agent.domain("com.example.x").endswith("/com.example.x")


# --- launchctl output parsing --------------------------------------------

LAUNCHCTL_OUTPUT = """\
com.rami.iterm-organizer = {
	active count = 1
	state = running
	pid = 8255
	last exit code = (never exited)
	endpoints = {
		state = active
	}
}
"""


def test_parse_launchctl_print():
    fields = agent.parse_launchctl_print(LAUNCHCTL_OUTPUT)
    assert fields["state"] == "running"
    assert fields["pid"] == "8255"
    assert fields["last exit code"] == "(never exited)"


def test_parse_launchctl_print_takes_the_first_state():
    """The nested endpoints block also has a `state` key."""
    assert agent.parse_launchctl_print(LAUNCHCTL_OUTPUT)["state"] == "running"


def test_parse_launchctl_print_on_garbage():
    assert agent.parse_launchctl_print("could not find service") == {}


# --- status rendering -----------------------------------------------------


def test_describe_status_running():
    text = agent.describe_status(
        {
            "loaded": True, "state": "running", "pid": "42", "note": "",
            "windows": 4, "panes": 7, "captured_at": 1_700_000_000.0, "snapshots": 20,
        }
    )
    assert "Running (pid 42)" in text
    assert "4 window(s), 7 pane(s)" in text
    assert "20 snapshot(s) kept" in text


def test_describe_status_not_installed():
    text = agent.describe_status(
        {"loaded": False, "state": "", "pid": "", "note": "", "windows": 0,
         "panes": 0, "captured_at": 0.0, "snapshots": 0}
    )
    assert "Not installed" in text
    assert "No snapshot captured yet" in text


def test_describe_status_installed_but_stopped():
    text = agent.describe_status(
        {"loaded": True, "state": "waiting", "pid": "", "note": "", "windows": 0,
         "panes": 0, "captured_at": 0.0, "snapshots": 3}
    )
    assert "not running" in text


# --- applescript escaping -------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('say "hi"', '"say \\"hi\\""'),
        ("back\\slash", '"back\\\\slash"'),
        ("two\nlines", '"two\\nlines"'),
    ],
)
def test_applescript_escaping(raw, expected):
    """A snapshot path or error text must not be able to break out of the
    AppleScript string literal."""
    assert agent._as_applescript(raw) == expected
