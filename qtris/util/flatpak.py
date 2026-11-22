"""Discover and run host Flatpaks, leaving installation to the desktop."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from gettext import gettext as _
from pathlib import Path
from typing import TYPE_CHECKING

from qtris.exceptions import QtrisError, UnavailableRunnerError
from qtris.util import cache_single
from qtris.util.system import read_process_output

if TYPE_CHECKING:
    from collections.abc import Iterable

_APP_ID = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){2,}")

# Variables that point at libraries or drivers outside the app's sandbox, which would break it
UNFORWARDED_ENV = frozenset(
    {
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "PATH",
        "PYTHONPATH",
        "VK_ICD_FILENAMES",
        "VK_DRIVER_FILES",
        "__GL_SHADER_DISK_CACHE_PATH",
    }
)

# Folders that belong to a sandbox or to the system, which Flatpak won't share with an app
_UNSHAREABLE_ROOTS = (Path("/app"), Path("/usr"), Path("/etc"), Path("/proc"), Path("/sys"))


class FlatpakError(QtrisError):
    """Raised when inspecting host apps or opening the app store fails."""


@cache_single
def get_executable() -> str | None:
    """Return the executable used to access Flatpak. None if Flatpak is not installed.

    In the case where Qtris is a Flatpak, we use flatpak-spawn. Some systems install
    flatpak-spawn outside of any sandbox too, where it can't reach the host's Flatpak.
    """
    if os.path.exists("/.flatpak-info"):
        return shutil.which("flatpak-spawn")
    return shutil.which("flatpak")


def is_installed() -> bool:
    """Returns Flatpak is installed"""
    return bool(get_executable())


def get_command() -> list[str]:
    """Return the full command used to interact with Flatpak."""
    exe = get_executable()
    if not exe:
        raise UnavailableRunnerError(_("Flatpak is not installed"))
    if "flatpak-spawn" in exe:
        return [exe, "--host", "flatpak"]
    return [exe]


def get_installed_apps() -> set[str]:
    if not is_installed():
        return set()

    command = [*get_command(), "list", "--app", "--columns=application"]
    try:
        package_list = read_process_output(command, timeout=10, error_result=None)
    except (OSError, subprocess.SubprocessError) as error:
        raise FlatpakError(_("Could not list the Flatpak apps installed on the host.")) from error
    return {appid for appid in package_list.splitlines() if is_app_id(appid)}


def is_app_installed(appid: str | None) -> bool:
    """Return whether an app is installed"""
    if not appid:
        return False
    return appid in get_installed_apps()


def is_app_id(appid: str) -> bool:
    return not appid.startswith("-") and _APP_ID.fullmatch(appid) is not None


def get_app_dir(appid: str) -> Path:
    """Return the folder that holds an app's own configuration and data, as the host sees it."""
    return Path.home() / ".var" / "app" / appid


def get_shareable_paths(paths: Iterable[str]) -> list[str]:
    """Return the existing folders that hold the paths, in the order given, for sharing with an app."""
    folders: list[str] = []
    for path in paths:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute() or not candidate.exists():
            continue
        folder = candidate if candidate.is_dir() else candidate.parent
        if folder == Path("/") or any(folder.is_relative_to(root) for root in _UNSHAREABLE_ROOTS):
            continue
        if str(folder) not in folders:
            folders.append(str(folder))
    return folders


def get_run_command(appid: str, filesystems: Iterable[str] = ()) -> list[str]:
    """Return the command that launches a Flatpak app, sharing the folders given with it."""
    # The explicit mode keeps a ':' in a folder's name from being read as one
    shares = [f"--filesystem={folder}:rw" for folder in get_shareable_paths(filesystems)]
    return [*get_command(), "run", *shares, appid]


def add_environment(command: list[str], appid: str, env: dict[str, str]) -> list[str]:
    """Pass the game's environment to the app a 'flatpak run' command launches.

    When Qtris is itself a Flatpak, flatpak-spawn starts the app with the host's environment,
    so the variables have to be given to 'flatpak run'. Variables that describe libraries or
    drivers outside the app's sandbox are removed from env and not passed on."""
    try:
        run_index = command.index("run")
        app_index = command.index(appid, run_index)
    except ValueError:
        return command
    for key in UNFORWARDED_ENV:
        env.pop(key, None)
    variables = [f"--env={key}={value}" for key, value in env.items()]
    return [*command[:app_index], *variables, *command[app_index:]]


def open_app_store(appid: str) -> None:
    """Open an app in the host's default AppStream handler."""
    if not is_app_id(appid):
        raise FlatpakError(_("Invalid Flatpak application ID: %s") % appid)
    command = ["xdg-open", f"appstream://{appid}"]
    try:
        if os.path.exists("/.flatpak-info"):
            command = [*get_command()[:-1], *command]
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError, UnavailableRunnerError) as error:
        raise FlatpakError(_("Could not open %s in the host's app store.") % appid) from error
