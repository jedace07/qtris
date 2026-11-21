"""Find and signal the processes of the host, even from inside the Flatpak sandbox.

Qtris's Flatpak has a PID namespace of its own, so /proc only shows its own processes.
Games that another program starts, such as Steam's, run outside it; from the sandbox,
`flatpak-spawn --host` searches and signals them with the host's grep and kill.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from collections.abc import Iterable

from qtris.util.log import logger

_TIMEOUT_SECONDS = 10


def _host_command() -> list[str] | None:
    """Return the prefix that runs a command on the host, [] outside a sandbox, or
    None when the host can't be reached."""
    if not os.path.exists("/.flatpak-info"):
        return []
    spawn = shutil.which("flatpak-spawn")
    return [spawn, "--host"] if spawn else None


def find_by_environment(entries: Iterable[str]) -> set[int]:
    """Return the PIDs of the host's processes whose environment holds any of the
    entries ("NAME=value"). The environment survives where the process tree doesn't,
    such as a program a Flatpak starts in a new sandbox through the Flatpak portal."""
    entries = list(entries)
    prefix = _host_command()
    if prefix is None or not entries:
        return set()
    if not prefix:
        return _find_by_environment(set(entries))
    patterns = [argument for entry in entries for argument in ("-e", entry)]
    # One grep over every process's environment, its entries split at NULs
    script = 'exec grep -lsxzF "$@" /proc/[0-9]*/environ'
    try:
        output = subprocess.run(
            [*prefix, "sh", "-c", script, "sh", *patterns],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_TIMEOUT_SECONDS,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError) as ex:
        logger.warning("Could not search the host's processes: %s", ex)
        return set()
    return {int(parts[2]) for line in output.split() if len(parts := line.split("/")) > 2 and parts[2].isdigit()}


def _find_by_environment(entries: set[str]) -> set[int]:
    pids = set()
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/environ", "rb") as environ_file:
                environ = environ_file.read().decode("utf-8", "replace").split("\0")
        except OSError:
            continue
        if entries.intersection(environ):
            pids.add(int(name))
    return pids


def send_signal(sig: signal.Signals, pids: Iterable[int]) -> None:
    """Send a signal to host processes, ignoring those that are gone already."""
    pids = sorted(set(pids))
    prefix = _host_command()
    if not pids or prefix is None:
        return
    if not prefix:
        for pid in pids:
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError) as ex:
                logger.debug("Could not signal process %s: %s", pid, ex)
        return
    # kill exits with an error when some of the processes are gone, which is fine
    command = [*prefix, "kill", "-s", sig.name.removeprefix("SIG"), "--", *(str(pid) for pid in pids)]
    try:
        subprocess.run(command, capture_output=True, timeout=_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError) as ex:
        logger.warning("Could not signal the host's processes %s: %s", pids, ex)
