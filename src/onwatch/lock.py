from __future__ import annotations

import os
from pathlib import Path


class ScanBusyError(RuntimeError):
    pass


class FileLock:
    def __init__(self, path: Path):
        self.path = path
        self.stream = None
        self._windows = os.name == "nt"

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        if self._windows:
            import msvcrt

            self.stream.seek(0)
            if self.stream.read(1) == b"":
                self.stream.seek(0)
                self.stream.write(b"0")
                self.stream.flush()
            self.stream.seek(0)
            try:
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                self.stream.close()
                self.stream = None
                raise ScanBusyError("another deep scan is already running") from error
        else:
            import fcntl

            try:
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                self.stream.close()
                self.stream = None
                raise ScanBusyError("another deep scan is already running") from error
        return self

    def __exit__(self, *_exc):
        if self.stream is None:
            return
        if self._windows:
            import msvcrt

            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.stream.close()
        self.stream = None
