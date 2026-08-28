# How to use

## Day to day

Nothing. That is the point. The agent runs in the background and saves your
iTerm2 layout every 60 seconds. It never opens, closes or changes a window
while you are working.

To confirm it is alive:

```bash
open -a "iTerm Organizer"
```

A small dialog reports whether the agent is running and when it last saved.

## After a restart or a crash

Start iTerm2 as usual. Within a few seconds a dialog appears:

```
Restore your previous terminal sessions?
4 window(s), 4 pane(s), saved 12 minutes ago.

[ Restore all ]  [ Choose… ]  [ Skip ]
```

- **Restore all** rebuilds every window, tab and split pane, each in its
  original folder.
- **Choose…** asks about each window in turn, so you can bring back two of the
  five you had open.
- **Skip** does nothing. Your snapshot is kept, so you can still restore later
  with `--restore`.

Each restored pane prints a one-line reminder of where you were:

```
~/Development/api-server · main · was: claude --resume
```

## Restoring without waiting

If you skipped the dialog, or want an older session back:

```bash
iterm-organizer --list        # what is saved
iterm-organizer --dry-run     # what the newest one would do
iterm-organizer --restore     # do it
```

To restore a specific earlier point:

```bash
iterm-organizer --list
iterm-organizer --restore ~/.iterm-organizer/sessions/20260828-115140.json
```

`--dry-run` never changes anything, so it is always safe to run first. Get in
the habit of it before a restore you are unsure about:

```bash
iterm-organizer --dry-run --from ~/.iterm-organizer/sessions/20260828-115140.json
```

## Reading a plan

```
Window 2 (1 pane(s))  frame=[2190.0, 177.0, 570.0, 462.0]
  Tab 1: (untitled)
    pane /Users/you/Development/api-server  [Default]
      RUN  cd /Users/you/Development/api-server
      RUN  printf '%s\n' '~/Development/api-server · main · was: claude --resume'
      RUN  claude --resume
```

`RUN` means the line is executed. `TYPE` means it is typed into the pane and
left there, waiting for you to press Return. Anything not on the allowlist is
always `TYPE`, never `RUN`. See [Configuration](CONFIGURATION.md).

## What is and is not restored

Restored:

- window positions and sizes
- tabs, and the exact split-pane layout including nested splits
- each pane's folder, profile and title
- the git branch you were on, shown in the banner
- allowlisted commands, re-run
- Claude Code sessions, resumed with `claude --continue`

Not restored:

- scrollback contents
- SSH connections and anything running on another machine
- shell history state, environment variables you exported by hand
- anything that was not running as a foreground process
