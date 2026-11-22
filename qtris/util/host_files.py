"""Read and write files in other apps' folders, even from inside the Flatpak sandbox.

Flatpak covers ~/.var/app with a tmpfs, whatever access the sandbox has to the home
folder: other apps' folders look empty there, and what's written to them is silently
lost. From the sandbox, these functions reach those folders with `flatpak-spawn --host`,
and they use ordinary file access for every other path.
"""

from __future__ import annotations

import os
import subprocess
from gettext import gettext as _
from pathlib import Path

from qtris.exceptions import QtrisError
from qtris.util import system

type StrPath = str | os.PathLike[str]

# What the scripts exit with when the path isn't there, to tell that from a failure to reach the host
_ABSENT = 44
# Libretro cores can be hundreds of megabytes
_TIMEOUT_SECONDS = 300

_EXISTS = f'[ -e "$1" ] || [ -L "$1" ] || exit {_ABSENT}'
_READ = f'[ -f "$1" ] || exit {_ABSENT}; exec cat -- "$1"'
# Publish only a complete copy, as an interrupted one would pass for an installed core
_WRITE = """
umask 022
mkdir -p -- "$(dirname -- "$1")" || exit 1
target=$(readlink -f -- "$1") || exit 1
temporary="$target.qtris-$$"
trap 'rm -f -- "$temporary"' EXIT
cat > "$temporary" || exit 1
[ "$(wc -c < "$temporary")" -eq "$2" ] || exit 1
if [ -e "$target" ]; then chmod --reference="$target" -- "$temporary" || exit 1; fi
mv -f -- "$temporary" "$target"
"""


class HostFileError(QtrisError):
    """Raised when a file in another app's folder can't be reached through the host."""


def _get_host_path(path: StrPath) -> str | None:
    """Return a path as the host should be given it, or None if the sandbox sees it itself."""
    if not os.path.exists("/.flatpak-info"):
        return None
    resolved = Path(os.path.expanduser(path)).resolve()
    apps = (Path.home() / ".var" / "app").resolve()
    if resolved == apps or not resolved.is_relative_to(apps):
        return None
    if resolved.relative_to(apps).parts[0] == os.environ.get("FLATPAK_ID"):
        return None
    return str(resolved)


def _run(script: str, *arguments: str, data: bytes = b"") -> bytes | None:
    """Run a script on the host, returning its output, or None if the path isn't there."""
    # Without a directory, flatpak-spawn passes on the sandbox's, which the host may not have
    command = ["flatpak-spawn", "--host", "--watch-bus", "--directory=/", "sh", "-c", script, "sh", *arguments]
    message = _("Could not access %s on the host.") % arguments[0]
    try:
        result = subprocess.run(command, input=data, capture_output=True, timeout=_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as error:
        raise HostFileError(message) from error
    if result.returncode == _ABSENT:
        return None
    if result.returncode:
        details = os.fsdecode(result.stderr).strip()
        raise HostFileError(f"{message}\n{details}" if details else message)
    return result.stdout


def exists(path: StrPath) -> bool:
    if host := _get_host_path(path):
        return _run(_EXISTS, host) is not None
    return system.path_exists(os.fspath(path))


def read_bytes(path: StrPath) -> bytes | None:
    """Return a file's contents, or None if there's no such file."""
    if host := _get_host_path(path):
        return _run(_READ, host)
    local = Path(os.path.expanduser(path))
    return local.read_bytes() if local.is_file() else None


def write_bytes(path: StrPath, data: bytes) -> None:
    """Write a file, creating its folder."""
    if host := _get_host_path(path):
        _run(_WRITE, host, str(len(data)), data=data)
        return
    local = Path(os.path.expanduser(path))
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(data)
