"""Update game runtime components from their own release channels."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from gettext import gettext as _
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
from time import time
from urllib.parse import urlsplit

from qtris import settings
from qtris.exceptions import QtrisError
from qtris.util import system
from qtris.util.downloader import SimpleDownloader
from qtris.util.extract import ExtractError, extract_archive
from qtris.util.http import HTTPError
from qtris.util.linux import LINUX_SYSTEM
from qtris.util.log import logger
from qtris.util.progress_info import ProgressInfo
from qtris.util.upstream import FileSource, ReleaseSource, UpstreamBuild, UpstreamError, UpstreamSource

CHECK_INTERVAL = 6 * 60 * 60
RUNTIME_DISABLED = os.environ.get("QTRIS_RUNTIME", "").casefold() in {"0", "off"}


@dataclass(frozen=True)
class Component:
    name: str
    source: UpstreamSource
    entry_point: str
    archive: bool = True
    executable: bool = True

    @property
    def directory(self) -> Path:
        return Path(settings.RUNTIME_DIR) / self.name

    @property
    def version_file(self) -> Path:
        return self.directory / ".upstream-version"

    def installed_version(self) -> str | None:
        if not (self.directory / self.entry_point).is_file():
            return None
        try:
            return self.version_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""
        except (OSError, UnicodeError) as error:
            logger.warning("Could not read the installed version of %s: %s", self.name, error)
            return ""


def get_components(arch: str | None) -> list[Component]:
    components = [
        Component("umu", ReleaseSource("Open-Wine-Components/umu-launcher", r"-zipapp\.tar$"), "umu-run"),
        Component("winetricks", ReleaseSource("Winetricks/winetricks"), "src/winetricks"),
        Component(
            "umu-games",
            FileSource("https://raw.githubusercontent.com/Open-Wine-Components/umu-database/main/umu-database.csv"),
            "umu-database.csv",
            archive=False,
            executable=False,
        ),
        Component(
            "gamecontrollerdb",
            FileSource("https://raw.githubusercontent.com/mdqinc/SDL_GameControllerDB/master/gamecontrollerdb.txt"),
            "gamecontrollerdb.txt",
            archive=False,
            executable=False,
        ),
        Component(
            "dgvoodoo2",
            FileSource("https://api.github.com/repos/dege-diosg/dgVoodoo2/releases"),
            "dgvoodoo2_versions.json",
            archive=False,
            executable=False,
        ),
    ]
    if arch in {"x86_64", "aarch64"}:
        asset_arch = {"x86_64": "x86_64", "aarch64": "arm64"}[arch]
        legendary_arch = "x64" if arch == "x86_64" else asset_arch
        components += [
            Component(
                "gogdl",
                ReleaseSource("Heroic-Games-Launcher/heroic-gogdl", rf"^gogdl_linux_{asset_arch}$"),
                "gogdl",
                archive=False,
            ),
            Component(
                "legendary",
                ReleaseSource("legendary-gl/legendary", rf"^legendary_linux_{legendary_arch}$"),
                "legendary",
                archive=False,
            ),
            Component(
                "nile",
                ReleaseSource("imLinguin/nile", rf"^nile_linux_{asset_arch}$"),
                "nile",
                archive=False,
            ),
        ]
    if seven_zip_arch := {"x86_64": "x64", "i386": "x86", "aarch64": "arm64", "armv7": "arm"}.get(arch or ""):
        components.append(Component("7zip", ReleaseSource("ip7z/7zip", rf"-linux-{seven_zip_arch}\.tar\.xz$"), "7zz"))
    if arch == "x86_64":
        components.append(
            Component("innoextract", ReleaseSource("dscharrer/innoextract", r"-linux\.tar\.xz$"), "innoextract")
        )
    return components


class ComponentUpdater:
    @property
    def name(self) -> str:
        raise NotImplementedError

    def install_update(self, updater: "RuntimeUpdater") -> None:
        raise NotImplementedError

    def join(self) -> None:
        """Wait for any background work from the component to finish."""

    def get_progress(self) -> ProgressInfo:
        return ProgressInfo.ended()


class RuntimeUpdater:
    def __init__(self, force: bool = False) -> None:
        self.check_path = Path(settings.CACHE_DIR) / "upstream-runtime-check"
        enabled = settings.read_bool_setting("auto_update_runtime", default=True)
        self.has_updates = not RUNTIME_DISABLED and (force or (enabled and self._is_check_due()))

    def _is_check_due(self) -> bool:
        try:
            return time() - self.check_path.stat().st_mtime >= CHECK_INTERVAL
        except FileNotFoundError:
            return True

    @staticmethod
    def _resolve_component(component: Component) -> "RuntimeComponentUpdater | None":
        try:
            build = component.source.find()
        except (UpstreamError, HTTPError, ValueError) as ex:
            logger.warning("Unable to check %s for updates: %s", component.name, ex)
            return None
        return RuntimeComponentUpdater(component, build)

    def create_component_updaters(self) -> list[ComponentUpdater]:
        if not self.has_updates:
            return []
        with ThreadPoolExecutor(max_workers=4) as executor:
            resolved = list(executor.map(self._resolve_component, get_components(LINUX_SYSTEM.arch)))
        if all(updater is not None for updater in resolved):
            self.check_path.parent.mkdir(parents=True, exist_ok=True)
            self.check_path.touch()
        # Imported here: rolling Proton builds are updaters themselves
        from qtris.util.wine.rolling_proton import RollingProtonUpdater

        updaters: list[ComponentUpdater] = [
            updater for updater in resolved if updater is not None and updater.should_update
        ]
        return updaters + RollingProtonUpdater.find()


class RuntimeComponentUpdater(ComponentUpdater):
    def __init__(self, component: Component, build: UpstreamBuild) -> None:
        self.component = component
        self.build = build
        self.destination = component.directory
        self.version_file = component.version_file
        self.downloader: SimpleDownloader | None = None
        self.progress = ProgressInfo(0, _("Updating %s") % component.name)

    @property
    def name(self) -> str:
        return self.component.name

    @property
    def should_update(self) -> bool:
        return not self.build.version or self.component.installed_version() != self.build.version

    def get_progress(self) -> ProgressInfo:
        if self.downloader is not None:
            return ProgressInfo(
                self.downloader.progress_fraction, _("Downloading %s") % self.name, self.downloader.cancel
            )
        return self.progress

    def install_update(self, updater: RuntimeUpdater) -> None:
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with TemporaryDirectory(dir=self.destination.parent) as tmp:
                work = Path(tmp)
                download = work / urlsplit(self.build.url).path.rsplit("/", 1)[-1]
                self._download(download)
                self.progress = ProgressInfo(0, _("Installing %s") % self.name)
                self._install(download, work / "component")
        except (ExtractError, QtrisError, OSError) as error:
            self.progress = ProgressInfo.ended(_("Could not update %s") % self.name)
            updater.check_path.unlink(missing_ok=True)
            raise QtrisError(_("Could not update %s: %s") % (self.name, error)) from error
        self.progress = ProgressInfo.ended(_("Updated %s") % self.name)

    def _download(self, destination: Path) -> None:
        downloader = SimpleDownloader(self.build.url, str(destination), overwrite=True)
        self.downloader = downloader
        try:
            downloader.start()
            if not downloader.join() or not system.path_exists(str(destination), exclude_empty=True):
                raise QtrisError(_("Download did not complete."))
        finally:
            self.downloader = None

    def _install(self, download: Path, staged: Path) -> None:
        entry = staged / self.component.entry_point
        if self.component.archive:
            extract_archive(str(download), str(staged))
        else:
            entry.parent.mkdir(parents=True, exist_ok=True)
            download.replace(entry)
        if not entry.is_file():
            raise ExtractError(_("The upstream build is missing %s.") % self.component.entry_point)
        if self.component.executable:
            system.make_executable(str(entry))

        self.destination.mkdir(parents=True, exist_ok=True)
        if self.component.archive:
            copytree(staged, self.destination, dirs_exist_ok=True, symlinks=True)
        else:
            entry.replace(self.destination / self.component.entry_point)
        if self.build.version:
            staged_version = staged / ".upstream-version"
            staged_version.write_text(self.build.version, encoding="utf-8")
            staged_version.replace(self.version_file)
        else:
            self.version_file.unlink(missing_ok=True)
