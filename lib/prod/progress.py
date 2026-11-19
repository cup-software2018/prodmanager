"""Progress lines for long operations (sync, verify, release), written to stderr.

stdout keeps only the final summary, so that it can be piped or logged as before; --quiet turns the
progress lines off. The live rsync progress line (rsync --info=progress2) is shown only when stderr is a
terminal, so that logs do not fill up with carriage returns.
"""

import sys
import time


class Progress:
    def __init__(self, stream=None, *, quiet=False):
        self.stream = sys.stderr if stream is None else stream
        self.quiet = quiet

    @property
    def live(self):
        """The stream for a live rsync progress line, or None when it should not be shown."""
        if self.quiet:
            return None
        try:
            return self.stream if self.stream.isatty() else None
        except (AttributeError, ValueError):
            return None

    def line(self, text):
        if not self.quiet:
            print(text, file=self.stream, flush=True)

    def item(self, name, version, text):
        self.line(f"  {name:<16} {version:<24} {text}")


SILENT = Progress(quiet=True)


class Timer:
    def __init__(self):
        self.started = time.monotonic()

    def __str__(self):
        return duration(time.monotonic() - self.started)


def duration(seconds):
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, seconds = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def size(n):
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"


def total_size(entries):
    return sum(e.size or 0 for e in entries if e.kind == "f")
