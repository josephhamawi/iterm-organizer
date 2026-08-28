"""Snapshot data model, JSON round-trip, and atomic on-disk storage.

Pure stdlib. Nothing here imports iterm2, so the whole module is unit-testable
without iTerm2 running.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

SCHEMA_VERSION = 1


@dataclass
class Pane:
    """A single leaf session inside a tab's split tree."""

    session_id: str = ""
    cwd: str = ""
    title: str = ""
    profile: str = ""
    job_name: str = ""
    job_pid: int | None = None
    argv: str = ""
    git_branch: str = ""


@dataclass
class Split:
    """An internal node of a tab's split tree.

    ``vertical`` follows iTerm2's own meaning: a vertical split divides the
    space left-to-right.
    """

    vertical: bool = False
    children: list[Any] = field(default_factory=list)


@dataclass
class Tab:
    title: str = ""
    root: Any = None


@dataclass
class Window:
    window_id: str = ""
    frame: list[float] | None = None
    tabs: list[Tab] = field(default_factory=list)


@dataclass
class Snapshot:
    created_at: float = 0.0
    version: int = SCHEMA_VERSION
    windows: list[Window] = field(default_factory=list)


# --------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------


def encode_node(node: Any) -> dict:
    """Encode a Pane or Split into a type-tagged dict."""
    if isinstance(node, Split):
        return {
            "type": "split",
            "vertical": bool(node.vertical),
            "children": [encode_node(c) for c in node.children],
        }
    if isinstance(node, Pane):
        return {
            "type": "pane",
            "session_id": node.session_id,
            "cwd": node.cwd,
            "title": node.title,
            "profile": node.profile,
            "job_name": node.job_name,
            "job_pid": node.job_pid,
            "argv": node.argv,
            "git_branch": node.git_branch,
        }
    raise TypeError(f"cannot encode node of type {type(node).__name__}")


def decode_node(data: dict) -> Any:
    kind = data.get("type")
    if kind == "split":
        return Split(
            vertical=bool(data.get("vertical", False)),
            children=[decode_node(c) for c in data.get("children", [])],
        )
    if kind == "pane":
        return Pane(
            session_id=data.get("session_id", ""),
            cwd=data.get("cwd", ""),
            title=data.get("title", ""),
            profile=data.get("profile", ""),
            job_name=data.get("job_name", ""),
            job_pid=data.get("job_pid"),
            argv=data.get("argv", ""),
            git_branch=data.get("git_branch", ""),
        )
    raise ValueError(f"unknown node type: {kind!r}")


def to_dict(snap: Snapshot) -> dict:
    return {
        "version": snap.version,
        "created_at": snap.created_at,
        "windows": [
            {
                "window_id": w.window_id,
                "frame": w.frame,
                "tabs": [
                    {"title": t.title, "root": encode_node(t.root)} for t in w.tabs
                ],
            }
            for w in snap.windows
        ],
    }


def from_dict(data: dict) -> Snapshot:
    version = int(data.get("version", 0))
    if version > SCHEMA_VERSION:
        raise ValueError(
            f"snapshot schema version {version} is newer than supported "
            f"version {SCHEMA_VERSION}"
        )
    return Snapshot(
        created_at=float(data.get("created_at", 0.0)),
        version=version or SCHEMA_VERSION,
        windows=[
            Window(
                window_id=w.get("window_id", ""),
                frame=w.get("frame"),
                tabs=[
                    Tab(title=t.get("title", ""), root=decode_node(t["root"]))
                    for t in w.get("tabs", [])
                ],
            )
            for w in data.get("windows", [])
        ],
    )


def dumps(snap: Snapshot) -> str:
    return json.dumps(to_dict(snap), indent=2, sort_keys=True, ensure_ascii=False)


def loads(text: str) -> Snapshot:
    return from_dict(json.loads(text))


# --------------------------------------------------------------------------
# Traversal helpers
# --------------------------------------------------------------------------


def iter_nodes(node: Any) -> Iterator[Any]:
    yield node
    if isinstance(node, Split):
        for child in node.children:
            yield from iter_nodes(child)


def panes(snap: Snapshot) -> Iterator[Pane]:
    for window in snap.windows:
        for tab in window.tabs:
            for node in iter_nodes(tab.root):
                if isinstance(node, Pane):
                    yield node


def count_panes(snap: Snapshot) -> int:
    return sum(1 for _ in panes(snap))


def count_panes_in(node: Any) -> int:
    return sum(1 for n in iter_nodes(node) if isinstance(n, Pane))


def content_hash(snap: Snapshot) -> str:
    """Hash of everything except the timestamp, so identical layouts collapse."""
    payload = to_dict(snap)
    payload.pop("created_at", None)
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()


# --------------------------------------------------------------------------
# Git context
# --------------------------------------------------------------------------


def read_git_branch(cwd: str, *, max_depth: int = 40) -> str:
    """Return the branch name (or short SHA) for ``cwd``, or "" if not a repo.

    Reads .git/HEAD directly rather than shelling out to git, so sweeping
    dozens of panes every 60 seconds stays cheap.
    """
    if not cwd:
        return ""
    path = os.path.abspath(os.path.expanduser(cwd))
    for _ in range(max_depth):
        git_path = os.path.join(path, ".git")
        head = _head_file(git_path)
        if head:
            return _parse_head(head)
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return ""


def _head_file(git_path: str) -> str:
    """Resolve .git (directory or gitfile) to the contents of its HEAD."""
    try:
        if os.path.isdir(git_path):
            with open(os.path.join(git_path, "HEAD"), encoding="utf-8") as fh:
                return fh.read()
        if os.path.isfile(git_path):
            # A worktree or submodule: ".git" is a file pointing elsewhere.
            with open(git_path, encoding="utf-8") as fh:
                content = fh.read().strip()
            if content.startswith("gitdir:"):
                target = content.split(":", 1)[1].strip()
                if not os.path.isabs(target):
                    target = os.path.join(os.path.dirname(git_path), target)
                with open(os.path.join(target, "HEAD"), encoding="utf-8") as fh:
                    return fh.read()
    except (OSError, UnicodeDecodeError):
        return ""
    return ""


def _parse_head(text: str) -> str:
    head = text.strip()
    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        return ref.rsplit("/", 1)[-1] if ref else ""
    return head[:8] if head else ""


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

DEFAULT_STORE = os.path.expanduser("~/.iterm-organizer")


def write_atomic(path: str, text: str) -> None:
    """Write ``text`` to ``path`` such that a crash cannot leave it torn."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Store:
    """Filesystem layout for snapshots."""

    def __init__(self, root: str = DEFAULT_STORE):
        self.root = os.path.expanduser(root)
        self.current = os.path.join(self.root, "snapshot.json")
        self.previous = os.path.join(self.root, "snapshot.prev.json")
        self.sessions = os.path.join(self.root, "sessions")
        self.config = os.path.join(self.root, "config.toml")
        self.log = os.path.join(self.root, "organizer.log")

    def ensure(self) -> None:
        os.makedirs(self.sessions, exist_ok=True)

    def save(self, snap: Snapshot, keep: int = 20) -> bool:
        """Persist ``snap``. Returns True if it was archived as a new state.

        An empty snapshot is never written: it would otherwise overwrite a
        good one during shutdown, which is exactly when it matters most.
        """
        if not snap.windows:
            return False
        self.ensure()
        previous = self.load_file(self.current)
        text = dumps(snap)
        if os.path.exists(self.current):
            try:
                write_atomic(self.previous, open(self.current, encoding="utf-8").read())
            except OSError:
                pass
        write_atomic(self.current, text)
        changed = previous is None or content_hash(previous) != content_hash(snap)
        if changed:
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(snap.created_at))
            write_atomic(os.path.join(self.sessions, f"{stamp}.json"), text)
            self.rotate(keep)
        return changed

    def rotate(self, keep: int) -> None:
        try:
            files = sorted(
                f for f in os.listdir(self.sessions) if f.endswith(".json")
            )
        except OSError:
            return
        for name in files[: max(0, len(files) - keep)]:
            try:
                os.unlink(os.path.join(self.sessions, name))
            except OSError:
                pass

    def load_file(self, path: str) -> Snapshot | None:
        try:
            with open(path, encoding="utf-8") as fh:
                return loads(fh.read())
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def load(self) -> tuple[Snapshot | None, str]:
        """Load the newest usable snapshot.

        Returns (snapshot, note) where note describes any fallback taken.
        """
        snap = self.load_file(self.current)
        if snap is not None:
            return snap, ""
        if os.path.exists(self.current):
            snap = self.load_file(self.previous)
            if snap is not None:
                return snap, "snapshot.json unreadable; used snapshot.prev.json"
            return None, "both snapshot.json and snapshot.prev.json are unreadable"
        return None, "no snapshot yet"

    def list_sessions(self) -> list[str]:
        try:
            return sorted(
                os.path.join(self.sessions, f)
                for f in os.listdir(self.sessions)
                if f.endswith(".json")
            )
        except OSError:
            return []
