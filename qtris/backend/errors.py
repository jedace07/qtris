"""The errors the backend's calls raise. Each is a QtrisError, whose message is
meant for the user."""

from qtris.exceptions import MisconfigurationError, QtrisError
from qtris.runners import InvalidRunnerError, RunnerInstallationError

__all__ = ["InvalidRunnerError", "MisconfigurationError", "QtrisError", "RunnerInstallationError"]
