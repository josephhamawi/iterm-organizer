# iterm-organizer — Design

Date: 2026-08-27
Status: Approved

## Problem

When macOS restarts or iTerm2 crashes, every terminal session is lost. There is no
record of which projects were open, which directories each pane was in, what was
running, or what the user was in the middle of doing. Recovering the working set is
manual and error-prone, and context (git branch, running dev servers, active Claude
Code sessions) is lost entirely.

iTerm2's built-in "Save Window Arrangement" does not solve this: arrangements are
saved manually, go stale immediately, and capture only layout — never the running
processes.

## Goals

1. Survive a hard crash (kernel panic, power loss) with at most 60 seconds of lost state.
2. Restore window/tab/pane layout including split geometry, with each pane in its
   original working directory.
3. Restore working context: pane titles and the git branch each pane was on.
4. Re-run long-running development processes that were active, under an explicit allowlist.
5. Restore Claude Code sessions via `claude --continue`.
6. Prompt at iTerm2 launch rather than restoring automatically.

## Non-Goals

- Restoring scrollback buffer contents.
- Restoring remote/SSH session state.
- Syncing sessions across machines.
- Replacing iTerm2 profiles or window arrangements.

## Architecture

### Deployment

Source of truth lives in a git repo at `~/Development/iterm-organizer`.

A ten-line shim is installed at:

```
~/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm_organizer.py
```

The shim prepends `~/Development/iterm-organizer/src` to `sys.path`, imports the
package, and calls `main()`. iTerm2 launches AutoLaunch scripts itself and injects
an authentication cookie, so no connection-permission dialog appears and no launchd
job is required. iTerm2's Python API is already enabled on this machine
(`EnableAPIServer = 1`).

On first run iTerm2 prompts once to download its managed Python runtime. This is a
one-time, user-visible cost and is accepted.

The process has two responsibilities in a single lifetime:

1. **Boot phase** — evaluate the last snapshot and, if warranted, offer to restore.
2. **Capture loop** — an async task that snapshots state every 60 seconds until
   iTerm2 exits.

### On-disk state

```
~/.iterm-organizer/
  config.toml          # snapshot interval, autorun allowlist, never-list, retention
  snapshot.json        # latest snapshot, written atomically
  snapshot.prev.json   # previous good snapshot, for corrupt/torn recovery
  sessions/            # rotated history, last 20 content-distinct snapshots
  organizer.log        # capture and restore log
```

All writes go to a temporary file in the same directory followed by `os.replace`,
which is atomic on APFS. Before replacing `snapshot.json`, the current file is copied
to `snapshot.prev.json`. A crash mid-write can therefore never leave the system
without a readable snapshot.

## Capture

Every `snapshot_interval` seconds (default 60), the daemon walks
`app.windows -> window.tabs -> tab.root`. `tab.root` is an `iterm2.Splitter` whose
`children` and `vertical` attributes describe the real split tree; this tree is
serialized recursively so pane geometry is reproduced faithfully rather than
flattened into a tab list.

### Recorded per pane

| Field | Source |
| --- | --- |
| `cwd` | `session.async_get_variable("path")` |
| `title` | session `autoName` / user-set `name` variable |
| `profile` | `session.async_get_profile()` name, so colors and settings survive |
| `job_name` | `jobName` variable |
| `job_pid` | `jobPid` variable |
| `argv` | `ps -p <job_pid> -o args=` |
| `git_branch` | parsed directly from `<cwd>/.git/HEAD` |

If `job_name` is a shell (`zsh`, `bash`, `sh`, `fish`), the pane is treated as idle.
If the job is a known process-manager wrapper, one level of child processes is walked
via `pgrep -P <pid>` so that `npm run dev` resolves to the underlying command.

Git branch is read by parsing `.git/HEAD` as a file. No subprocess is spawned, which
keeps a 60-second sweep over dozens of panes cheap. A detached HEAD records the short
SHA instead of a branch name.

### Recorded per tab and window

Tab: ordered tabs, tab title, and the serialized split tree.
Window: `window_id` and frame from `window.async_get_frame()`.

### Retention

A snapshot is appended to `sessions/` only when its content hash differs from the
previous one. The 20 most recent are kept; older files are deleted.

## Job replay

Commands are classified into exactly three tiers by `jobs.py`.

```toml
[jobs]
autorun = [
  "^npm run (dev|start|watch)",
  "^yarn (dev|start)",
  "^pnpm (dev|start)",
  "^flutter run",
  "^firebase emulators:start",
  "^next dev",
  "^vite",
  "^claude",
]
never = [
  "^ssh ",
  "^sudo",
  "^git (rebase|commit)",
  "^psql",
  "^mysql",
  "^vim?",
  "^nano",
]
```

1. **autorun** — the command matches an `autorun` pattern and no `never` pattern. It is
   executed on restore. `claude` is rewritten to `claude --continue`, which is
   idempotent and safe to re-issue.
2. **never** — the command matches a `never` pattern. It is recorded and displayed in
   the pane's context banner, but never typed and never executed.
3. **default** — anything unmatched. The command is typed into the pane via
   `async_send_text` *without a trailing newline*. The user sees exactly what was
   running and presses Enter themselves if they want it.

`never` is evaluated before `autorun`, so a `never` match always wins.

### Security

Replaying commands read from the process table is code execution at login, and is
treated as such. The following constraints are mandatory and are unit-tested:

- Only an exact `autorun` allowlist match is ever executed.
- Any recovered command containing a newline or carriage return is dropped entirely,
  in every tier. This prevents a crafted process name from injecting a second command.
- A pane's recorded `cwd` must exist and be a directory before any command is sent.
- Commands are never concatenated with shell metacharacters by the tool itself; the
  `cd` is emitted separately and its path is quoted with `shlex.quote`.
- The default tier types but does not execute, so an unrecognized command can never
  run without a deliberate keypress.

## Restore

### Trigger

At boot the daemon reads `snapshot.json`, falling back to `snapshot.prev.json` on a
parse error. It stays silent when:

- no snapshot exists, or
- the snapshot is older than 7 days, or
- the snapshot is trivial: a single window with a single pane whose cwd is `$HOME`.

Otherwise it presents an `iterm2.Alert` with three buttons: **Restore all**,
**Choose...**, **Skip**. "Choose..." opens a single scratch tab running a small
checkbox picker over the saved windows; the user's selection is fed back into the
same restore plan.

If the `Alert` API proves too limited to carry the button set, the fallback is to open
one scratch tab and run the picker there for all three choices. This decision is made
during implementation against the real API; the restore plan itself is unaffected
either way.

Restore never reuses the window the user is currently in. It always creates new
windows.

### Per-pane sequence

1. `cd <shlex.quote(cwd)>` — or, if the directory no longer exists, remain in `$HOME`
   and note the original path in the banner.
2. Set the pane and tab title from the snapshot.
3. Print a one-line context banner, e.g.
   `~/Development/aied · main · was: npm run dev`
4. Apply the job tier: execute, skip, or type-without-newline.

### Limits

Restoring is serialized and each pane costs a spawn. Restores are capped at 40 panes;
beyond that the user is warned and the remainder is listed in the log rather than
opened.

## Error handling

| Failure | Behavior |
| --- | --- |
| `snapshot.json` unparseable | fall back to `snapshot.prev.json`, log at WARNING |
| both snapshots unparseable | skip restore, log at ERROR, continue into capture loop |
| API connection drops | reconnect with exponential backoff, capped at 60s |
| recorded `cwd` missing | open in `$HOME`, note original path in banner |
| `ps` returns nothing for a pid | record the pane as idle |
| a pane fails to restore | continue with remaining panes; collect errors and show one summary alert at the end |

No failure path aborts a restore in progress. Partial restoration is always preferred
over none.

## Module boundaries

iTerm2 API calls are confined to one module. Everything that carries logic is pure and
testable without iTerm2 running.

| Module | Responsibility | Depends on | Unit-testable |
| --- | --- | --- | --- |
| `model.py` | snapshot dataclasses, JSON round-trip, split-tree encode/decode | stdlib | yes |
| `jobs.py` | `ps` output parsing, tier classification, allowlist matching, newline rejection | stdlib | yes |
| `plan.py` | snapshot -> ordered list of restore actions, cwd validation, banner text | `model`, `jobs` | yes |
| `config.py` | load and validate `config.toml`, supply defaults | stdlib `tomllib` | yes |
| `daemon.py` | iTerm2 API glue: walk sessions, build windows, send text, alerts | `iterm2`, all above | manual only |
| `__main__.py` | CLI entry, argument parsing | all above | yes |

`plan.py` is the seam that makes this testable: it turns a snapshot into a list of
declarative actions (`CreateWindow`, `SplitPane`, `SendText`, `SetTitle`) with no side
effects. `daemon.py` only executes that list.

## CLI

| Command | Effect |
| --- | --- |
| `--dry-run` | print the restore plan for the latest snapshot; touch nothing |
| `--snapshot-now` | capture one snapshot immediately and exit |
| `--restore <file>` | restore from a specific snapshot in `sessions/` |
| `--list` | list retained snapshots with timestamp, window count, pane count |

## Testing

- `model.py`: round-trip property tests over generated split trees, including
  deeply nested and single-pane cases.
- `jobs.py`: table-driven classification tests covering each tier, `never`-beats-
  `autorun` precedence, newline rejection, `claude` to `claude --continue` rewriting,
  and shell-wrapper detection.
- `plan.py`: fixture snapshots to expected action lists; missing-cwd fallback; the
  40-pane cap; banner formatting.
- `config.py`: defaults applied on a missing file; invalid regex rejected with a clear
  error rather than a crash.
- `daemon.py`: verified manually via `--dry-run` and `--snapshot-now` against a live
  iTerm2.

## Rollout

1. Build and unit-test `model`, `jobs`, `config`, `plan` with no iTerm2 involvement.
2. Add `daemon.py` capture only. Run `--snapshot-now`, inspect the JSON by hand.
3. Install the AutoLaunch shim with capture enabled and restore disabled. Let it run
   for a day and confirm snapshots track reality.
4. Enable restore behind `--dry-run` and compare the plan against a known layout.
5. Enable the live boot prompt.
