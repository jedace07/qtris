"""Calls about runners, the programs (Proton, Steam, ...) that run games.

Errors are QtrisError subclasses, such as InvalidRunnerError for a name no
runner has, and RunnerInstallationError.
"""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from gettext import gettext as _
from pathlib import Path
from time import monotonic
from typing import Any

from qtris.backend.models import RunnerInfo
from qtris.delegates import InstallUIDelegate
from qtris.exceptions import QtrisError
from qtris.game import Game
from qtris.runners import (
    RunnerInstallationError,
    get_enabled,
    get_user_runner_names,
    import_runner,
    is_runner_enabled,
)
from qtris.runners import set_runner_enabled as _save_runner_enabled
from qtris.util import system
from qtris.util.downloader import BaseDownloader, SimpleDownloader
from qtris.util.http import HTTPError
from qtris.util.log import logger
from qtris.util.strings import human_size
from qtris.util.wine import proton, proton_releases, rolling_proton
from qtris.util.wine.wine import GE_PROTON_LATEST as GE_PROTON_LATEST
from qtris.util.wine.wine import clear_wine_version_cache, get_installed_wine_versions

# The Proton forks Qtris downloads releases of, by name
PROTON_FAMILIES: tuple[str, ...] = proton_releases.get_family_names()


@dataclass(frozen=True)
class RunnerCatalogEntry:
    name: str
    label: str
    description: str
    enabled: bool
    installed: bool
    can_remove: bool


@dataclass(frozen=True)
class ProtonBuild:
    version: str
    installed: bool
    can_remove: bool
    label: str = ""


@dataclass(frozen=True)
class LatestProton:
    """A build that follows a Proton family's newest release."""

    version: str
    label: str
    # The release it holds, or "" if it isn't installed
    release: str
    release_label: str = ""


@dataclass(frozen=True)
class RunnerInstallInteraction:
    progress: Callable[[str, float | None], None]
    question: Callable[[str, str], Coroutine[Any, Any, bool]]
    file: Callable[[str, str, str], Coroutine[Any, Any, str | None]]


class _InstallDelegate(InstallUIDelegate):
    def __init__(self, interaction: RunnerInstallInteraction, loop: asyncio.AbstractEventLoop) -> None:
        self._interaction = interaction
        self._loop = loop
        self._last_progress = 0.0

    def show_install_yesno_inquiry(self, question: str, title: str) -> bool:
        return asyncio.run_coroutine_threadsafe(self._interaction.question(title, question), self._loop).result()

    def show_install_file_inquiry(self, question: str, title: str, message: str) -> str | None:
        return asyncio.run_coroutine_threadsafe(self._interaction.file(title, question, message), self._loop).result()

    def download_install_file(self, url: str, destination: str) -> bool:
        self._publish(_("Downloading…"), None)
        self._last_progress = 0.0
        downloader = SimpleDownloader(url, destination, overwrite=True)
        downloader.start()
        completed = downloader.join(self._download_progress)
        if completed:
            self._publish(_("Verifying and installing…"), None)
        return completed

    def _publish(self, label: str, fraction: float | None) -> None:
        self._loop.call_soon_threadsafe(self._interaction.progress, label, fraction)

    def _download_progress(self, downloader: BaseDownloader) -> None:
        now = monotonic()
        if not downloader.full_size or now - self._last_progress < 0.1:
            return
        self._last_progress = now
        self._publish(
            _("%(downloaded)s of %(total)s")
            % {"downloaded": human_size(downloader.downloaded_size), "total": human_size(downloader.full_size)},
            min(1.0, downloader.progress_fraction),
        )


async def list_runners() -> list[str]:
    """Return the names of the runners offered to the user, in alphabetical order."""
    return sorted(get_user_runner_names(), key=lambda name: name.lower())


async def list_enabled_runners() -> list[RunnerInfo]:
    """Return the runners that are turned on, in alphabetical order of their names."""
    enabled = await asyncio.to_thread(get_enabled)
    return sorted((RunnerInfo(r.name, r.human_name) for r in enabled), key=lambda r: r.human_name.casefold())


async def set_runner_enabled(name: str, enabled: bool) -> None:
    """Turn a runner on or off. Turning one off hides it, and leaves it installed."""
    _save_runner_enabled(name, enabled)


async def list_runner_catalog() -> tuple[RunnerCatalogEntry, ...]:
    """Describe the runners and which actions Qtris can perform."""
    return await asyncio.to_thread(_runner_catalog)


def _runner_catalog() -> tuple[RunnerCatalogEntry, ...]:
    entries = []
    for name in get_user_runner_names():
        runner = import_runner(name)()
        # Wine downloads the Proton build a game needs when it first runs, so it's always ready
        installed = name == "wine" or runner.is_installed()
        entries.append(
            RunnerCatalogEntry(
                name,
                runner.human_name,
                str(getattr(runner, "description", "")),
                is_runner_enabled(name),
                installed,
                name != "wine" and runner.can_uninstall(),
            )
        )
    return tuple(sorted(entries, key=lambda entry: entry.label.casefold()))


def _managed_proton_path(version: str) -> Path | None:
    """Return where a Proton release or rolling build Qtris can remove is installed:
    Qtris's own Proton folder, or Steam's, where Qtris used to install releases. Builds
    ProtonPlus manages there are its own to remove."""
    if rolling := proton_releases.get_rolling(version):
        path = Path(proton.get_rolling_path(rolling))
        return path if (path / "proton").is_file() else None
    if not proton_releases.is_proton_release(version):
        return None
    for root in _managed_proton_dirs():
        for name in proton_releases.get_build_folder_names(version):
            path = root / name
            if not path.is_symlink() and (path / "proton").is_file() and not proton.is_protonplus_build(path):
                return path
    return None


def _managed_proton_dirs() -> tuple[Path, ...]:
    return Path(proton.get_proton_dir()), Path(proton.get_steam_compat_dir())


async def list_proton_builds(family: str, available: tuple[str, ...] = ()) -> tuple[ProtonBuild, ...]:
    """List a Proton family's installed and available releases, and identify the
    installations Qtris can remove."""
    return await asyncio.to_thread(_proton_builds, proton_releases.get_family_by_name(family), available)


def _proton_builds(family: proton_releases.ProtonFamily, available: tuple[str, ...]) -> tuple[ProtonBuild, ...]:
    installed_names = list(proton.get_proton_versions())
    for root in _managed_proton_dirs():
        if root.is_dir():
            installed_names.extend(
                path.name
                for path in root.iterdir()
                if (path / "proton").is_file() and not proton.is_protonplus_build(path)
            )
    installed = {
        release
        for name in installed_names
        if (release := proton_releases.get_release_of_build(name)) and family.is_release(release)
    }
    builds = []
    for version in proton_releases.sort_releases(installed.union(available)):
        managed = _managed_proton_path(version) is not None
        builds.append(ProtonBuild(version, managed or version in installed, managed, family.get_label(version)))
    return tuple(builds)


async def list_proton_releases(family: str) -> tuple[str, ...]:
    """Fetch a Proton family's recent releases."""
    try:
        return await asyncio.to_thread(proton_releases.get_proton_releases, proton_releases.get_family_by_name(family))
    except (HTTPError, ValueError) as error:
        logger.warning("Could not load %s releases: %s", family, error)
        return ()


async def get_latest_proton(family: str) -> LatestProton | None:
    """Return the build that follows a Proton family's newest release, if it has one."""
    return await asyncio.to_thread(_latest_proton, family)


def _latest_proton(family: str) -> LatestProton | None:
    for rolling in proton_releases.ROLLING:
        if rolling.family.name == family:
            release = rolling_proton.get_installed_release(rolling) or ""
            release_label = proton_releases.get_release_label(release)
            if not release and rolling_proton.is_installed(rolling):
                # Installed without its metadata; the next update replaces it
                release = release_label = _("an unknown release")
            return LatestProton(rolling.version, rolling.label, release, release_label)
    return None


async def list_wine_versions() -> tuple[str, ...]:
    """Return the installed Proton builds and available Proton releases."""
    installed, *available = await asyncio.gather(
        asyncio.to_thread(get_installed_wine_versions), *(list_proton_releases(family) for family in PROTON_FAMILIES)
    )
    return tuple(dict.fromkeys((*(release for releases in available for release in releases), *installed)))


def parse_runner_name(name: str) -> tuple[str, str | None]:
    """Resolve a runner name or a managed Proton release from the command line."""
    if proton_releases.is_proton_release(name) or proton_releases.get_rolling(name):
        return "wine", name
    return name, None


async def is_runner_installed(name: str, version: str | None = None) -> bool:
    """True if a runner is installed; for "wine", a version names a Proton release."""
    if name == "wine" and version:
        downloadable = proton_releases.is_proton_release(version) or proton_releases.get_rolling(version)
        return bool(downloadable and (_managed_proton_path(version) or proton.is_proton_version(version)))
    return import_runner(name)().is_installed()


async def install_runner(
    name: str, version: str | None = None, *, interaction: RunnerInstallInteraction | None = None
) -> None:
    """Download and install a runner, or one version of it."""
    runner = import_runner(name)()
    if version and name == "wine" and proton_releases.is_proton_release(version):
        if _managed_proton_path(version) or proton.is_proton_version(version):
            return
    delegate = _InstallDelegate(interaction, asyncio.get_running_loop()) if interaction else InstallUIDelegate()
    await asyncio.to_thread(runner.install, delegate, version=version)
    if (
        interaction
        and name == "wine"
        and version
        and (proton_releases.is_proton_release(version) or proton_releases.get_rolling(version))
        and not _managed_proton_path(version)
    ):
        raise RunnerInstallationError(_("%s was not installed.") % version)
    if interaction and name != "wine" and not await asyncio.to_thread(runner.is_installed):
        raise RunnerInstallationError(_("%s was not installed.") % runner.human_name)


async def prepare_game_runner(game_id: str, interaction: RunnerInstallInteraction) -> bool:
    """Offer to install the runner configured for this game, if it is missing."""
    delegate = _InstallDelegate(interaction, asyncio.get_running_loop())

    def prepare() -> bool:
        game = Game(game_id)
        if not game.runner or not game.is_installed:
            return True
        return game.runner.is_installed() or game.runner.install_dialog(delegate)

    return await asyncio.to_thread(prepare)


async def can_uninstall_runner(name: str) -> bool:
    """True if Qtris installed a runner, and so can uninstall it."""
    return import_runner(name)().can_uninstall()


async def _wait_for_callbacks(start: Callable[[Callable[[], None], Callable[[Exception], None]], None]) -> None:
    """Call start with a completion and an error callback, and wait until it calls one."""
    done = asyncio.get_running_loop().create_future()

    def on_completed() -> None:
        if not done.done():
            done.set_result(None)

    def on_error(error: Exception) -> None:
        if not done.done():
            done.set_exception(error)

    start(on_completed, on_error)
    await done


async def uninstall_runner(name: str, version: str | None = None) -> None:
    """Move a runner, or one Proton build Qtris downloaded, to the trash."""
    if name == "wine" and (rolling := proton_releases.get_rolling(version)):
        await _wait_for_callbacks(lambda done, error: rolling_proton.remove(rolling, done, error))
        clear_wine_version_cache()
        return
    if name == "wine" and version:
        proton_path = _managed_proton_path(version)
        if proton_path is None:
            raise QtrisError(_("%s is outside the Proton folder managed here.") % version)
        await _wait_for_callbacks(lambda done, error: system.remove_folder(str(proton_path), done, error))
        clear_wine_version_cache()
        return
    runner = import_runner(name)()
    await _wait_for_callbacks(runner.uninstall)
