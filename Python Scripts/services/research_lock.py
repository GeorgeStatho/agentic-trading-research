"""Single-host process lock released by the OS on crashes, including on Windows."""
from contextlib import contextmanager
from pathlib import Path


class ResearchSessionBusy(RuntimeError):
    pass


@contextmanager
def research_session_lock(db_path, session_id):
    directory = Path(db_path).resolve().parent / '.research-locks'
    directory.mkdir(exist_ok=True)
    path = directory / session_id.replace(':', '_')
    with path.open('a+b') as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            import fcntl
        except ImportError:
            import msvcrt
            acquire = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            release = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            acquire = lambda: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = lambda: fcntl.flock(handle, fcntl.LOCK_UN)
        try:
            acquire()
        except OSError as exc:
            raise ResearchSessionBusy(session_id) from exc
        try:
            yield
        finally:
            handle.seek(0)
            release()
    # Do not unlink: waiters must continue to lock the same inode.
