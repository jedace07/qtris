"""See and signal the processes of the host, even from inside the Flatpak sandbox.

Qtris's Flatpak has a PID namespace of its own, so /proc only shows its own processes.
Games that another program starts, such as Steam's, run outside it; from the sandbox,
`flatpak-spawn --host` lists and signals them with the host's ps and kill.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass

from qtris.util.log import logger

_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class HostProcess:
    pid: int
    ppid: int
    cmdline: str


def _host_command() -> list[str] | None:
    """Return the prefix that runs a command on the host, [] outside a sandbox, or
    None when the host can't be reached."""
    if not os.path.exists("/.flatpak-info"):
        return []
    spawn = shutil.which("flatpak-spawn")
    return [spawn, "--host"] if spawn else None


def list_processes() -> list[HostProcess]:
    """Return every process of the host that Qtris can see."""
    prefix = _host_command()
    if prefix is None:
        return []
    if not prefix:
        return _read_proc()
    try:
        output = subprocess.run(
            [*prefix, "ps", "-e", "-ww", "-o", "pid=,ppid=,args="],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_TIMEOUT_SECONDS,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as ex:
        logger.warning("Could not list the host's processes: %s", ex)
        return []
    return list(parse_ps_output(output))


def parse_ps_output(output: str) -> Iterable[HostProcess]:
    for line in output.splitlines():
        fields = line.split(None, 2)
        if len(fields) >= 2 and fields[0].isdigit() and fields[1].isdigit():
            yield HostProcess(int(fields[0]), int(fields[1]), fields[2] if len(fields) == 3 else "")


def _read_proc() -> list[HostProcess]:
    processes = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/cmdline", "rb") as cmdline_file:
                cmdline = cmdline_file.read().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
            with open(f"/proc/{name}/stat", encoding="utf-8", errors="replace") as stat_file:
                # The command name in parentheses may hold spaces; the fields after it don't
                ppid = int(stat_file.read().rpartition(")")[2].split()[1])
        except OSError, IndexError, ValueError:
            continue
        processes.append(HostProcess(int(name), ppid, cmdline))
    return processes


def get_descendants(roots: Iterable[int], processes: Iterable[HostProcess]) -> set[int]:
    """Return the PIDs of every process under the roots, not counting the roots."""
    children: dict[int, list[int]] = {}
    for process in processes:
        children.setdefault(process.ppid, []).append(process.pid)
    descendants: set[int] = set()
    pending = list(roots)
    while pending:
        for child in children.get(pending.pop(), ()):
            if child not in descendants:
                descendants.add(child)
                pending.append(child)
    return descendants


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
