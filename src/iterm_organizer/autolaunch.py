"""Entry point used by the iTerm2 AutoLaunch shim."""

from __future__ import annotations

import logging
import logging.handlers

from .config import ConfigError, load_config
from .model import Store


def setup_logging(store: Store) -> None:
    store.ensure()
    handler = logging.handlers.RotatingFileHandler(
        store.log, maxBytes=1_000_000, backupCount=2
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    )
    root = logging.getLogger("iterm_organizer")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def run() -> None:
    import iterm2

    from . import daemon

    store = Store()
    setup_logging(store)
    log = logging.getLogger("iterm_organizer")
    try:
        config = load_config(store.config)
    except ConfigError as exc:
        log.error("config error, falling back to defaults: %s", exc)
        from .config import Config

        config = Config()

    async def entry(connection):
        await daemon.main(connection, store=store, config=config)

    log.info("iterm-organizer starting (interval=%ss)", config.snapshot_interval)
    iterm2.run_forever(entry)
