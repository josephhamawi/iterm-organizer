"""Turn a snapshot into a declarative restore plan.

This module is the seam that makes restore testable: it produces an ordered,
side-effect-free description of what to build, and ``daemon.py`` does nothing
but execute it.
"""

from __future__ import annotations

import os
import re
import shlex
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import model
from .jobs import SHELLS, Tier, classify, program_name
from .model import Pane, Snapshot, Split


@dataclass
class PaneSetup:
    """Everything to do inside one pane once it exists."""

    cwd: str
    original_cwd: str
    cwd_missing: bool
    title: str
    banner: str
    command: str
    execute: bool

    def shell_lines(self) -> list[tuple[str, bool]]:
        """(text, press_enter) pairs to send to the pane, in order."""
        lines: list[tuple[str, bool]] = []
        if self.cwd:
            lines.append((f"cd {shlex.quote(self.cwd)}", True))
        if self.banner:
            lines.append((f"printf '%s\\n' {shlex.quote(self.banner)}", True))
        if self.command:
            lines.append((self.command, self.execute))
        return lines


@dataclass
class PlanPane:
    setup: PaneSetup
    profile: str = ""


@dataclass
class PlanSplit:
    vertical: bool
    children: list[Any] = field(default_factory=list)


@dataclass
class PlanTab:
    title: str
    root: Any


@dataclass
class PlanWindow:
    frame: list[float] | None
    tabs: list[PlanTab] = field(default_factory=list)

    @property
    def pane_count(self) -> int:
        return sum(_count(t.root) for t in self.tabs)


@dataclass
class RestorePlan:
    windows: list[PlanWindow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pane_count: int = 0
    skipped_panes: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.windows


def _count(node: Any) -> int:
    if isinstance(node, PlanSplit):
        return sum(_count(c) for c in node.children)
    return 1


def tilde(path: str, home: str) -> str:
    """Render an absolute path with ``$HOME`` collapsed to ``~``."""
    if not path:
        return ""
    if path == home:
        return "~"
    if home and path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def build_banner(pane: Pane, home: str, missing: bool, note: str) -> str:
    """One line of context printed into the restored pane."""
    parts: list[str] = []
    if missing:
        parts.append(f"! {tilde(pane.cwd, home)} is gone - opened in ~")
    else:
        parts.append(tilde(pane.cwd, home))
    if pane.git_branch:
        parts.append(pane.git_branch)
    if note:
        parts.append(note)
    return " · ".join(p for p in parts if p)


def build_setup(
    pane: Pane,
    config,
    home: str,
    isdir: Callable[[str], bool],
) -> PaneSetup:
    missing = bool(pane.cwd) and not isdir(os.path.expanduser(pane.cwd))
    cwd = home if (missing or not pane.cwd) else pane.cwd

    tier, command = classify(pane.argv, config)

    # A command is only ever sent into a directory that actually exists.
    if missing and tier in (Tier.AUTORUN, Tier.TYPE):
        tier, note_command = Tier.NEVER, command
    else:
        note_command = command

    if tier is Tier.AUTORUN:
        send, execute, note = command, True, f"was: {command}"
    elif tier is Tier.TYPE:
        send, execute, note = command, False, f"was: {command}"
    elif tier is Tier.NEVER:
        send, execute, note = "", False, f"was: {note_command} (not restored)"
    else:
        send, execute, note = "", False, ""

    return PaneSetup(
        cwd=cwd,
        original_cwd=pane.cwd,
        cwd_missing=missing,
        title=pane.title,
        banner=build_banner(pane, home, missing, note),
        command=send,
        execute=execute,
    )


_TRAILING_PAREN = re.compile(r"\s*\([^()]*\)\s*$")


def clean_tab_title(title: str, pane_titles: set[str], job_names: set[str]) -> str:
    """Drop tab titles iTerm2 composed itself.

    iTerm2 names a tab after its pane and the job running in it, e.g.
    "my session (npm)". Restoring that verbatim would pin a stale job name to
    the tab forever, so only titles the user actually chose survive.
    """
    if not title:
        return ""
    stripped = _TRAILING_PAREN.sub("", title).strip()
    if not stripped or stripped in pane_titles:
        return ""
    bare = stripped.lstrip("-")
    if program_name(stripped) in SHELLS or bare in job_names:
        return ""
    return stripped


def _tab_context(node: Any) -> tuple[set[str], set[str]]:
    titles: set[str] = set()
    jobs: set[str] = set()
    for item in model.iter_nodes(node):
        if isinstance(item, Pane):
            if item.title:
                titles.add(item.title)
            if item.job_name:
                jobs.add(item.job_name)
    return titles, jobs


def _build_node(node: Any, config, home: str, isdir) -> Any:
    if isinstance(node, Split):
        return PlanSplit(
            vertical=node.vertical,
            children=[_build_node(c, config, home, isdir) for c in node.children],
        )
    return PlanPane(setup=build_setup(node, config, home, isdir), profile=node.profile)


def build_plan(
    snapshot: Snapshot,
    config,
    *,
    home: str | None = None,
    isdir: Callable[[str], bool] = os.path.isdir,
    only_windows: list[int] | None = None,
) -> RestorePlan:
    """Build a restore plan, honouring the pane budget.

    Tabs are admitted whole. Once the budget cannot fit the next tab, that tab
    and everything after it is dropped and counted in ``skipped_panes`` - a
    truncated restore is never silent.
    """
    home = home if home is not None else os.path.expanduser("~")
    plan = RestorePlan()
    budget = max(0, config.max_restore_panes)
    used = 0
    skipped = 0
    truncated = False

    for index, window in enumerate(snapshot.windows):
        if only_windows is not None and index not in only_windows:
            skipped += sum(model.count_panes_in(t.root) for t in window.tabs)
            continue
        plan_window = PlanWindow(frame=window.frame)
        for tab in window.tabs:
            size = model.count_panes_in(tab.root)
            if used + size > budget:
                skipped += size
                truncated = True
                continue
            titles, job_names = _tab_context(tab.root)
            plan_window.tabs.append(
                PlanTab(
                    title=clean_tab_title(tab.title, titles, job_names),
                    root=_build_node(tab.root, config, home, isdir),
                )
            )
            used += size
        if plan_window.tabs:
            plan.windows.append(plan_window)

    plan.pane_count = used
    plan.skipped_panes = skipped
    if truncated:
        plan.warnings.append(
            f"pane budget is {budget}; {skipped} pane(s) were not restored. "
            f"Raise max_restore_panes in config.toml to include them."
        )
    return plan


def should_prompt(
    snapshot: Snapshot | None,
    config,
    *,
    now: float | None = None,
    home: str | None = None,
) -> bool:
    """Whether a snapshot is worth interrupting the user about."""
    if snapshot is None or not snapshot.windows:
        return False
    if config.restore_prompt == "off":
        return False
    now = now if now is not None else time.time()
    age_days = (now - snapshot.created_at) / 86400.0
    if snapshot.created_at <= 0 or age_days > config.stale_days:
        return False
    return not is_trivial(snapshot, home=home)


def is_trivial(snapshot: Snapshot, *, home: str | None = None) -> bool:
    """A lone shell sitting in $HOME is not worth restoring."""
    home = home if home is not None else os.path.expanduser("~")
    if len(snapshot.windows) != 1:
        return False
    window = snapshot.windows[0]
    if len(window.tabs) != 1:
        return False
    all_panes = list(model.panes(snapshot))
    if len(all_panes) != 1:
        return False
    pane = all_panes[0]
    return pane.cwd in ("", home) and not pane.argv.strip()


def render_plan(plan: RestorePlan) -> str:
    """Human-readable plan for ``--dry-run``."""
    out: list[str] = []
    if plan.is_empty:
        out.append("Nothing to restore.")
    for w_index, window in enumerate(plan.windows, start=1):
        frame = f"  frame={window.frame}" if window.frame else ""
        out.append(f"Window {w_index} ({window.pane_count} pane(s)){frame}")
        for t_index, tab in enumerate(window.tabs, start=1):
            out.append(f"  Tab {t_index}: {tab.title or '(untitled)'}")
            out.extend(_render_node(tab.root, depth=2))
    for warning in plan.warnings:
        out.append(f"WARNING: {warning}")
    out.append(
        f"Total: {len(plan.windows)} window(s), {plan.pane_count} pane(s)"
        + (f", {plan.skipped_panes} skipped" if plan.skipped_panes else "")
    )
    return "\n".join(out)


def _render_node(node: Any, depth: int) -> list[str]:
    pad = "  " * depth
    if isinstance(node, PlanSplit):
        axis = "vertical (left|right)" if node.vertical else "horizontal (top/bottom)"
        lines = [f"{pad}split {axis}"]
        for child in node.children:
            lines.extend(_render_node(child, depth + 1))
        return lines
    setup = node.setup
    lines = [f"{pad}pane {setup.cwd}" + (f"  [{node.profile}]" if node.profile else "")]
    if setup.title:
        lines.append(f"{pad}  title: {setup.title}")
    for text, enter in setup.shell_lines():
        marker = "RUN  " if enter else "TYPE "
        lines.append(f"{pad}  {marker}{text}")
    return lines
