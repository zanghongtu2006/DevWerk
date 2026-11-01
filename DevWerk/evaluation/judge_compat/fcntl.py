"""Thread lock substitute for upstream Judge's POSIX flock on Windows.

The evaluation CLI runs one Judge process per output directory. This shim only
serializes that process's rubric writer threads; it does not change scoring.
"""

from threading import RLock

LOCK_EX = 2
LOCK_UN = 8
_lock = RLock()


def flock(_fd: int, operation: int) -> None:
    if operation == LOCK_EX:
        _lock.acquire()
    elif operation == LOCK_UN:
        _lock.release()
    else:
        raise ValueError(f"unsupported flock operation: {operation}")
