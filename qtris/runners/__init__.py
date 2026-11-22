"""Runner loaders"""

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

from qtris import settings
from qtris.exceptions import MisconfigurationError, QtrisError

if TYPE_CHECKING:
    from qtris.runners.runner import Runner

# The runners a new profile starts with turned on; the others are opt-in
DEFAULT_RUNNERS = ("linux", "web", "wine")
# The runners of the Steam and Flathub sources' games, which aren't offered as runners
INTERNAL_RUNNERS = ("steam", "flatpak")

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


def is_internal_runner(runner_name: str) -> bool:
    """True if a runner only runs the games of a source, and isn't offered as a runner."""
    return runner_name in INTERNAL_RUNNERS


def is_runner_enabled(runner_name: str) -> bool:
    """True if the user turned a runner on, or it's one of the defaults they haven't turned
    off. Internal runners are always on, since their source's games need them."""
    if is_internal_runner(runner_name):
        return True
    return settings.read_bool_setting(runner_name, default=runner_name in DEFAULT_RUNNERS, section="runners")


def set_runner_enabled(runner_name: str, enabled: bool) -> None:
    if not is_valid_runner_name(runner_name) or is_internal_runner(runner_name):
        raise InvalidRunnerError("Invalid runner name '%s'" % runner_name)
    settings.write_setting(runner_name, enabled, section="runners")


def get_enabled() -> list["Runner"]:
    """Return the runners offered to the user that are turned on (class instances)."""
    return [import_runner(runner_name)() for runner_name in get_user_runner_names() if is_runner_enabled(runner_name)]


def get_runner_names() -> list[str]:
    return __all__


def get_user_runner_names() -> list[str]:
    """Return the names of the runners offered to the user, leaving out internal ones."""
    return [runner_name for runner_name in __all__ if not is_internal_runner(runner_name)]


def is_valid_runner_name(runner_name: str) -> bool:
    return runner_name in __all__


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
