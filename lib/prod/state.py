"""Local state under `<Products>/.prod/state/`: lock, history.

Commands run by an account other than the owner (e.g. amore2 running `prodman verify`) cannot write here;
for them history is skipped and the lock is taken on a read-only descriptor if the lock file exists.
"""

import contextlib
import datetime
import errno
import fcntl
import json
import os
import pwd
import socket
import tempfile

from .errors import LockBusy


def now_iso():
    """Current local time, ISO 8601 with time zone, seconds precision."""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def hostname():
    return socket.gethostname().split(".")[0]


def username():
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return str(os.getuid())


def atomic_write(path, data, mode=0o644):
    """Write data to path atomically (temporary file in the same directory + fsync + rename)."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def lock(paths, exclusive):
    """flock on state/lock without waiting. Raises LockBusy if another prod holds a conflicting lock."""
    fd = None
    try:
        os.makedirs(paths.state, exist_ok=True)
        fd = os.open(paths.lock, os.O_RDWR | os.O_CREAT, 0o644)
    except PermissionError:
        if exclusive:
            raise
        try:
            fd = os.open(paths.lock, os.O_RDONLY)
        except FileNotFoundError:
            fd = None  # read-only user and no lock file yet: nothing to lock against
    try:
        if fd is not None:
            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except OSError as e:
                if e.errno in (errno.EAGAIN, errno.EACCES):
                    raise LockBusy("another prod is running on this node; try again later") from None
                raise
        yield
    finally:
        if fd is not None:
            os.close(fd)


def record(paths, tool, command, **fields):
    """Append one event to history.jsonl. Returns False if history is not writable (other accounts)."""
    event = {"time": now_iso(), "host": hostname(), "user": username(), "tool": tool, "command": command}
    event.update(fields)
    line = (json.dumps(event, ensure_ascii=False, sort_keys=False) + "\n").encode("utf-8")
    try:
        os.makedirs(paths.state, exist_ok=True)
        fd = os.open(paths.history, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    except PermissionError:
        return False
    try:
        os.write(fd, line)  # a single append write keeps lines intact
    finally:
        os.close(fd)
    return True


def read_history(paths):
    """Return history events (oldest first). Lines that do not parse are skipped."""
    events = []
    try:
        with open(paths.history, encoding="utf-8") as f:
            for line in f:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    return events

