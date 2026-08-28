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
git clone <this repo> ~/Development/iterm-organizer
cd ~/Development/iterm-organizer
./install.sh
```

Then restart iTerm2. The first launch offers to download iTerm2's Python
runtime; accept it. After that the organizer starts automatically with iTerm2.

`install.sh` writes three things and nothing else:

| Path | Purpose |
| --- | --- |
| `~/.iterm-organizer/config.toml` | your settings (never overwritten if it exists) |
| `~/Library/Application Support/iTerm2/Scripts/AutoLaunch/iterm_organizer.py` | 10-line shim that runs the code from this repo |
| `~/.local/bin/iterm-organizer` | CLI wrapper |

`./uninstall.sh` removes the shim and the CLI. Snapshots and config stay put.

## Everyday use

```bash
iterm-organizer --dry-run       # show what would be restored, change nothing
iterm-organizer --list          # list retained snapshots
iterm-organizer --snapshot-now  # capture immediately
iterm-organizer --restore       # restore now, from the latest snapshot
iterm-organizer --restore ~/.iterm-organizer/sessions/20260827-141500.json
tail -f ~/.iterm-organizer/organizer.log
```

Running the CLI from a normal shell asks iTerm2 for permission the first time.
The AutoLaunch daemon never does, because iTerm2 hands it a cookie directly.

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

## When it prompts

At iTerm2 launch, only if all of the following hold:

- a snapshot exists and is newer than `stale_days` (7)
- it is not trivial (a single pane sitting in `$HOME` with nothing running)
- iTerm2 looks freshly started rather than mid-session, so reloading the script
  while you are working never triggers a restore

Set `restore_prompt = "auto"` to skip the prompt, or `"off"` to disable restore.

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

Snapshots are written to a temp file and `os.replace`d, with the previous
version kept alongside, so a crash mid-write cannot leave you without a
readable snapshot. An empty snapshot is never written over a good one.

The full design document is in
`docs/superpowers/specs/2026-08-27-iterm-organizer-design.md`.

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install pytest iterm2
.venv/bin/python -m pytest
```
