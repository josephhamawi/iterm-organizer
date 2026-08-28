# How it works

## The shape of it

A launchd agent runs in the background, connected to iTerm2's Python API. Every
60 seconds it walks the window and pane tree and writes a snapshot to disk. At
startup it reads the last snapshot back and offers to rebuild it.

```
launchd  ──starts──▶  iTerm Organizer.app --daemon
                              │
                    iTerm2 Python API (websocket)
                              │
              ┌───────────────┴───────────────┐
         capture every 60s              restore on request
              │                                │
     ~/.iterm-organizer/snapshot.json ─────────┘
```

## What a snapshot holds

Per pane: folder, profile, title, git branch, and the command running in it.
Per tab: the split tree, so nested splits come back in the same arrangement.
Per window: position and size.

The git branch is read straight out of `.git/HEAD` rather than by shelling out
to git, which keeps a sweep across dozens of panes cheap enough to do every
minute.

## Finding the command in a pane

iTerm2 reports a `jobPid` for each session, but that is the *deepest*
foreground process. For a pane running Claude Code it points at one of the MCP
helpers Claude spawned, not at `claude`.

Instead the daemon starts from the session's own process and walks *down* the
process tree, taking the shallowest descendant that is not a shell. That gives
back the command you actually typed. One `ps` call covers every pane in a
sweep.

## Not losing your session

Three things protect the snapshot, because the moment it matters is the moment
the machine is misbehaving.

**Atomic writes.** A snapshot is written to a temporary file, flushed, then
moved into place with `os.replace`. A crash during a write cannot leave a
half-written file.

**A previous copy.** The old snapshot is moved to `snapshot.prev.json` before
the new one lands. If the newest file is unreadable, the one before it is used.

**An archive.** Every snapshot whose content differs from the last is copied
into `sessions/`, and the newest 20 are kept. This is what lets you go back to
where you were an hour ago, not just to the most recent save.

An empty snapshot is never written over a good one. Without that rule, a
capture taken while iTerm2 was shutting down would erase exactly the session
you wanted back.

## Restoring safely

Replaying a command read out of the process table is code execution at login,
so it is treated as such:

- Only an exact `autorun` allowlist match is ever executed.
- `never` is checked before `autorun`, so `sudo npm run dev` cannot slip
  through on the strength of the `npm run dev` rule.
- Any recovered command containing a newline or a control character is dropped
  entirely. A crafted process name cannot smuggle in a second command.
- A pane's folder must still exist before any command is sent to it.
- Paths are shell-quoted. The reminder banner is printed through `printf` with
  a quoted argument, so it cannot execute.
- Everything unrecognised is typed but not run.

## When it decides to ask

Restore is offered once per iTerm2 instance, identified by its process id, or
by the modification time of iTerm2's API socket if the process id cannot be
read. Being unable to identify the instance never counts as "same as before",
because that would silently cancel a restore.

The decision is made after connecting, never before. At login launchd normally
starts the agent before iTerm2 exists, so anything measured up front would be
meaningless.

Window counts are deliberately not used to judge whether iTerm2 is freshly
started. After a reboot iTerm2 often reopens windows of its own, and requiring
a single empty window would suppress the prompt in exactly the case this tool
exists for.

The first run after installation adopts whatever iTerm2 is already running, so
installing in the middle of a working day never prompts on top of your windows.

## Staying alive

launchd keeps the agent running with `KeepAlive` set to restart on failure
only:

- iTerm2 quits, the websocket closes, the daemon exits non-zero, launchd starts
  it again and it waits for iTerm2 to come back.
- Another instance already holds the lock, so the daemon exits zero and launchd
  leaves it alone instead of respawning it every 20 seconds.

A `flock` on `~/.iterm-organizer/daemon.lock` guarantees a single daemon. The
kernel releases it when the process dies, so a crash cannot leave a stale lock
behind, which matters because crashing is the situation this tool is for.

## Talking to iTerm2 at all

Connecting needs an authentication cookie, which the iterm2 library requests
over AppleScript. For a launchd job with no bundle identity, macOS blocks that
request behind a permission prompt that never appears, and iTerm2 rejects
cookieless connections outright. The agent's job definition therefore declares
`AssociatedBundleIdentifiers = com.googlecode.iterm2`, which is what lets the
request through.

## Layout

The app bundle carries its own copy of the code and its dependencies, and uses
whatever Python 3.11 or newer is installed. No virtualenv, and iTerm2's own
Python runtime is never involved.

```
iTerm Organizer.app/Contents/
  MacOS/iTermOrganizer     launcher: picks an interpreter, dispatches
  Resources/app/           the package
  Resources/vendor/        iterm2, websockets, protobuf
  Resources/AppIcon.icns
```

Inside the package, everything that carries logic is separated from the iTerm2
API so it can be tested without iTerm2 running:

| Module | Responsibility |
| --- | --- |
| `model.py` | snapshot types, JSON, git branch, atomic storage |
| `jobs.py` | process tree, command classification |
| `config.py` | settings |
| `plan.py` | snapshot to an ordered list of restore actions |
| `agent.py` | launchd job definition and control |
| `supervisor.py` | process lifecycle, instance identity, restore gating |
| `daemon.py` | iTerm2 API calls, and nothing else |

`plan.py` is the seam that makes restore testable. It turns a snapshot into a
declarative list of actions with no side effects, and `daemon.py` only executes
that list.
