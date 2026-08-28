# iTerm Organizer

Saves your iTerm2 windows every 60 seconds and offers to bring them back after
a restart or a crash.

It remembers, for every pane: which folder you were in, which git branch, the
window and split layout, and what was running. When iTerm2 next starts it asks
whether you want it all back.

Built for the moment when the Mac restarts and every terminal is gone along
with the memory of what you were doing.

## Install

Double-click **Install.command**.

That is all. The background agent starts immediately and from then on runs at
login. iTerm2 does not need to be restarted.

macOS may warn that the file is from an unidentified developer. Right-click it
and choose **Open** to run it anyway.

Double-clicking **iTerm Organizer.app** does the same thing and then shows you
its current status.

## What happens next

- Every 60 seconds your layout is saved to `~/.iterm-organizer/`.
- Nothing else. No windows are touched while you work.
- The next time iTerm2 starts, a dialog asks: **Restore all / Choose… / Skip**.

## Quick reference

```bash
open -a "iTerm Organizer"                       # status
"iTerm Organizer.app/Contents/MacOS/iTermOrganizer" --dry-run
```

Add the command line tool to your PATH if you want it handy:

```bash
ln -s "$PWD/iTerm Organizer.app/Contents/MacOS/iTermOrganizer" ~/.local/bin/iterm-organizer
iterm-organizer --status
```

| Command | Does |
| --- | --- |
| `--status` | is the agent running, when was the last save |
| `--dry-run` | print exactly what a restore would do, change nothing |
| `--list` | list saved snapshots |
| `--restore` | restore now, without waiting for a restart |
| `--snapshot-now` | save immediately |
| `--uninstall` | stop and remove the agent |

## Documentation

- [How to use](docs/HOW-TO-USE.md) — everyday use, and what to do after a crash
- [Configuration](docs/CONFIGURATION.md) — which commands are re-run, and how to change that
- [Troubleshooting](docs/TROUBLESHOOTING.md) — when it does not do what you expect
- [How it works](docs/HOW-IT-WORKS.md) — the design, and why it is safe

## Uninstall

Double-click **Uninstall.command**. Your saved sessions and settings stay in
`~/.iterm-organizer` so you can reinstall without losing them. Delete that
folder too if you want it all gone.

## Requirements

macOS 12 or newer, iTerm2 with its Python API enabled (Preferences → General →
Magic → Enable Python API), and Python 3.11 or newer on the machine.

No network access, no telemetry. Everything stays on this Mac.
