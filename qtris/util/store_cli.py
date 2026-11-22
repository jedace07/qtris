"""Run the command-line store clients Qtris downloads with its other components.

Legendary (the Epic Games Store) and Nile (Amazon Games) each keep their sign-in,
library and installed games in a folder of Qtris's own, and both report downloads
in the same "= Progress:" format.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from gettext import gettext as _
from typing import Any

from qtris import settings
from qtris.exceptions import QtrisError
from qtris.util.log import logger

# "= Progress: 12.34% (123/456), Running for 00:00:10, ETA: 00:01:00" (legendary),
# "= Progress: 12.34 123/456, Running for: 00:00:10, ETA: 00:01:00" (Nile)
PROGRESS_RE = re.compile(r"= Progress: (?P<percent>[\d.]+)%?.*ETA: (?P<eta>\d+:\d+:\d+)")
SPEED_RE = re.compile(r"\+ Download\s+- (?P<speed>[\d.]+) MiB/s")
ERROR_RE = re.compile(r"\b(ERROR|CRITICAL|FATAL)\b")
EXCEPTION_RE = re.compile(r"^\w+(Error|Exception): ")

_TIMEOUT_SECONDS = 300


class StoreClientError(QtrisError):
    """A store client is missing, or failed."""


@dataclass
class DownloadProgress:
    fraction: float = 0.0
    speed_mib: float = 0.0
    eta: str = ""

    def parse_line(self, line: str) -> bool:
        """Read one line of output; True if it said how far the download has got."""
        if match := PROGRESS_RE.search(line):
            self.fraction = min(1.0, float(match["percent"]) / 100)
            self.eta = match["eta"]
            return True
        if match := SPEED_RE.search(line):
            self.speed_mib = float(match["speed"])
            return True
        return False


@dataclass(frozen=True)
class StoreClient:
    name: str
    # The variable that points the client at its folder, and the folder it names
    config_variable: str
    config_value: str

    @property
    def binary(self) -> str:
        binary = os.path.join(settings.RUNTIME_DIR, self.name, self.name)
        if not os.access(binary, os.X_OK):
            raise StoreClientError(
                _("%s hasn't been downloaded yet. Check for updates in Preferences → Updates, then try again.")
                % self.name
            )
        return binary

    def get_env(self) -> dict[str, str]:
        return {**os.environ, self.config_variable: self.config_value}

    def run(self, args: list[str], timeout: float = _TIMEOUT_SECONDS, *, sensitive: bool = False) -> str:
        """Run the client and return what it printed; raise StoreClientError with its
        error messages if it fails."""
        command = [self.binary, *args]
        logger.debug("Running %s %s", self.name, args[0] if args else "")
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, errors="replace", env=self.get_env(), timeout=timeout
            )
        except subprocess.TimeoutExpired:
            raise StoreClientError(_("Timed out waiting for %s.") % self.name) from None
        except OSError as ex:
            raise StoreClientError(_("%s could not run: %s") % (self.name, ex)) from ex
        if result.returncode != 0:
            # Sign-in commands can include credentials in their error output
            output = "" if sensitive else result.stderr + result.stdout
            raise StoreClientError(_get_error(self.name, output, result.returncode))
        return result.stdout

    def run_json(self, args: list[str], timeout: float = _TIMEOUT_SECONDS) -> Any:
        output = self.run(args, timeout)
        # Some commands log before the JSON; it's on the last line that holds any
        for line in reversed(output.strip().splitlines()):
            if line.startswith(("{", "[")):
                try:
                    return json.loads(line)
                except ValueError:
                    break
        try:
            return json.loads(output)
        except ValueError as ex:
            raise StoreClientError(_("%s returned something Qtris doesn't understand.") % self.name) from ex

    def download(self, args: list[str], progress_callback: Callable[[DownloadProgress], None]) -> None:
        """Run a download, reporting its progress, until it ends."""
        command = [self.binary, *args]
        logger.info("Running %s %s", self.name, " ".join(args))
        progress = DownloadProgress()
        output: list[str] = []
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                errors="replace",
                env=self.get_env(),
            )
        except OSError as ex:
            raise StoreClientError(_("%s could not run: %s") % (self.name, ex)) from ex

        def read() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                line = line.rstrip()
                if not line:
                    continue
                logger.debug("[%s] %s", self.name, line)
                output.append(line)
                del output[:-200]
                if progress.parse_line(line):
                    progress_callback(progress)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        process.wait()
        reader.join(timeout=5)
        if process.returncode != 0:
            raise StoreClientError(_get_error(self.name, "\n".join(output), process.returncode))


def _get_error(name: str, output: str, returncode: int) -> str:
    lines = output.splitlines()
    errors = [line.strip() for line in lines if ERROR_RE.search(line)]
    if errors:
        return "\n".join(errors[-5:])
    # The clients are Python programs; when one crashes, the last line of the
    # traceback says why, such as legendary's "No saved credentials"
    if exceptions := [line for line in lines if EXCEPTION_RE.match(line)]:
        return exceptions[-1].partition(": ")[2] or exceptions[-1]
    return _("%(client)s failed (exit code %(code)d).") % {"client": name, "code": returncode}
