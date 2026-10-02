"""Coordinate processes that share one data folder (desktop app and CLI).

Atomic file replacement keeps each file whole, but two processes can still both
read, change and write the same data, and one change is lost. Every
read-check-write sequence therefore holds an exclusive OS lock on a file in the
data folder. The OS releases it if a process crashes, so a lock is never left
behind. Locks on the same file through different paths (symlinks, relative
paths) are the same lock.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import threading
import time

WRITE_LOCK = ".write.lock"
DESKTOP_LOCK = ".desktop.lock"
WRITE_TIMEOUT = 10.0

# Some network filesystems cannot lock at all; writing without coordination is
# then better than refusing every change.
_UNSUPPORTED = {errno.ENOLCK, getattr(errno, "ENOTSUP", errno.EOPNOTSUPP), errno.EOPNOTSUPP}


class DataFolderBusy(RuntimeError):
    """Another process holds the data folder's lock."""


if os.name == "nt":
    import msvcrt

    def _try_lock(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd):
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)


def _acquire(path: Path, timeout: float, busy_message: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + timeout
    while True:
        try:
            _try_lock(fd)
            return fd
        except OSError as error:
            if error.errno in _UNSUPPORTED:
                return fd
            if time.monotonic() >= deadline:
                os.close(fd)
                raise DataFolderBusy(busy_message) from None
            time.sleep(0.05)


def _release(fd: int) -> None:
    try:
        _unlock(fd)
    except OSError:
        pass  # unsupported filesystem, or already released by close
    finally:
        os.close(fd)


class FolderLock:
    """The write lock for one data folder, shared by everything in the process.

    Re-entrant within a thread, so nested transactions do not deadlock; other
    threads in the process wait their turn.
    """

    def __init__(self, path: Path):
        self.path = path
        self._guard = threading.RLock()
        self._depth = 0
        self._fd = None

    @contextmanager
    def hold(self, timeout: float = WRITE_TIMEOUT):
        with self._guard:
            if self._depth == 0:
                self._fd = _acquire(self.path, timeout,
                                    f"Another Vocab Study window or command is saving to "
                                    f"{self.path.parent}. Try again in a moment.")
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
                if self._depth == 0:
                    fd, self._fd = self._fd, None
                    _release(fd)


_REGISTRY = {}
_REGISTRY_GUARD = threading.Lock()


def write_lock(root) -> FolderLock:
    """The process-wide write lock for the data folder at ``root``."""
    path = (Path(root) / WRITE_LOCK).resolve()
    with _REGISTRY_GUARD:
        lock = _REGISTRY.get(path)
        if lock is None:
            lock = _REGISTRY[path] = FolderLock(path)
        return lock


class DesktopInstanceLock:
    """Held by a desktop process for its lifetime.

    Only the owning desktop may read, recover or delete note recovery copies, so
    a second desktop on the same data folder must not start.
    """

    def __init__(self, root):
        self.root = Path(root)
        self._fd = _acquire(self.root / DESKTOP_LOCK, 0,
                            f"Vocab Study is already open for the data folder {self.root}.")

    def release(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            _release(fd)
