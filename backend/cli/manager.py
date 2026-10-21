"""Shared lock for this project's own runtime only."""
from contextlib import contextmanager
from backend.storage.vault import HOME


@contextmanager
def locked():
    import msvcrt
    HOME.mkdir(parents=True,exist_ok=True)
    with (HOME/'operation.lock').open('a+b') as file:
        try:
            file.seek(0)
            if not file.read(1):file.write(b'0');file.flush()
            file.seek(0)
            msvcrt.locking(file.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise RuntimeError('本机版正在检查或保存，请稍后重试') from None
        try:yield
        finally:file.seek(0);msvcrt.locking(file.fileno(),msvcrt.LK_UNLCK,1)
