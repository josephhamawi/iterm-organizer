# iterm-organizer

Snapshots your iTerm2 windows, tabs, split panes and running work every 60
seconds, and offers to rebuild them the next time iTerm2 starts.

Built for the case where the machine restarts or iTerm2 dies and you lose every
terminal along with the memory of what you were doing.

## What it restores

- Window, tab and split-pane layout, including nested splits and window geometry
- Each pane's working directory and profile
- Pane titles, plus a one-line context banner: directory, git branch, and the
  command that was running
- Long-running dev processes, under an allowlist
- Claude Code sessions, resumed with `claude --continue`

## Install

```bash
cd ~/Development/iterm-organizer
./install.sh
```

That is the whole install. It creates a private virtualenv, writes a launchd
agent, and starts it immediately - iTerm2 does not need to be restarted, and
iTerm2's own Python runtime is never involved.

| Path | Purpose |
| --- | --- |
| `~/.iterm-organizer/config.toml` | your settings (never overwritten if it exists) |
| `~/.iterm-organizer/` | snapshots, state, logs |
| `~/Library/LaunchAgents/com.rami.iterm-organizer.plist` | the agent |
| `~/.local/bin/iterm-organizer` | CLI |
| `<repo>/.venv` | private virtualenv |

`./uninstall.sh` stops and removes the agent and the CLI. Snapshots and config
stay put.

## How it stays running

launchd starts the agent at login with `KeepAlive`/`SuccessfulExit=false`:

- iTerm2 quits or crashes, so our websocket closes and the process exits
  non-zero; launchd restarts it and it waits for iTerm2 to come back
- another instance already holds the lock, so the process exits **zero** and
  launchd leaves it down rather than respawning in a loop

An advisory `flock` on `~/.iterm-organizer/daemon.lock` guarantees one daemon at
a time. The kernel drops it when the process dies, so a crash cannot leave a
stale lock - which matters, since crashing is what this tool exists for.

The plist sets `AssociatedBundleIdentifiers = com.googlecode.iterm2`. Without
it the agent blocks forever: connecting needs an auth cookie that the iterm2
library requests over AppleScript, and for an unattributed launchd job macOS
raises a TCC Automation prompt that never surfaces. Attributing the agent to
iTerm2's bundle lets the request through.

## When it prompts

Restore is offered **once per iTerm2 instance**, keyed on iTerm2's pid:

- installing while you are working adopts the running instance and stays quiet
- a daemon restart against the same iTerm2 never re-prompts
- a genuinely new iTerm2 - after a reboot, a crash, or a manual relaunch -
  prompts once

Window counts are deliberately not used as a freshness signal. After a reboot
iTerm2 often reopens windows of its own, and requiring a single empty window
would suppress the prompt in exactly the case that matters.

The prompt is also skipped when the snapshot is older than `stale_days` (7) or
is trivial (a single pane in `$HOME` with nothing running). Set
`restore_prompt = "auto"` to skip the dialog and restore straight away, or
`"off"` to disable restore.

## Everyday use

```bash
iterm-organizer --dry-run       # show what would be restored, change nothing
iterm-organizer --list          # list retained snapshots
iterm-organizer --snapshot-now  # capture immediately
iterm-organizer --restore       # restore now, from the latest snapshot
iterm-organizer --restore ~/.iterm-organizer/sessions/20260827-141500.json
tail -f ~/.iterm-organizer/organizer.log
```

Running the CLI from a shell inside iTerm2 works without any prompt, because
iTerm2 is already trusted to send Apple Events to itself.

## How commands are replayed

Every recovered command falls into exactly one of three tiers.

| Tier | Behaviour | Example |
| --- | --- | --- |
| `autorun` | executed on restore | `npm run dev`, `flutter run`, `claude` |
| `never` | shown in the banner, never typed or run | `ssh`, `sudo`, `git rebase`, `vim` |
| everything else | **typed into the pane without pressing Enter** | `./deploy.sh --prod` |

The third tier is the important one. An unrecognized command is put in front of
you, ready to run, and waits for you to press Enter. Nothing you did not
explicitly allowlist ever executes by itself.

Edit the lists in `~/.iterm-organizer/config.toml`. Patterns are regexes and
must use TOML *literal* strings (single quotes), or `\b` is parsed as a
backspace:

```toml
[jobs]
autorun = ['^make watch$']
never   = ['^terraform apply']
```

`never` is checked before `autorun`, so `sudo npm run dev` is never executed.

## Safety

Replaying a command read out of the process table is code execution at login,
and is treated as such:

- Only an exact `autorun` match is ever executed.
- Any recovered command containing a newline or a control character is dropped
  entirely, so a crafted process name cannot smuggle in a second command.
- A pane's directory must still exist before any command is sent. If the
  directory is gone, the pane opens in `$HOME`, the banner says so, and the
  command is downgraded to banner-only rather than run somewhere unintended.
- Paths are quoted with `shlex.quote`; the banner is printed via `printf` with a
  quoted argument, so it cannot execute.
- Restores are capped at `max_restore_panes` (40) and never reuse the window you
  are currently working in.

Note that flags are replayed faithfully. If a pane was running
`claude --dangerously-skip-permissions`, restore brings it back with that flag.

## Design

`daemon.py` holds every iTerm2 API call. Everything with logic in it is pure and
tested without iTerm2 running:

| Module | Responsibility |
| --- | --- |
| `model.py` | snapshot types, JSON round-trip, git branch, atomic storage |
| `jobs.py` | process-table parsing, tree walking, tier classification |
| `config.py` | config loading and validation |
| `plan.py` | snapshot to an ordered list of restore actions |
| `daemon.py` | iTerm2 glue only |
| `lock.py` | single-instance advisory lock |
| `supervisor.py` | process lifecycle, iTerm2 pid tracking, restore gating |

Snapshots are written to a temp file and `os.replace`d, with the previous
version kept alongside, so a crash mid-write cannot leave you without a
readable snapshot. An empty snapshot is never written over a good one.

The full design document is in
`docs/superpowers/specs/2026-08-27-iterm-organizer-design.md`.

## Troubleshooting

```bash
launchctl print gui/$UID/com.rami.iterm-organizer   # is it running?
tail -f ~/.iterm-organizer/organizer.log            # what is it doing?
cat ~/.iterm-organizer/launchd.err.log              # did it fail to start?
```

If the log stops after `iterm2 module loaded` and never reaches `starting`, the
agent is blocked on the AppleScript cookie: check that
`AssociatedBundleIdentifiers` is still present in the plist.

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install pytest iterm2
.venv/bin/python -m pytest
```
