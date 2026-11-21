# Contributing

Thanks for taking a look. This is a small, deliberately boring codebase: all the
logic is pure and unit-tested, and the parts that cannot be tested are kept thin.

## Getting set up

```bash
git clone https://github.com/josephhamawi/iterm-organizer.git
cd iterm-organizer
python3 -m venv .venv && .venv/bin/pip install pytest iterm2
.venv/bin/python -m pytest
```

The suite runs without iTerm2 and without an installed agent. If a change cannot
be covered that way, it probably belongs on the other side of the line described
below.

## Where things live

| Path | What belongs there |
| --- | --- |
| `src/iterm_organizer/model.py` | snapshot types, JSON round-trip, atomic storage |
| `src/iterm_organizer/jobs.py` | process-table parsing and command classification |
| `src/iterm_organizer/plan.py` | turning a snapshot into ordered restore actions |
| `src/iterm_organizer/config.py` | config loading and validation |
| `src/iterm_organizer/daemon.py` | **the only** iTerm2 API calls |
| `src/iterm_organizer/supervisor.py` | process lifecycle, instance identity, restore gating |
| `src/iterm_organizer/agent.py` | launchd job definitions |
| `packaging/menubar/main.swift` | menu bar item; shells out to the CLI, holds no logic |
| `tests/` | one module per source module |

The rule that keeps this maintainable: **logic never goes in `daemon.py`**. If you
need a decision made, put the decision in a pure function elsewhere and call it
from the glue.

## Conventions

- Tests first. Every bug fix starts with a test that fails for the stated reason.
- Comments explain *why*, especially where the code works around macOS or iTerm2
  behaviour. Several of the oddest-looking lines here are load-bearing.
- No new runtime dependencies. The app bundle vendors `iterm2` and nothing else,
  and runs on whatever Python 3.11+ is already on the machine.
- Anything that executes a recovered command must respect the three tiers in the
  README's safety section. New code paths that send text to a pane need a test
  showing an unrecognized command is typed, not run.

## Building the app

```bash
./build-app.sh            # -> ~/Development/iTermOrganizer
```

The menu bar item is compiled only when a Swift toolchain is present; the rest of
the app builds and works without one.

## Pull requests

Keep them focused, explain the failure the change fixes, and include the test
that proves it. Screenshots help for anything that touches the prompt or the
menu bar item.
