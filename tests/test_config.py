import pytest

from iterm_organizer.config import (
    Config,
    ConfigError,
    DEFAULT_AUTORUN,
    default_config_text,
    load_config,
)


def test_missing_file_yields_defaults(tmp_path):
    cfg = load_config(str(tmp_path / "nope.toml"))
    assert cfg.snapshot_interval == 60
    assert cfg.autorun == DEFAULT_AUTORUN


def test_partial_file_keeps_other_defaults(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("snapshot_interval = 30\n")
    cfg = load_config(str(path))
    assert cfg.snapshot_interval == 30
    assert cfg.keep_snapshots == 20
    assert cfg.autorun == DEFAULT_AUTORUN


def test_custom_job_lists(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[jobs]\nautorun = ["^make dev$"]\nnever = ["^rm"]\n')
    cfg = load_config(str(path))
    assert cfg.autorun == ["^make dev$"]
    assert cfg.never == ["^rm"]


def test_invalid_toml(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("this is not = = toml")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(str(path))


def test_invalid_regex_message_names_the_pattern(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[jobs]\nnever = ["^(unclosed"]\n')
    with pytest.raises(ConfigError) as exc:
        load_config(str(path))
    assert "^(unclosed" in str(exc.value)


def test_non_integer_interval(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('snapshot_interval = "sixty"\n')
    with pytest.raises(ConfigError, match="must be an integer"):
        load_config(str(path))


def test_bad_restore_prompt(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('restore_prompt = "maybe"\n')
    with pytest.raises(ConfigError, match="restore_prompt must be one of"):
        load_config(str(path))


def test_too_small_interval():
    with pytest.raises(ConfigError, match="at least 5 seconds"):
        Config(snapshot_interval=1)


def test_jobs_must_be_string_list(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("[jobs]\nautorun = [1, 2]\n")
    with pytest.raises(ConfigError, match="list of strings"):
        load_config(str(path))


def test_default_config_text_parses_back_to_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(default_config_text())
    cfg = load_config(str(path))
    assert cfg.autorun == DEFAULT_AUTORUN
    assert cfg.snapshot_interval == 60


def test_missing_toml_parser_is_reported_not_crashed(tmp_path, monkeypatch):
    """iTerm2's bundled Python may predate tomllib; the daemon must still run."""
    import iterm_organizer.config as config_mod

    path = tmp_path / "c.toml"
    path.write_text("snapshot_interval = 30\n")
    monkeypatch.setattr(config_mod, "tomllib", None)
    with pytest.raises(ConfigError, match="no TOML parser available"):
        config_mod.load_config(str(path))


def test_missing_toml_parser_still_allows_defaults(tmp_path, monkeypatch):
    import iterm_organizer.config as config_mod

    monkeypatch.setattr(config_mod, "tomllib", None)
    cfg = config_mod.load_config(str(tmp_path / "absent.toml"))
    assert cfg.snapshot_interval == 60
