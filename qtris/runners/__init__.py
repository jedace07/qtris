"""Runner loaders"""

from __future__ import annotations

__all__ = [
    "flatpak",
    "linux",
    "steam",
    "web",
    "wine",
]

from collections.abc import Callable
from types import ModuleType
from typing import TYPE_CHECKING, Any, cast

from qtris.exceptions import MisconfigurationError, QtrisError

if TYPE_CHECKING:
    from qtris.runners.runner import Runner

# The built-in platforms for manually added games, in sidebar order
LOCAL_RUNNERS = ("linux", "wine", "web")

_cached_runner_human_names: dict[str, str] = {}


class InvalidRunnerError(MisconfigurationError):
    """Raise if a runner name is used that is not known to Qtris."""


class RunnerInstallationError(QtrisError):
    """Raised if the attempt to install a runner fails, perhaps because
    of invalid data from a server."""


class NonInstallableRunnerError(QtrisError):
    """Raised for a runner that Qtris cannot install, like Steam.
    These must be installed separately."""


def get_runner_module(runner_name: str) -> ModuleType:
    if not is_valid_runner_name(runner_name):
        raise InvalidRunnerError("Invalid runner name '%s'" % runner_name)
    module = __import__("qtris.runners.%s" % runner_name, globals(), locals(), [runner_name], 0)
    if not module:
        raise InvalidRunnerError("Runner module for '%s' could not be imported." % runner_name)
    return module


def import_runner(runner_name: str) -> type["Runner"]:
    """Dynamically import a runner class."""
    runner_module = get_runner_module(runner_name)
    return cast(type["Runner"], getattr(runner_module, runner_name))


def import_task(runner: str, task: str) -> Callable[..., Any]:
    """Return a runner task."""
    runner_module = get_runner_module(runner)
    return cast(Callable[..., Any], getattr(runner_module, task))


def get_runner_names() -> list[str]:
    return __all__


def get_user_runner_names() -> list[str]:
    """Return the names of the runners offered to the user, leaving out internal ones."""
    return list(LOCAL_RUNNERS)


def is_valid_runner_name(runner_name: str) -> bool:
    return runner_name in __all__


def get_runner_platform_name(runner_name: str) -> str:
    """Return the local platform name, or the name of a store's runner."""
    runner = import_runner(runner_name)
    if runner_name in LOCAL_RUNNERS:
        return next(iter(runner.platform_dict), runner.human_name)
    return runner.human_name


def get_runner_human_name(runner_name: str) -> str:
    """Returns a human-readable name for a runner; as a convenience, if the name
    is falsy (None or blank) this returns an empty string. Provides caching for the
    names."""
    if runner_name:
        if runner_name not in _cached_runner_human_names:
            try:
                _cached_runner_human_names[runner_name] = import_runner(runner_name)().human_name
            except InvalidRunnerError:
                _cached_runner_human_names[runner_name] = runner_name  # an obsolete runner
        return _cached_runner_human_names[runner_name]

    return ""
