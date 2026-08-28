"""iTerm2 API glue: capture live state, and execute a restore plan.

Everything that carries logic lives in model/jobs/config/plan. This module is
deliberately thin so that the untestable part stays small.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import iterm2

from . import model, plan as plan_mod
from .config import Config
from .model import Pane, Snapshot, Split, Store, Tab, Window

log = logging.getLogger("iterm_organizer")


# --------------------------------------------------------------------------
# Capture
# --------------------------------------------------------------------------


async def _run(*argv: str, timeout: float = 2.0) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return out.decode("utf-8", "replace")
    except (OSError, asyncio.TimeoutError):
        return ""


async def process_table() -> dict[int, tuple[int, str]]:
    """One ps snapshot per capture, shared by every pane."""
    from .jobs import build_process_table

    return build_process_table(await _run("/bin/ps", "-axo", "pid=,ppid=,args="))


async def capture_pane(session, table: dict[int, tuple[int, str]] | None = None) -> Pane:
    async def var(name: str) -> Any:
        try:
            return await session.async_get_variable(name)
        except Exception:  # noqa: BLE001
            return None

    cwd = await var("path") or ""
    title = await var("autoName") or await var("name") or ""
    job_name = await var("jobName") or ""
    job_pid = _as_pid(await var("jobPid"))
    shell_pid = _as_pid(await var("pid"))

    from .jobs import find_foreground, is_passthrough, parse_ps_args

    argv = ""
    if table is not None and shell_pid:
        argv = find_foreground(table, shell_pid)
    if not argv and table is not None and job_pid:
        candidate = table.get(job_pid, (0, ""))[1]
        argv = "" if is_passthrough(candidate) else candidate
    if not argv and table is None and job_pid:
        candidate = parse_ps_args(
            await _run("/bin/ps", "-p", str(job_pid), "-o", "args=")
        )
        argv = "" if is_passthrough(candidate) else candidate

    # The underlying profile must come from the profileName variable.
    # Session.async_get_profile() returns a Profile whose .name is the SESSION's
    # name once anything has renamed it, which would both lose the real profile
    # and feed a nonexistent profile name to async_split_pane on restore.
    profile = await var("profileName") or ""
    return Pane(
        session_id=session.session_id,
        cwd=cwd,
        # iTerm2 falls back to the profile name when a pane has no real title.
        title="" if title == profile else title,
        profile=profile,
        job_name=job_name,
        job_pid=job_pid,
        argv=argv,
        git_branch=model.read_git_branch(cwd),
    )


def _as_pid(value: Any) -> int | None:
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


async def capture_node(node, table: dict[int, tuple[int, str]] | None = None) -> Any:
    """Serialize an iTerm2 Splitter/Session tree into our own node types.

    iTerm2 wraps even a single-session tab in a Splitter; those are unwrapped so
    a snapshot describes the layout the user actually sees.
    """
    children = getattr(node, "children", None)
    if children:
        encoded = [await capture_node(c, table) for c in children]
        if len(encoded) == 1:
            return encoded[0]
        return Split(vertical=bool(getattr(node, "vertical", False)), children=encoded)
    return await capture_pane(node, table)


async def capture(connection) -> Snapshot:
    app = await iterm2.async_get_app(connection)
    table = await process_table()
    snap = Snapshot(created_at=time.time())
    for window in app.terminal_windows:
        frame = None
        try:
            f = await window.async_get_frame()
            frame = [
                float(f.origin.x),
                float(f.origin.y),
                float(f.size.width),
                float(f.size.height),
            ]
        except Exception:  # noqa: BLE001
            frame = None
        tabs = []
        for tab in window.tabs:
            title = ""
            try:
                title = await tab.async_get_variable("title") or ""
            except Exception:  # noqa: BLE001
                title = ""
            tabs.append(Tab(title=title, root=await capture_node(tab.root, table)))
        if tabs:
            snap.windows.append(
                Window(window_id=str(window.window_id), frame=frame, tabs=tabs)
            )
    return snap


# --------------------------------------------------------------------------
# Restore
# --------------------------------------------------------------------------


async def _apply_setup(session, setup) -> None:
    if setup.title:
        try:
            await session.async_set_name(setup.title)
        except Exception:  # noqa: BLE001
            pass
    for text, enter in setup.shell_lines():
        await session.async_send_text(text + ("\n" if enter else ""))
        await asyncio.sleep(0.05)


async def _realize_node(node, session, errors: list[str]) -> None:
    """Build ``node`` starting from an already-existing ``session``."""
    if isinstance(node, plan_mod.PlanPane):
        try:
            await _apply_setup(session, node.setup)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"pane {node.setup.cwd}: {exc}")
        return

    sessions = [session]
    for child in node.children[1:]:
        try:
            profile = getattr(child, "profile", "") or None
            new = await sessions[-1].async_split_pane(
                vertical=node.vertical,
                profile=profile if isinstance(profile, str) and profile else None,
            )
            sessions.append(new)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"split failed: {type(exc).__name__}: {exc}")
            break
    for child, target in zip(node.children, sessions):
        await _realize_node(child, target, errors)


def _first_session(tab):
    """The session to start building a tab from.

    ``Tab.current_session`` resolves by matching ``active_session_id``, which
    iTerm2 has not populated yet on a freshly created window, so it returns
    None exactly when a restore needs it. The first session is what we want
    regardless.
    """
    sessions = list(getattr(tab, "sessions", []) or [])
    if sessions:
        return sessions[0]
    return getattr(tab, "current_session", None)


def _first_profile(node) -> str | None:
    if isinstance(node, plan_mod.PlanPane):
        return node.profile or None
    for child in node.children:
        found = _first_profile(child)
        if found:
            return found
    return None


async def realize(connection, plan) -> list[str]:
    """Execute a restore plan. Never aborts; collects and returns errors."""
    errors: list[str] = []
    # Session.async_split_pane asserts on a class-level delegate that only
    # async_get_app installs. Without this, every split fails.
    try:
        await iterm2.async_get_app(connection)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"could not attach to iTerm2: {exc}")
        return errors

    for window_plan in plan.windows:
        if not window_plan.tabs:
            continue
        try:
            window = await iterm2.Window.async_create(
                connection, profile=_first_profile(window_plan.tabs[0].root)
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"could not create window: {exc}")
            continue
        if window is None:
            errors.append("could not create window: iTerm2 returned nothing")
            continue

        if window_plan.frame:
            try:
                x, y, w, h = window_plan.frame
                await window.async_set_frame(
                    iterm2.Frame(iterm2.Point(x, y), iterm2.Size(w, h))
                )
            except Exception:  # noqa: BLE001
                pass  # geometry is cosmetic; never fail a restore over it

        for index, tab_plan in enumerate(window_plan.tabs):
            try:
                if index == 0:
                    tab = window.tabs[0]
                else:
                    tab = await window.async_create_tab(
                        profile=_first_profile(tab_plan.root)
                    )
                if tab is None:
                    errors.append(f"could not create tab {index + 1}")
                    continue
                session = _first_session(tab)
                if session is None:
                    errors.append(f"tab {index + 1} has no session to build into")
                    continue
                await _realize_node(tab_plan.root, session, errors)
                if tab_plan.title:
                    try:
                        await tab.async_set_title(tab_plan.title)
                    except Exception:  # noqa: BLE001
                        pass
            except Exception as exc:  # noqa: BLE001
                errors.append(f"tab {index + 1}: {exc}")
    return errors


# --------------------------------------------------------------------------
# Launch-time prompt
# --------------------------------------------------------------------------


async def _alert(connection, title: str, subtitle: str, buttons: list[str]) -> int:
    """Show an alert; returns the index of the button pressed, or -1 on failure."""
    try:
        alert = iterm2.Alert(title, subtitle)
        for label in buttons:
            alert.add_button(label)
        result = await alert.async_run(connection)
    except Exception as exc:  # noqa: BLE001
        log.warning("alert failed (%s); skipping restore prompt", exc)
        return -1
    # iTerm2 returns NSAlertFirstButtonReturn (1000) for the first button.
    index = int(result) - 1000
    return index if 0 <= index < len(buttons) else -1


async def prompt_and_restore(connection, snapshot: Snapshot, config: Config) -> None:
    total_panes = model.count_panes(snapshot)
    age = _describe_age(time.time() - snapshot.created_at)
    subtitle = (
        f"{len(snapshot.windows)} window(s), {total_panes} pane(s), saved {age}."
    )

    if config.restore_prompt == "auto":
        choice = 0
    else:
        choice = await _alert(
            connection,
            "Restore your previous terminal sessions?",
            subtitle,
            ["Restore all", "Choose…", "Skip"],
        )

    if choice < 0 or choice == 2:
        log.info("restore declined or unavailable")
        return

    only: list[int] | None = None
    if choice == 1:
        only = []
        for index, window in enumerate(snapshot.windows):
            panes = sum(model.count_panes_in(t.root) for t in window.tabs)
            dirs = _window_summary(window)
            keep = await _alert(
                connection,
                f"Restore window {index + 1} of {len(snapshot.windows)}?",
                f"{len(window.tabs)} tab(s), {panes} pane(s): {dirs}",
                ["Restore", "Skip"],
            )
            if keep == 0:
                only.append(index)
        if not only:
            log.info("no windows selected")
            return

    restore_plan = plan_mod.build_plan(snapshot, config, only_windows=only)
    if restore_plan.is_empty:
        return
    errors = await realize(connection, restore_plan)
    for warning in restore_plan.warnings:
        log.warning(warning)
    if errors or restore_plan.warnings:
        for err in errors:
            log.error("restore: %s", err)
        await _alert(
            connection,
            "Restore finished with problems",
            "\n".join(restore_plan.warnings + errors)[:900],
            ["OK"],
        )


def _window_summary(window: Window, limit: int = 3) -> str:
    home = os.path.expanduser("~")
    seen: list[str] = []
    for tab in window.tabs:
        for node in model.iter_nodes(tab.root):
            if isinstance(node, Pane) and node.cwd:
                short = plan_mod.tilde(node.cwd, home)
                if short not in seen:
                    seen.append(short)
    extra = len(seen) - limit
    text = ", ".join(seen[:limit])
    return text + (f" (+{extra} more)" if extra > 0 else "")


def _describe_age(seconds: float) -> str:
    if seconds < 120:
        return "moments ago"
    if seconds < 7200:
        return f"{int(seconds // 60)} minutes ago"
    if seconds < 172800:
        return f"{int(seconds // 3600)} hours ago"
    return f"{int(seconds // 86400)} days ago"


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------


async def capture_loop(connection, store: Store, config: Config, gate: asyncio.Event):
    while True:
        await asyncio.sleep(config.snapshot_interval)
        if not gate.is_set():
            continue  # a restore is in flight; do not snapshot a half-built world
        try:
            snap = await capture(connection)
            if store.save(snap, keep=config.keep_snapshots):
                log.info(
                    "snapshot: %d window(s), %d pane(s)",
                    len(snap.windows),
                    model.count_panes(snap),
                )
        except Exception as exc:  # noqa: BLE001
            log.error("capture failed: %s", exc)


async def main(connection, store: Store | None = None, config: Config | None = None):
    store = store or Store()
    store.ensure()
    if config is None:
        from .config import load_config

        config = load_config(store.config)

    gate = asyncio.Event()
    gate.set()

    snapshot, note = store.load()
    if note:
        log.warning(note)

    if plan_mod.should_prompt(snapshot, config) and await _launch_is_fresh(connection):
        gate.clear()
        try:
            await prompt_and_restore(connection, snapshot, config)
        except Exception as exc:  # noqa: BLE001
            log.error("restore failed: %s", exc)
        finally:
            gate.set()

    await capture_loop(connection, store, config, gate)


async def _launch_is_fresh(connection) -> bool:
    """True only if iTerm2 looks freshly started, not mid-session.

    Guards against re-running a restore when the script is merely reloaded
    while the user already has work open.
    """
    try:
        app = await iterm2.async_get_app(connection)
    except Exception:  # noqa: BLE001
        return False
    windows = list(app.terminal_windows)
    if len(windows) > 1:
        return False
    if not windows:
        return True
    tabs = list(windows[0].tabs)
    if len(tabs) != 1:
        return False
    return len(list(model.iter_nodes(await capture_node(tabs[0].root)))) == 1
