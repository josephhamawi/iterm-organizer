# Troubleshooting

Start here:

```bash
iterm-organizer --status
tail -20 ~/.iterm-organizer/organizer.log
```

A healthy log looks like this:

```
12:09:10 INFO  process start (pid=8255, python=3.14.0)
12:09:13 INFO  iterm2 module loaded
12:09:15 INFO  starting (interval=60s, iterm2 pid=523)
12:09:28 INFO  connected (iterm2 instance=pid:523, restore=already handled)
12:10:30 INFO  snapshot: 4 window(s), 4 pane(s)
```

`snapshot:` lines only appear when something actually changed, so gaps between
them are normal.

## No dialog appeared after a restart

Check what the agent decided:

```bash
grep connected ~/.iterm-organizer/organizer.log | tail -3
```

- `restore=already handled` means it considered this the same iTerm2 it had
  already dealt with. Restore is offered once per iTerm2 instance.
- `restore=offered` means it did ask, so the dialog was shown and dismissed.

Your session is not lost either way:

```bash
iterm-organizer --list
iterm-organizer --restore ~/.iterm-organizer/sessions/<newest>.json
```

To force the dialog for testing, forget the current instance and restart the
agent:

```bash
rm ~/.iterm-organizer/state.json
iterm-organizer --install "$(command -v iterm-organizer)"
```

Click **Skip** to confirm the dialog works without opening anything.

## The log stops after "iterm2 module loaded"

The agent is stuck asking iTerm2 for a connection cookie. That request goes
through AppleScript, and macOS blocks it for a background job that has no
bundle identity.

Check the plist still carries the attribution that makes it work:

```bash
grep -A2 AssociatedBundleIdentifiers ~/Library/LaunchAgents/com.rami.iterm-organizer.plist
```

It must list `com.googlecode.iterm2`. Reinstall to restore it.

## Nothing is being saved

```bash
iterm-organizer --snapshot-now
```

Run from a terminal inside iTerm2, this works without any permission prompt.

- If it succeeds but the agent saves nothing, the agent is not running:
  `iterm-organizer --status`.
- If it fails, check the iTerm2 Python API is on: iTerm2 → Settings → General →
  Magic → **Enable Python API**.

## "Python 3.11 or newer is required"

The app uses whichever Python it finds on the machine. Install a current one:

```bash
brew install python
```

Then reinstall.

## A restored pane opened in the wrong folder

If the folder no longer exists, the pane opens in your home directory and the
banner says so:

```
! ~/Development/old-project is gone - opened in ~
```

Any command that pane was running is deliberately not restored, since running a
project command in the wrong directory is worse than not running it.

## A command I expected did not run

By design. Only commands matching the `autorun` allowlist are executed.
Everything else is typed into the pane and waits for you. See
[Configuration](CONFIGURATION.md) to add your own.

## Too many windows opened

Lower the cap:

```toml
max_restore_panes = 10
```

Restores stop cleanly at the limit and tell you how many panes were left out.

## Starting over

```bash
iterm-organizer --uninstall
rm -rf ~/.iterm-organizer
```

Then double-click **Install.command** again.
