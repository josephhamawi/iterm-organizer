"""Single-instance lock, so two launch paths cannot both run the daemon."""

from __future__ import annotations

import errno
import fcntl
import os


class AlreadyRunning(Exception):
    """Raised when another instance already holds the lock."""


class InstanceLock:
    """An advisory whole-file lock held for the life of the process.

    The lock is released by the kernel when the process dies, so a crash never
    leaves a stale lock behind - which matters here, since crashing is the
    situation this tool exists for.
    """

    def __init__(self, path: str):
        self.path = os.path.expanduser(path)
        self._fd: int | None = None

    def acquire(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise AlreadyRunning(
                    f"another iterm-organizer already holds {self.path}"
                ) from exc
            raise
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        os.fsync(fd)
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()
