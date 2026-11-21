"""Utilities for manipulating Wine"""

import os
import re
import signal
from collections import OrderedDict
from gettext import gettext as _

from qtris.exceptions import MissingExecutableError, UnspecifiedVersionError
from qtris.util import cache_single, system
from qtris.util.log import logger
from qtris.util.process import Process
from qtris.util.wine import proton

# Proton only runs 64-bit prefixes
WINE_DEFAULT_ARCH: str = "win64"
# The configured version that means GE-Proton Latest, which umu keeps up to date
GE_PROTON_LATEST: str = "ge-proton"


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
    # happens for GE-Proton Latest before umu first downloads it
    return True


@cache_single
def get_installed_wine_versions() -> list[str]:
    """Return the Wine versions that can be chosen, in presentation order: GE-Proton
    Latest, then the installed Proton builds."""
    return [GE_PROTON_LATEST, *proton.list_proton_versions()]


def get_wine_version_label(version: str) -> str:
    """Return how a Wine version is shown to the user."""
    return _("GE-Proton Latest") if version == GE_PROTON_LATEST else version


def get_proton_version_or_default(version: str | None) -> str:
    """Return the Proton build a configured Wine version names, using the bare release
    tag for GE-Proton. Anything that isn't Proton, such as system Wine, an old Wine build
    or a custom executable, gives the default, GE-Proton Latest."""
    if not version or version == GE_PROTON_LATEST:
        return GE_PROTON_LATEST
    tag = proton.get_proton_release_tag(version)
    if tag:
        return tag
    if proton.is_proton_version(version):
        return version
    logger.debug("Wine version '%s' isn't a Proton build; using GE-Proton Latest", version)
    return GE_PROTON_LATEST


def clear_wine_version_cache() -> None:
    get_installed_wine_versions.cache_clear()
    proton.get_proton_versions.cache_clear()
    proton.get_umu_path.cache_clear()


def get_runner_files_dir_for_version(version: str) -> str | None:
    """Return the root of a Proton build's Wine files, the directory holding 'bin', or
    None if the version names no installed Proton build."""
    if version == GE_PROTON_LATEST:
        return proton.get_ge_proton_latest_files_dir()
    if proton.is_proton_version(version):
        return proton.get_proton_wine_files_dir(version)
    return None


def get_wine_path_for_version(version: str) -> str:
    """Return the program that runs a Wine version: umu for GE-Proton Latest, and the
    wine executable of any other Proton build."""
    if not version:
        raise UnspecifiedVersionError(_("The Wine version must be specified."))

    if version == GE_PROTON_LATEST:
        return proton.get_umu_path()
    if proton.is_proton_version(version):
        return proton.get_proton_wine_path(version)
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
