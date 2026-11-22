"""Runner loaders"""

__all__ = [
    "eden",
    "flatpak",
    "libretro",
    "linux",
    "pico8",
    "ryubing",
    "shadps4",
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
DEFAULT_RUNNERS = ("linux", "wine")
# Source runners and legacy emulator integrations needed by existing game configurations
INTERNAL_RUNNERS = ("steam", "flatpak", "eden", "ryubing", "shadps4", "86box", "basiliskii", "gargoyle", "ruffle")

ADDON_RUNNERS: dict[str, type["Runner"]] = {}
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
    if runner_name in ADDON_RUNNERS:
        return ADDON_RUNNERS[runner_name]

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


def inject_runners(runners: dict[str, type["Runner"]]) -> None:
    for runner_name in runners:
        if runner_name not in __all__:
            ADDON_RUNNERS[runner_name] = runners[runner_name]
            __all__.append(runner_name)
    _cached_runner_human_names.clear()


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
