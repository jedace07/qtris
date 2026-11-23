"""Bridge module for invoking heroic-gogdl.

gogdl is a tool from the Heroic Games Launcher project that downloads
GOG games directly from GOG's depot/CDN system. Qtris downloads the
pre-built binary and keeps it updated with its other components, such
as umu and Winetricks.

This module handles:
- Locating the downloaded gogdl binary
- Auth token conversion between Qtris and gogdl formats
- Subprocess invocation with progress parsing
- Reading back refreshed tokens after gogdl operations
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from qtris import settings
from qtris.util.credentials import save_credentials
from qtris.util.log import logger

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

# Where the component updates install gogdl
GOGDL_DIR = os.path.join(settings.RUNTIME_DIR, "gogdl")

# GOG client ID (same for both Qtris and gogdl)
GOG_CLIENT_ID = "46899977096215655"

# Regex to parse gogdl progress output
PROGRESS_RE = re.compile(
    r"= Progress: (?P<percent>[\d.]+) (?P<written>\d+)/(?P<total>\d+),"
    r" Running for: (?P<elapsed>\S+), ETA: (?P<eta>\S+)"
)
SPEED_RE = re.compile(r"\+ Download\t- (?P<raw_speed>[\d.]+) MiB/s \(raw\)")
# Outcome lines from the game download managers; the REDIST manager also says
# "Nothing to do", about redistributables only, so match on the logger name.
NOTHING_TO_DO_RE = re.compile(r"^\[(V1|V2|LINUX)\] INFO: (Nothing to do|All files look good)$")
BROKEN_FILES_RE = re.compile(r"^\[(V1|V2)\] INFO: Found (?P<count>\d+) broken files")


def _get_gogdl_binary() -> str:
    """Return the path to the gogdl binary that Qtris downloads with its other components."""
    binary = os.path.join(GOGDL_DIR, "gogdl")
    if os.path.isfile(binary) and os.access(binary, os.X_OK):
        return binary
    raise FileNotFoundError(
        "gogdl hasn't been downloaded yet. Check for updates in Preferences → Updates, then try again."
    )


def is_gogdl_available() -> bool:
    try:
        _get_gogdl_binary()
    except FileNotFoundError:
        return False
    return True


def _write_auth_config(auth_path: Path) -> dict[str, Any]:
    """Read Qtris's GOG token and write it in gogdl's expected format."""
    from qtris.services.gog import GOGService

    service = GOGService()
    token = service.load_token()

    token_age = service.get_token_age()
    login_time = time.time() - token_age

    if token_age > 2600:
        service.request_token(refresh_token=token["refresh_token"])
        token = service.load_token()
        login_time = time.time()

    gogdl_auth = {
        GOG_CLIENT_ID: {
            "access_token": token["access_token"],
            "refresh_token": token["refresh_token"],
            "expires_in": token.get("expires_in", 3600),
            "loginTime": login_time,
        }
    }

    save_credentials(auth_path, json.dumps(gogdl_auth))
    remove_legacy_auth_config()
    return token


def remove_legacy_auth_config() -> None:
    try:
        Path(settings.CACHE_DIR, "gogdl-auth.json").unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove GOG's legacy gogdl auth file")


def _read_back_token(auth_path: Path, current_token: dict[str, Any]) -> None:
    """Save a token refreshed by gogdl if Qtris still has the original session."""
    try:
        gogdl_auth = json.loads(auth_path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        logger.warning("Could not read refreshed credentials from gogdl")
        return

    creds = gogdl_auth.get(GOG_CLIENT_ID) if isinstance(gogdl_auth, dict) else None
    if not isinstance(creds, dict) or not creds.get("access_token") or not creds.get("refresh_token"):
        return

    from qtris.services.gog import GOGService

    if creds.get("access_token") != current_token.get("access_token"):
        updated_token = dict(current_token)
        updated_token["access_token"] = creds["access_token"]
        updated_token["refresh_token"] = creds["refresh_token"]
        updated_token["expires_in"] = creds.get("expires_in", 3600)
        if GOGService().store_refreshed_token(updated_token, current_token):
            logger.info("Updated Qtris GOG token from gogdl refresh")


def _build_command(command, game_id, path, auth_path, platform="windows", lang=None, dlcs=None):
    """Build the gogdl command line arguments."""
    binary = _get_gogdl_binary()

    cmd = [
        binary,
        "--auth-config-path",
        str(auth_path),
        command,
        str(game_id),
    ]

    if command != "info":
        cmd.extend(["--path", path])

    cmd.extend(["--platform", platform])

    if lang:
        cmd.extend(["--lang", lang])

    if dlcs is True:
        cmd.append("--with-dlcs")
    elif dlcs is False:
        cmd.append("--skip-dlcs")
    elif isinstance(dlcs, str) and dlcs:
        # `--dlcs <ids>` only filters which DLCs to include; gogdl still gates
        # on `--with-dlcs` before consulting ownership, so both must be passed.
        cmd.extend(["--with-dlcs", "--dlcs", dlcs])

    return cmd


class GogdlProgress:
    """Tracks the latest progress state from gogdl output."""

    def __init__(self):
        self.percent = 0.0
        self.written = 0
        self.total = 0
        self.speed_mib = 0.0
        self.eta = ""
        # True if gogdl found every file already correct, so it changed nothing
        self.nothing_to_do = False
        # How many files a repair found damaged or missing, if gogdl said
        self.broken_files = None

    def parse_line(self, line):
        """Parse a single line of gogdl output. Returns True if progress was updated."""
        if NOTHING_TO_DO_RE.search(line):
            self.nothing_to_do = True
            return False

        match = BROKEN_FILES_RE.search(line)
        if match:
            self.broken_files = int(match.group("count"))
            return False

        match = PROGRESS_RE.search(line)
        if match:
            self.percent = float(match.group("percent"))
            self.written = int(match.group("written"))
            self.total = int(match.group("total"))
            self.eta = match.group("eta")
            return True

        match = SPEED_RE.search(line)
        if match:
            self.speed_mib = float(match.group("raw_speed"))
            return True

        return False


def run_gogdl(command, game_id, path, platform="windows", lang=None, dlcs=None, progress_callback=None):
    """Run a gogdl command as a subprocess.

    Args:
        command: "download", "update", "repair", or "info"
        game_id: GOG product ID
        path: Installation path
        platform: "windows" or "linux"
        lang: Language code
        dlcs: True (all), False (none), or comma-separated DLC ID string
        progress_callback: Called with (GogdlProgress) on progress updates
    Returns:
        For "info": parsed JSON dict
        For other commands: the GogdlProgress, which records the outcome
    Raises:
        FileNotFoundError: if gogdl runtime component is not installed
        RuntimeError: if gogdl fails (with gogdl's error output as the message)
    """
    with TemporaryDirectory(prefix="qtris-gogdl-") as folder:
        auth_path = Path(folder, "auth.json")
        original_token = _write_auth_config(auth_path)
        cmd = _build_command(command, game_id, path, auth_path, platform, lang, dlcs)
        try:
            return _run_command(cmd, command, progress_callback)
        finally:
            _read_back_token(auth_path, original_token)


def _run_command(
    cmd: list[str], command: str, progress_callback: Callable[[GogdlProgress], None] | None
) -> dict[str, Any] | GogdlProgress:
    env = os.environ.copy()
    config_path = os.path.join(settings.CACHE_DIR, "gogdl")
    os.makedirs(config_path, exist_ok=True)
    env["GOGDL_CONFIG_PATH"] = config_path

    logger.info("Running gogdl %s", command)

    if command == "info":
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
        if result.returncode != 0:
            error_msg = result.stderr.strip() or "gogdl info failed (exit code %d)" % result.returncode
            logger.error("gogdl info failed: %s", error_msg)
            raise RuntimeError(error_msg)
        return json.loads(result.stdout)

    # For download/update/repair, stream stderr for progress
    progress = GogdlProgress()
    error_lines = []

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

    def read_stderr():
        for line in process.stderr:
            line = line.rstrip()
            if not line:
                continue
            logger.debug("[gogdl stderr] %s", line)
            if progress.parse_line(line) and progress_callback:
                progress_callback(progress)
            if "ERROR" in line:
                error_lines.append(line)

    def read_stdout():
        for line in process.stdout:
            line = line.rstrip()
            if not line:
                continue
            logger.debug("[gogdl stdout] %s", line)
            if progress.parse_line(line) and progress_callback:
                progress_callback(progress)

    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stderr_thread.start()
    stdout_thread.start()

    process.wait()
    stderr_thread.join(timeout=5)
    stdout_thread.join(timeout=5)

    if process.returncode != 0:
        if error_lines:
            error_msg = "\n".join(error_lines)
        else:
            error_msg = "gogdl %s failed (exit code %d)" % (command, process.returncode)
        logger.error("gogdl %s failed: %s", command, error_msg)
        raise RuntimeError(error_msg)

    return progress


LINUX_MANIFEST_FILENAME = ".gogdl-linux-manifest"


def _find_manifest(game_id, install_dir, runner):
    """Return the on-disk path to the gogdl manifest for this install, or None.

    The Linux native download manager writes `.gogdl-linux-manifest` inside
    the install directory (nested one level under an installDirectory
    subfolder named after the game's display name), while the v2 (Windows)
    manager writes to a shared cache dir keyed by game id alone."""
    if runner == "linux":
        if not install_dir or not os.path.isdir(install_dir):
            return None
        direct = os.path.join(install_dir, LINUX_MANIFEST_FILENAME)
        if os.path.exists(direct):
            return direct
        for entry in os.listdir(install_dir):
            candidate = os.path.join(install_dir, entry, LINUX_MANIFEST_FILENAME)
            if os.path.exists(candidate):
                return candidate
        return None
    win_manifest = os.path.join(settings.CACHE_DIR, "gogdl", "heroic_gogdl", "manifests", str(game_id))
    return win_manifest if os.path.exists(win_manifest) else None


def is_depot_installed(game_id, install_dir, runner):
    """Check if a game was installed via gogdl depot. Both install_dir and
    runner are required: Linux native installs can only be detected from the
    install directory, while Windows installs are tracked by game id."""
    return _find_manifest(game_id, install_dir, runner) is not None


def clear_stale_manifest(game_id, install_dir, runner):
    """Remove any gogdl manifest associated with this install.

    Used before a fresh `download` to ensure gogdl doesn't skip work because
    a previous interrupted install left a manifest behind."""
    manifest = _find_manifest(game_id, install_dir, runner)
    if manifest:
        os.remove(manifest)
        logger.debug("Cleared stale gogdl manifest at %s", manifest)
