"""Utilities for manipulating Wine"""

from __future__ import annotations

import os
import re
import signal
from collections import OrderedDict
from gettext import gettext as _
from itertools import count
from pathlib import Path
from typing import TYPE_CHECKING

from qtris.exceptions import MissingExecutableError, QtrisError, UnspecifiedVersionError
from qtris.util import cache_single, system
from qtris.util.log import logger
from qtris.util.process import Process
from qtris.util.strings import slugify
from qtris.util.wine import proton, proton_releases

if TYPE_CHECKING:
    from qtris.config import SystemConfigDict

# Proton only runs 64-bit prefixes
WINE_DEFAULT_ARCH: str = "win64"
# The configured versions that mean GE-Proton Latest, the default, and Proton-CachyOS
# Latest, rolling builds Qtris keeps up to date
GE_PROTON_LATEST: str = proton_releases.GE_PROTON_LATEST
PROTON_CACHYOS_LATEST: str = proton_releases.PROTON_CACHYOS_LATEST
PREFIX_ROOT_OPTION = "prefix_path"


def get_prefix_root(system_config: SystemConfigDict | None = None) -> Path:
    if system_config is None:
        from qtris.config import QtrisConfig

        system_config = QtrisConfig().system_config
    configured = system_config.get(PREFIX_ROOT_OPTION)
    return Path(configured or "~/Games/Prefixes").expanduser()


def available_prefix_path(root: Path, game_name: str) -> Path:
    name = slugify(game_name)
    for number in count(1):
        path = root / (name if number == 1 else f"{name}-{number}")
        if not path.exists() and not path.is_symlink():
            return path
    raise AssertionError("No available prefix directory")


def reserve_game_prefix(root: Path, game_name: str) -> str:
    try:
        if not root.is_absolute():
            raise QtrisError(_("Enter an absolute path for the Proton prefix directory."))
        root.mkdir(parents=True, exist_ok=True)
        while True:
            path = available_prefix_path(root, game_name)
            try:
                path.mkdir()
            except FileExistsError:
                continue
            return str(path.resolve())
    except OSError as error:
        raise QtrisError(_("Could not create a Proton prefix directory in %s") % root) from error


def remove_empty_prefix(prefix: str) -> None:
    try:
        Path(prefix).rmdir()
    except OSError as error:
        logger.debug("Could not remove unused prefix directory %s: %s", prefix, error)


def detect_arch(prefix_path: str | None = None, wine_path: str | None = None) -> str:
    """Return the architecture of a Wine prefix. Proton only runs 64-bit prefixes, so
    that's what a Proton build gets; otherwise an existing prefix is detected from its
    registry, and a new one is 64-bit."""
    if wine_path and proton.is_proton_path(wine_path):
        return "win64"
    if prefix_path and is_prefix_directory(prefix_path):
        return detect_prefix_arch(prefix_path)
    return WINE_DEFAULT_ARCH


def is_prefix_directory(prefix_path: str) -> bool:
    """Detects if a path is ther oot of a Wine prefix; to be one, it must contain
    a 'system.reg' file."""
    if not prefix_path:
        return False

    prefix_path = os.path.expanduser(prefix_path)
    registry_path = os.path.join(prefix_path, "system.reg")
    return os.path.isdir(prefix_path) and os.path.isfile(registry_path)


def detect_prefix_arch(prefix_path: str) -> str:
    """Return the architecture of the prefix found in `prefix_path`"""
    if not is_prefix_directory(prefix_path):
        raise RuntimeError("Prefix not found: %s" % prefix_path)

    prefix_path = os.path.expanduser(prefix_path)
    registry_path = os.path.join(prefix_path, "system.reg")
    with open(registry_path, "r", encoding="utf-8") as registry:
        for _line_no in range(5):
            line = registry.readline()
            if "win64" in line:
                return "win64"
            if "win32" in line:
                return "win32"
    logger.error("Failed to detect Wine prefix architecture in %s; defaulting to %s.", prefix_path, WINE_DEFAULT_ARCH)
    return WINE_DEFAULT_ARCH


def prepare_wine_prefix(directory: str) -> str:
    """Resolve a chosen Wine prefix and make its parent available to Proton."""
    try:
        path = Path(directory).expanduser()
        if not path.is_absolute():
            raise QtrisError(_("Enter an absolute path for the Wine prefix."))
        path = path.resolve()
        if is_prefix_directory(str(path)):
            if detect_prefix_arch(str(path)) == "win32":
                raise QtrisError(_("Proton needs a 64-bit Wine prefix."))
        elif path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise QtrisError(_("Choose a Wine prefix, an empty folder, or a new path."))
        path.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, RuntimeError, ValueError) as error:
        raise QtrisError(_("Could not open the Wine prefix path: %s") % directory) from error
    return str(path)


def set_drive_path(prefix: str, letter: str, path: str) -> None:
    """Changes the path to a Wine drive"""
    dosdevices_path = os.path.join(prefix, "dosdevices")
    if not system.path_exists(dosdevices_path):
        raise OSError("Invalid prefix path %s" % prefix)
    drive_path = os.path.join(dosdevices_path, letter + ":")
    if system.path_exists(drive_path):
        os.remove(drive_path)
    logger.debug("Linking %s to %s", drive_path, path)
    system.create_symlink(path, drive_path)


def is_winewayland_available(runner_version: str) -> bool:
    """Check if winewayland graphics driver available"""
    files_dir = get_runner_files_dir_for_version(runner_version)
    if files_dir:
        winewayland_paths = (
            "lib64/wine/x86_64-windows/winewayland.drv",
            "lib64/wine/i386-windows/winewayland.drv",
            "lib/wine/x86_64-windows/winewayland.drv",
            "lib/wine/i386-windows/winewayland.drv",
        )
        return any(os.path.exists(os.path.join(files_dir, winewayland_path)) for winewayland_path in winewayland_paths)

    # If there's no file dir to check, we assume Wayland is available; this
    # happens for a rolling build before it is installed
    return True


@cache_single
def get_installed_wine_versions() -> list[str]:
    """Return the Wine versions that can be chosen, in presentation order: the rolling
    builds, GE-Proton Latest first, which are installed when a game needs them, then the
    installed Proton builds."""
    families = proton_releases.get_family_names()
    rolling = [rolling.version for rolling in proton_releases.ROLLING if rolling.family.name in families]
    return [*rolling, *proton.list_proton_versions()]


def get_wine_version_label(version: str) -> str:
    """Return how a Wine version is shown to the user: Steam's own Proton builds by the
    names Steam gives them, and the releases Qtris downloads by their release names."""
    return proton.get_steam_proton_label(version) or proton_releases.get_release_label(version)


def get_proton_version_or_default(version: str | None) -> str:
    """Return the Proton build a configured Wine version names, using the release name
    for managed Proton builds. Anything that isn't Proton, such as system Wine, an old Wine build
    or a custom executable, gives the default, GE-Proton Latest."""
    if not version:
        return GE_PROTON_LATEST
    if proton_releases.get_rolling(version):
        return version
    release = proton_releases.get_release_of_build(version)
    if release:
        return release
    if proton.is_proton_version(version):
        return version
    logger.debug("Wine version '%s' isn't a Proton build; using GE-Proton Latest", version)
    return GE_PROTON_LATEST


def clear_wine_version_cache() -> None:
    get_installed_wine_versions.cache_clear()
    proton.get_proton_versions.cache_clear()
    proton.get_steam_proton_labels.cache_clear()
    proton.get_umu_path.cache_clear()


def get_runner_files_dir_for_version(version: str) -> str | None:
    """Return the root of a Proton build's Wine files, the directory holding 'bin', or
    None if the version names no installed Proton build."""
    if proton.is_proton_version(version):
        return proton.get_proton_wine_files_dir(version)
    return None


def get_wine_path_for_version(version: str) -> str:
    """Return the wine executable of an installed Proton build, which umu runs."""
    if not version:
        raise UnspecifiedVersionError(_("The Wine version must be specified."))

    if proton.is_proton_version(version):
        return proton.get_proton_wine_path(version)
    if rolling := proton_releases.get_rolling(version):
        raise MissingExecutableError(_("%s isn't installed yet.") % rolling.label)
    raise MissingExecutableError(_("Wine version '%s' isn't an installed Proton build.") % version)


def get_default_wine_version() -> str:
    """Return the default version of wine."""
    return GE_PROTON_LATEST


def get_real_executable(windows_executable: str, working_dir: str | None) -> tuple[str, list[str], str | None]:
    """Given a Windows executable, return the real program
    capable of launching it along with necessary arguments."""

    exec_name = windows_executable.lower()

    if exec_name.endswith(".msi"):
        return ("msiexec", ["/i", windows_executable], working_dir)

    if exec_name.endswith(".bat"):
        if not working_dir or os.path.dirname(windows_executable) == working_dir:
            working_dir = os.path.dirname(windows_executable)
            windows_executable = os.path.basename(windows_executable)
        return ("cmd", ["/C", windows_executable], working_dir)

    if exec_name.endswith(".lnk"):
        return ("start", ["/unix", windows_executable], working_dir)

    return (windows_executable, [], working_dir)


def get_overrides_env(overrides: dict[str, str]) -> str:
    """
    Output a string of dll overrides usable with WINEDLLOVERRIDES
    See: https://wiki.winehq.org/Wine_User%27s_Guide#WINEDLLOVERRIDES.3DDLL_Overrides
    """
    default_overrides = {"winemenubuilder": ""}
    overrides.update(default_overrides)
    override_buckets: dict[str, list] = OrderedDict(
        [("n,b", []), ("b,n", []), ("b", []), ("n", []), ("d", []), ("", [])]
    )
    for dll, value in overrides.items():
        if not value:
            value = ""
        value = value.replace(" ", "")
        value = value.replace("builtin", "b")
        value = value.replace("native", "n")
        value = value.replace("disabled", "")
        try:
            override_buckets[value].append(dll)
        except KeyError:
            logger.error("Invalid override value %s", value)
            continue

    override_strings = []
    for value, dlls in override_buckets.items():
        if not dlls:
            continue
        override_strings.append("{}={}".format(",".join(sorted(dlls)), value))
    return ";".join(override_strings)


WINE_PROCESS_PATTERN = re.compile(r"(wine|processid|\.exe)", re.IGNORECASE)


def kill_all_wine_processes():
    """Kill all Wine-related processes on the system.

    Returns the number of processes killed."""
    killed = 0
    for pid in system.get_running_pid_list():
        try:
            proc = Process(pid)
            name = proc.name or ""
            cmdline = proc.cmdline or ""
            if WINE_PROCESS_PATTERN.search(name) or WINE_PROCESS_PATTERN.search(cmdline):
                logger.info("Killing Wine process %s (%s)", pid, name)
                os.kill(pid, signal.SIGKILL)
                killed += 1
        except ProcessLookupError, PermissionError:
            continue
    logger.info("Killed %d Wine processes", killed)
    return killed
