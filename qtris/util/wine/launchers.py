"""Windows launcher setup and compatibility settings used by the built-in installers."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from gettext import gettext as _
from pathlib import Path
from threading import Lock

from qtris.exceptions import QtrisError

_EA_BASE = Path("drive_c/Program Files/Electronic Arts/EA Desktop")
_EA_EXE = _EA_BASE / "EA Desktop/EALauncher.exe"


@dataclass(frozen=True)
class LauncherSetup:
    url: str
    filename: str
    executable: Path
    args: str
    write_copy: bool = False

    def environment(self) -> dict[str, str]:
        env = {"PROTON_ENABLE_WAYLAND": "0"}
        if self.write_copy:
            env["WINE_SIMULATE_WRITECOPY"] = "1"
        return env


# Installer arguments and compatibility settings follow https://github.com/Faugus/faugus-launcher
LAUNCHER_SETUPS = {
    "battlenet": LauncherSetup(
        "https://downloader.battle.net/download/getInstaller?os=win&installer=Battle.net-Setup.exe",
        "Battle.net-Setup.exe",
        Path("drive_c/Program Files (x86)/Battle.net/Battle.net.exe"),
        r"""--installpath='C:\Program Files (x86)\Battle.net' --lang=enUS""",
        write_copy=True,
    ),
    "ea_app": LauncherSetup(
        "https://origin-a.akamaihd.net/EA-Desktop-Client-Download/installer-releases/EAappInstaller.exe",
        "EAappInstaller.exe",
        _EA_EXE,
        "/S",
    ),
    "ubisoft": LauncherSetup(
        "https://static3.cdn.ubi.com/orbit/launcher_installer/UbisoftConnectInstaller.exe",
        "UbisoftConnectInstaller.exe",
        Path("drive_c/Program Files (x86)/Ubisoft/Ubisoft Game Launcher/UbisoftConnect.exe"),
        "/S",
    ),
}


def reserve_launcher_prefix(directory: str, service_id: str) -> None:
    """Keep each launcher in its own directory, and allow its setup to be retried."""
    prefix = Path(directory)
    if not prefix.is_absolute() or prefix.is_symlink():
        raise QtrisError(_("Choose a dedicated folder for the launcher."))
    if not prefix.is_dir():
        raise QtrisError(_("Could not create the launcher folder."))

    marker = prefix / ".qtris-launcher"
    if marker.is_file() and not marker.is_symlink() and marker.read_text(encoding="utf-8") == service_id:
        return
    if any(prefix.iterdir()):
        raise QtrisError(_("Choose an empty folder for the launcher."))
    marker.write_text(service_id, encoding="utf-8")


_ea_repair_lock = Lock()


def repair_ea_launcher(prefix: Path, executable: Path) -> None:
    """Move staged EA updates into use at EA's stable executable path."""
    if executable != prefix / _EA_EXE:
        return
    with _ea_repair_lock:
        base = prefix / _EA_BASE
        if not base.is_dir():
            return
        versions = [
            folder
            for folder in base.iterdir()
            if folder.name[0] in "0123456789" and (folder / "EA Desktop/EALauncher.exe").is_file()
        ]
        if not versions:
            return
        latest = max(versions, key=lambda folder: [int(number) for number in re.findall(r"[0-9]+", folder.name)])
        staged = latest / "EA Desktop"
        marker = base / ".qtris-staged-version"
        stamp = f"{latest.name}:{(staged / executable.name).stat().st_mtime_ns}"
        if executable.is_file() and marker.is_file() and marker.read_text(encoding="utf-8") == stamp:
            return
        shutil.copytree(staged, executable.parent, dirs_exist_ok=True)
        marker.write_text(stamp, encoding="utf-8")
