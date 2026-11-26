"""prodman errors and exit codes."""


class ProdError(Exception):
    """Base class of all prod errors. exit_code becomes the process exit code."""

    exit_code = 1


class CheckFailed(ProdError):
    """A check found a problem (verify failure, hash mismatch, ...)."""

    exit_code = 1


class FormatError(ProdError):
    """A file is malformed (syntax error, missing field, rule violation)."""

    exit_code = 1


class UsageError(ProdError):
    """Command-line usage error."""

    exit_code = 2


class EnvError(ProdError):
    """Environment error (no config, server unreachable, role mismatch, ...)."""

    exit_code = 3


class LockBusy(ProdError):
    """Another prod is running."""

    exit_code = 4


class TreeError(ProdError):
    """A version directory violates the tree rules. violations is a list of (path, reason)."""

    exit_code = 1

    def __init__(self, violations):
        self.violations = list(violations)
        lines = [f"  {path}: {reason}" for path, reason in self.violations]
        super().__init__("%d tree rule violation(s):\n%s" % (len(self.violations), "\n".join(lines)))
