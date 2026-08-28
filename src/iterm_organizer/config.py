"""Configuration loading for iterm-organizer."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field

DEFAULT_AUTORUN = [
    r"^npm run (dev|start|watch)\b",
    r"^yarn (dev|start|watch)\b",
    r"^pnpm (dev|start|watch)\b",
    r"^bun (dev|run dev)\b",
    r"^flutter run\b",
    r"^firebase emulators:start\b",
    r"^next dev\b",
    r"^vite\b",
    r"^expo start\b",
    r"^claude\b",
]

DEFAULT_NEVER = [
    r"^ssh\b",
    r"^sudo\b",
    r"^git (rebase|commit|merge|cherry-pick|bisect)\b",
    r"^psql\b",
    r"^mysql\b",
    r"^vim?\b",
    r"^nvim\b",
    r"^nano\b",
    r"^emacs\b",
    r"^less\b",
    r"^man\b",
    r"^top\b",
    r"^htop\b",
    r"^kubectl (exec|port-forward)\b",
    r"^docker (exec|attach)\b",
]

DEFAULT_PATH = os.path.expanduser("~/.iterm-organizer/config.toml")


class ConfigError(Exception):
    """Raised for a malformed config file, with a message a human can act on."""


@dataclass
class Config:
    snapshot_interval: int = 60
    keep_snapshots: int = 20
    max_restore_panes: int = 40
    stale_days: int = 7
    restore_prompt: str = "alert"  # alert | auto | off
    autorun: list[str] = field(default_factory=lambda: list(DEFAULT_AUTORUN))
    never: list[str] = field(default_factory=lambda: list(DEFAULT_NEVER))
    autorun_patterns: list[re.Pattern] = field(default_factory=list, repr=False)
    never_patterns: list[re.Pattern] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self.autorun_patterns = _compile(self.autorun, "autorun")
        self.never_patterns = _compile(self.never, "never")
        if self.restore_prompt not in ("alert", "auto", "off"):
            raise ConfigError(
                f"restore_prompt must be one of alert, auto, off "
                f"(got {self.restore_prompt!r})"
            )
        if self.snapshot_interval < 5:
            raise ConfigError("snapshot_interval must be at least 5 seconds")


def _compile(patterns: list[str], key: str) -> list[re.Pattern]:
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error as exc:
            raise ConfigError(
                f"invalid regex in [jobs].{key}: {pattern!r} ({exc})"
            ) from exc
    return compiled


def _as_int(table: dict, key: str, default: int) -> int:
    value = table.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{key} must be an integer (got {value!r})")
    return value


def _as_str_list(table: dict, key: str, default: list[str]) -> list[str]:
    value = table.get(key, default)
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ConfigError(f"[jobs].{key} must be a list of strings")
    return list(value)


def load_config(path: str | None = None) -> Config:
    """Load config from ``path``. A missing file yields the defaults."""
    path = os.path.expanduser(path or DEFAULT_PATH)
    if not os.path.exists(path):
        return Config()
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    jobs = data.get("jobs", {})
    if not isinstance(jobs, dict):
        raise ConfigError("[jobs] must be a table")
    return Config(
        snapshot_interval=_as_int(data, "snapshot_interval", 60),
        keep_snapshots=_as_int(data, "keep_snapshots", 20),
        max_restore_panes=_as_int(data, "max_restore_panes", 40),
        stale_days=_as_int(data, "stale_days", 7),
        restore_prompt=str(data.get("restore_prompt", "alert")),
        autorun=_as_str_list(jobs, "autorun", DEFAULT_AUTORUN),
        never=_as_str_list(jobs, "never", DEFAULT_NEVER),
    )


def default_config_text() -> str:
    """The commented starter config written by the installer."""
    # TOML *literal* strings (single quotes). A basic string would turn the
    # regex escape \\b into a backspace character and silently break every
    # word-boundary pattern.
    autorun = "\n".join(f"  '{p}'," for p in DEFAULT_AUTORUN)
    never = "\n".join(f"  '{p}'," for p in DEFAULT_NEVER)
    return f"""# iterm-organizer configuration

# How often to snapshot iTerm2 state, in seconds.
snapshot_interval = 60

# How many distinct snapshots to keep in sessions/.
keep_snapshots = 20

# Refuse to restore more than this many panes at once.
max_restore_panes = 40

# Ignore snapshots older than this many days.
stale_days = 7

# alert = ask at launch, auto = restore without asking, off = never restore.
restore_prompt = "alert"

[jobs]
# Commands matching autorun are RE-EXECUTED on restore.
# Anything not listed here is typed into the pane WITHOUT pressing Enter.
autorun = [
{autorun}
]

# Commands matching never are shown in the banner but never typed or run.
# never is checked before autorun, so a never match always wins.
never = [
{never}
]
"""
