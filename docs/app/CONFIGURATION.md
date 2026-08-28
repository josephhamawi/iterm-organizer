# Configuration

Settings live in `~/.iterm-organizer/config.toml`. It is created on first
install and never overwritten afterwards.

After editing, restart the agent so it picks up the change:

```bash
iterm-organizer --install "$(command -v iterm-organizer)"
```

or just double-click **Install.command** again.

## Settings

```toml
snapshot_interval = 60     # seconds between saves
keep_snapshots    = 20     # how many past sessions to keep
max_restore_panes = 40     # refuse to open more panes than this at once
stale_days        = 7      # ignore snapshots older than this
restore_prompt    = "alert" # alert | auto | off
```

`restore_prompt`:

- `alert` asks before restoring. The default.
- `auto` restores immediately at startup without asking.
- `off` never restores. Snapshots are still saved, and `--restore` still works.

## Which commands come back

Every command found running in a pane falls into exactly one of three tiers.

| Tier | What happens | Examples |
| --- | --- | --- |
| `autorun` | executed on restore | `npm run dev`, `flutter run`, `claude` |
| `never` | mentioned in the banner, never typed or run | `ssh`, `sudo`, `git rebase`, `vim` |
| everything else | **typed into the pane, Return not pressed** | `./deploy.sh --prod` |

The third tier is the safety net. An unrecognised command is put in front of
you ready to go, and waits for you to press Return. Nothing you have not
explicitly allowlisted ever runs by itself.

```toml
[jobs]
autorun = [
  '^npm run (dev|start|watch)\b',
  '^flutter run\b',
  '^claude\b',
]
never = [
  '^ssh\b',
  '^sudo\b',
  '^git (rebase|commit|merge)\b',
]
```

Both lists are regular expressions matched against the command line.

**Use single quotes.** TOML single-quoted strings are literal. In double
quotes, `\b` is read as a backspace character and your word boundaries silently
stop working.

`never` is checked first, so `sudo npm run dev` is never executed even though
`npm run dev` is on the allowlist.

## Adding your own

To auto-start a build watcher:

```toml
[jobs]
autorun = ['^npm run (dev|start|watch)\b', '^make watch$']
```

To make sure a dangerous command is never even typed:

```toml
[jobs]
never = ['^terraform apply', '^kubectl delete']
```

To stop auto-running anything at all, leaving every command typed but not
executed:

```toml
[jobs]
autorun = []
```

## Checking your changes took effect

```bash
iterm-organizer --dry-run
```

Look at the `RUN` and `TYPE` markers. A malformed regex is reported with the
exact pattern that failed, and the agent falls back to defaults rather than
refusing to start.
