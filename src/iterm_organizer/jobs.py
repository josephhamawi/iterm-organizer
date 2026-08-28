"""Recovering, normalizing, and classifying the command running in a pane.

Replaying a command read out of the process table is code execution at login.
Every guard that keeps that safe lives here, and every one of them is tested:

* only an exact ``autorun`` allowlist match is ever executed;
* ``never`` is evaluated before ``autorun``, so it always wins;
* any command carrying a newline or control character is dropped outright,
  which stops a crafted process name from smuggling in a second command;
* everything unrecognized is typed into the pane but not run.
"""

from __future__ import annotations

import os
import re
import shlex
from enum import Enum

SHELLS = frozenset(
    {"sh", "bash", "zsh", "fish", "dash", "tcsh", "csh", "ksh", "login"}
)

# Interpreters worth stripping when they merely front a known launcher script.
INTERPRETERS = frozenset({"node", "python", "python3", "ruby", "sh", "bash", "bun"})

LAUNCHERS = frozenset(
    {
        "npm", "npx", "yarn", "pnpm", "vite", "next", "expo", "tsx", "ts-node",
        "nodemon", "webpack", "jest", "vitest", "eslint", "serve", "uvicorn",
        "gunicorn", "flask", "celery", "storybook",
    }
)

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# Wrappers iTerm2 launches a session through; they are never the user's command.
LAUNCH_WRAPPERS = frozenset({"login", "su", "env"})

CONTINUE_FLAGS = frozenset({"--continue", "-c", "--resume", "-r"})


class Tier(Enum):
    """What restore should do with a recovered command."""

    AUTORUN = "autorun"  # execute it
    NEVER = "never"      # mention it in the banner, nothing more
    TYPE = "type"        # type it into the pane without pressing Enter
    DROP = "drop"        # ignore it entirely


def parse_ps_args(output: str) -> str:
    """Turn ``ps -o args=`` output into a single command line."""
    return " ".join(output.split())


def program_name(command: str) -> str:
    """Basename of the executable in ``command``, minus any login dash."""
    if not command.strip():
        return ""
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    if not parts:
        return ""
    return os.path.basename(parts[0]).lstrip("-")


def is_shell(name: str) -> bool:
    """True if ``name`` is an interactive shell, i.e. the pane is idle."""
    if not name:
        return False
    return os.path.basename(name).lstrip("-") in SHELLS


def normalize_argv(argv: str) -> str:
    """Reduce a process-table command line to what the user would have typed.

    ``/usr/local/bin/node /usr/local/bin/npm run dev`` becomes ``npm run dev``.
    Unrecognized shapes are returned with whitespace collapsed and nothing else
    changed.
    """
    if not argv or not argv.strip():
        return ""
    try:
        parts = shlex.split(argv)
    except ValueError:
        parts = argv.split()
    if not parts:
        return ""

    first = os.path.basename(parts[0]).lstrip("-")
    if (
        first in INTERPRETERS
        and len(parts) > 1
        and os.sep in parts[1]
        and os.path.basename(parts[1]) in LAUNCHERS
    ):
        parts = [os.path.basename(parts[1])] + parts[2:]
    elif os.sep in parts[0] and first in LAUNCHERS:
        parts = [first] + parts[1:]

    return shlex.join(parts)


def rewrite_claude(command: str) -> str:
    """Restore a Claude Code pane as a resumed session rather than a fresh one."""
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if not parts or os.path.basename(parts[0]) != "claude":
        return command
    if any(arg in CONTINUE_FLAGS for arg in parts[1:]):
        return command
    return shlex.join(parts + ["--continue"])


def classify(argv: str, config) -> tuple[Tier, str]:
    """Classify a recovered command line into a tier and its replay text."""
    if not argv or not argv.strip():
        return Tier.DROP, ""
    # Reject on the RAW string, before any normalization can hide something.
    if "\n" in argv or "\r" in argv or _CONTROL.search(argv):
        return Tier.DROP, ""

    command = normalize_argv(argv)
    if not command:
        return Tier.DROP, ""
    if is_shell(program_name(command)):
        return Tier.DROP, ""

    if any(p.search(command) for p in config.never_patterns):
        return Tier.NEVER, command
    if any(p.search(command) for p in config.autorun_patterns):
        return Tier.AUTORUN, rewrite_claude(command)
    return Tier.TYPE, command


# --------------------------------------------------------------------------
# Process tree
# --------------------------------------------------------------------------


def build_process_table(ps_output: str) -> dict[int, tuple[int, str]]:
    """Parse ``ps -axo pid=,ppid=,args=`` into ``{pid: (ppid, argv)}``.

    One ps call per capture beats one per pane; a sweep over dozens of panes
    every 60 seconds cannot afford a subprocess each.
    """
    table: dict[int, tuple[int, str]] = {}
    for line in ps_output.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        table[pid] = (ppid, parts[2].strip() if len(parts) > 2 else "")
    return table


def _children(table: dict[int, tuple[int, str]]) -> dict[int, list[int]]:
    kids: dict[int, list[int]] = {}
    for pid, (ppid, _) in table.items():
        kids.setdefault(ppid, []).append(pid)
    for values in kids.values():
        values.sort()
    return kids


def is_passthrough(command: str) -> bool:
    """True for a shell or a launcher wrapper, i.e. not the user's command."""
    name = program_name(command)
    return not name or name in SHELLS or name in LAUNCH_WRAPPERS


def find_foreground(
    table: dict[int, tuple[int, str]], root_pid: int, *, max_depth: int = 8
) -> str:
    """The shallowest non-shell descendant of ``root_pid``.

    iTerm2's ``jobPid`` points at the *deepest* foreground process, which for a
    Claude Code pane is one of the MCP helpers it spawned rather than ``claude``
    itself. Searching downward from the session's own pid and stopping at the
    first real command recovers what the user actually typed.
    """
    if root_pid not in table:
        return ""
    kids = _children(table)
    frontier = [root_pid]
    depth = 0
    while frontier and depth < max_depth:
        nxt: list[int] = []
        for pid in frontier:
            for child in kids.get(pid, []):
                command = table.get(child, (0, ""))[1]
                if command and not is_passthrough(command):
                    return command
                nxt.append(child)
        frontier = nxt
        depth += 1
    return ""
