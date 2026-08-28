import json
import os
import time

import pytest

from iterm_organizer import model
from iterm_organizer.model import Pane, Snapshot, Split, Store, Tab, Window


def pane(**kw):
    base = dict(session_id="s1", cwd="/tmp", title="t", profile="Default")
    base.update(kw)
    return Pane(**base)


def snap(*windows):
    return Snapshot(created_at=1000.0, windows=list(windows))


def test_round_trip_single_pane():
    original = snap(Window("w1", [0, 0, 800, 600], [Tab("tab", pane())]))
    assert model.loads(model.dumps(original)) == original


def test_round_trip_nested_splits():
    tree = Split(
        vertical=True,
        children=[
            pane(session_id="a"),
            Split(vertical=False, children=[pane(session_id="b"), pane(session_id="c")]),
        ],
    )
    original = snap(Window("w1", None, [Tab("t", tree)]))
    restored = model.loads(model.dumps(original))
    assert restored == original
    assert model.count_panes(restored) == 3


def test_round_trip_deeply_nested():
    node = pane(session_id="leaf")
    for depth in range(10):
        node = Split(vertical=depth % 2 == 0, children=[pane(session_id=f"p{depth}"), node])
    original = snap(Window("w", None, [Tab("t", node)]))
    assert model.loads(model.dumps(original)) == original
    assert model.count_panes(original) == 11


def test_decode_rejects_unknown_node_type():
    with pytest.raises(ValueError):
        model.decode_node({"type": "wormhole"})


def test_from_dict_rejects_future_schema():
    with pytest.raises(ValueError, match="newer than supported"):
        model.from_dict({"version": model.SCHEMA_VERSION + 1, "windows": []})


def test_content_hash_ignores_timestamp():
    a = snap(Window("w", None, [Tab("t", pane())]))
    b = snap(Window("w", None, [Tab("t", pane())]))
    b.created_at = 999999.0
    assert model.content_hash(a) == model.content_hash(b)


def test_content_hash_changes_with_layout():
    a = snap(Window("w", None, [Tab("t", pane())]))
    b = snap(Window("w", None, [Tab("t", pane(cwd="/other"))]))
    assert model.content_hash(a) != model.content_hash(b)


# --- git branch -----------------------------------------------------------


def test_read_git_branch_from_head(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/feature/login\n")
    assert model.read_git_branch(str(repo)) == "login"


def test_read_git_branch_walks_up_from_subdir(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    deep = repo / "a" / "b" / "c"
    deep.mkdir(parents=True)
    assert model.read_git_branch(str(deep)) == "main"


def test_read_git_branch_detached_head(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("9f1c2ab3d4e5f60718293a4b5c6d7e8f90a1b2c3\n")
    assert model.read_git_branch(str(repo)) == "9f1c2ab3"


def test_read_git_branch_gitfile_worktree(tmp_path):
    real = tmp_path / "realgit"
    real.mkdir()
    (real / "HEAD").write_text("ref: refs/heads/wt\n")
    work = tmp_path / "work"
    work.mkdir()
    (work / ".git").write_text(f"gitdir: {real}\n")
    assert model.read_git_branch(str(work)) == "wt"


def test_read_git_branch_outside_repo(tmp_path):
    assert model.read_git_branch(str(tmp_path)) == ""
    assert model.read_git_branch("") == ""


# --- store ----------------------------------------------------------------


def test_write_atomic_replaces_file(tmp_path):
    target = tmp_path / "x.json"
    model.write_atomic(str(target), "one")
    model.write_atomic(str(target), "two")
    assert target.read_text() == "two"
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".tmp-")]


def test_store_save_and_load(tmp_path):
    store = Store(str(tmp_path))
    s = snap(Window("w", None, [Tab("t", pane())]))
    assert store.save(s) is True
    loaded, note = store.load()
    assert note == ""
    assert loaded == s
    assert len(store.list_sessions()) == 1


def test_store_does_not_archive_identical_snapshots(tmp_path):
    store = Store(str(tmp_path))
    for offset in range(3):
        s = snap(Window("w", None, [Tab("t", pane())]))
        s.created_at = 1000.0 + offset
        store.save(s)
    assert len(store.list_sessions()) == 1


def test_store_refuses_to_save_empty_snapshot(tmp_path):
    """A zero-window snapshot at shutdown must not clobber a good one."""
    store = Store(str(tmp_path))
    good = snap(Window("w", None, [Tab("t", pane())]))
    store.save(good)
    assert store.save(Snapshot(created_at=2000.0, windows=[])) is False
    loaded, _ = store.load()
    assert loaded == good


def test_store_falls_back_to_previous_on_corruption(tmp_path):
    store = Store(str(tmp_path))
    first = snap(Window("w1", None, [Tab("t", pane(cwd="/first"))]))
    store.save(first)
    second = snap(Window("w2", None, [Tab("t", pane(cwd="/second"))]))
    second.created_at = 2000.0
    store.save(second)
    with open(store.current, "w") as fh:
        fh.write("{ this is not json")
    loaded, note = store.load()
    assert loaded is not None
    assert list(model.panes(loaded))[0].cwd == "/first"
    assert "snapshot.prev.json" in note


def test_store_reports_when_both_files_are_unreadable(tmp_path):
    store = Store(str(tmp_path))
    store.save(snap(Window("w", None, [Tab("t", pane())])))
    for path in (store.current, store.previous):
        with open(path, "w") as fh:
            fh.write("garbage")
    loaded, note = store.load()
    assert loaded is None
    assert "unreadable" in note


def test_store_rotation_keeps_only_n(tmp_path):
    store = Store(str(tmp_path))
    for i in range(8):
        s = snap(Window("w", None, [Tab("t", pane(cwd=f"/dir{i}"))]))
        s.created_at = 1000.0 + i * 3600
        store.save(s, keep=3)
    assert len(store.list_sessions()) == 3


def test_store_load_with_no_snapshot(tmp_path):
    loaded, note = Store(str(tmp_path)).load()
    assert loaded is None
    assert note == "no snapshot yet"
