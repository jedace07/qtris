"""Rolling Proton builds, such as Proton-CachyOS Latest.

Like ProtonPlus's and Heroic's "Latest" builds, a rolling build lives in one folder
that keeps its name, so games set to it follow every new release. An update
downloads and verifies the newest release, unpacks it next to that folder, carries
over the user's 'user_settings.py', and swaps it in, keeping the previous build until
the new one is in place. A metadata file in the folder records which release it is.
"""

from __future__ import annotations

import json
import os
import shutil
from gettext import gettext as _
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from qtris.exceptions import QtrisError
from qtris.monitored_command import RUNNING_COMMANDS
from qtris.runtime import ComponentUpdater
from qtris.util import system
from qtris.util.downloader import SimpleDownloader
from qtris.util.extract import ExtractError, extract_archive
from qtris.util.http import HTTPError
from qtris.util.log import logger
from qtris.util.progress_info import ProgressInfo
from qtris.util.wine import proton, proton_releases

if TYPE_CHECKING:
    from collections.abc import Callable

    from qtris.runtime import RuntimeUpdater
    from qtris.util.wine.proton_releases import RollingProton

METADATA_FILE = ".qtris-rolling.json"
USER_SETTINGS_FILE = "user_settings.py"


class RollingProtonError(QtrisError):
    """Raised when a rolling Proton build can't be installed or updated."""


def is_installed(rolling: RollingProton) -> bool:
    return os.path.isfile(os.path.join(proton.get_rolling_path(rolling), "proton"))


def get_installed_release(rolling: RollingProton) -> str | None:
    """Return the release a rolling build holds, or None if it isn't installed or
    its metadata is missing."""
    if not is_installed(rolling):
        return None
    try:
        with open(os.path.join(proton.get_rolling_path(rolling), METADATA_FILE), encoding="utf-8") as metadata_file:
            release = json.load(metadata_file).get("release")
    except (OSError, ValueError, AttributeError) as ex:
        logger.warning("Unable to read which release %s holds: %s", rolling.label, ex)
        return None
    return release if isinstance(release, str) else None


def is_in_use(rolling: RollingProton) -> bool:
    """Check whether a running game or tool uses a rolling build, which must not be
    replaced underneath it."""
    path = proton.get_rolling_path(rolling)
    for command in list(RUNNING_COMMANDS):
        env = getattr(command, "env", None) or {}
        if env.get("PROTONPATH") == path or str(env.get("WINE", "")).startswith(path + os.sep):
            return True
    return False


def get_latest_release(rolling: RollingProton) -> str:
    """Return the newest release of a rolling build's family with a build for this machine."""
    try:
        releases = proton_releases.get_proton_releases(rolling.family)
    except (HTTPError, ValueError) as ex:
        raise RollingProtonError(_("Failed to look up %s releases: %s") % (rolling.family.name, ex)) from ex
    if not releases:
        raise RollingProtonError(_("%s has no release for this machine.") % rolling.family.name)
    return releases[0]


def needs_update(rolling: RollingProton, release: str) -> bool:
    return release != get_installed_release(rolling)


def update(rolling: RollingProton, download: Callable[[str, str], bool]) -> bool:
    """Install a rolling build, or bring it up to the newest release. Returns True if
    a release was installed, and False if it was up to date, in use or the download
    was canceled."""
    release = get_latest_release(rolling)
    if not needs_update(rolling, release):
        logger.info("%s is up to date (%s)", rolling.label, release)
        return False
    return install_release(rolling, release, download)


def install_release(rolling: RollingProton, release: str, download: Callable[[str, str], bool]) -> bool:
    """Download a release and swap it into a rolling build's folder. 'download' fetches
    a URL into a file and returns False if it was canceled."""
    if is_in_use(rolling):
        logger.info("%s is in use; it will be updated to %s later", rolling.label, release)
        return False
    try:
        urls = proton_releases.get_proton_release_urls(release)
    except (HTTPError, ValueError) as ex:
        raise RollingProtonError(_("Failed to retrieve %s information: %s") % (release, ex)) from ex
    if not urls:
        raise RollingProtonError(_("%s has no build for this machine.") % release)
    archive_url, checksum_url = urls
    try:
        expected_checksum = proton_releases.get_proton_checksum(checksum_url)
    except HTTPError as ex:
        raise RollingProtonError(_("Failed to retrieve the checksum of %s: %s") % (release, ex)) from ex

    target = Path(proton.get_rolling_path(rolling))
    target.parent.mkdir(parents=True, exist_ok=True)
    # Staging next to the target keeps the final swap a rename on one file system
    with TemporaryDirectory(dir=target.parent, prefix=".qtris-") as work_dir:
        work = Path(work_dir)
        archive = work / os.path.basename(archive_url)
        logger.info("Downloading %s for %s", release, rolling.label)
        if not download(archive_url, str(archive)):
            logger.info("The %s download was canceled", release)
            return False
        if not expected_checksum or system.get_file_checksum(str(archive), "sha512") != expected_checksum:
            raise RollingProtonError(_("The %s download is corrupt; its checksum does not match.") % release)
        build = _unpack(archive, work / "unpacked")
        _carry_over_user_settings(target, build)
        (build / METADATA_FILE).write_text(json.dumps({"release": release, "build": build.name}), encoding="utf-8")
        if is_in_use(rolling):
            logger.info("%s came into use; it will be updated to %s later", rolling.label, release)
            return False
        _swap_in(build, target, work / "previous")
    proton.get_proton_versions.cache_clear()
    logger.info("%s is now %s", rolling.label, release)
    return True


def _unpack(archive: Path, destination: Path) -> Path:
    try:
        extract_archive(str(archive), str(destination), merge_single=False)
    except ExtractError as ex:
        raise RollingProtonError(_("Failed to extract %s: %s") % (archive.name, ex)) from ex
    archive.unlink()
    build = destination / proton_releases.get_build_name(archive.name)
    entry_point = build / "proton"
    if not entry_point.is_file() or not entry_point.resolve().is_relative_to(build.resolve()):
        raise RollingProtonError(_("The %s archive is missing its proton script.") % archive.name)
    system.make_executable(str(entry_point))
    return build


def _carry_over_user_settings(previous_build: Path, build: Path) -> None:
    """Keep the Proton options a user set in the build being replaced."""
    user_settings = previous_build / USER_SETTINGS_FILE
    if user_settings.is_file() or user_settings.is_symlink():
        shutil.copy2(user_settings, build / USER_SETTINGS_FILE, follow_symlinks=False)


def _swap_in(build: Path, target: Path, previous: Path) -> None:
    """Move a new build into the target folder, restoring the old one if that fails."""
    had_previous = target.exists()
    if had_previous:
        os.replace(target, previous)
    try:
        os.replace(build, target)
    except OSError:
        if had_previous:
            os.replace(previous, target)
        raise


def remove(rolling: RollingProton, completion: Callable[[], None], error: Callable[[Exception], None]) -> None:
    """Move a rolling build to the trash."""
    if is_in_use(rolling):
        error(RollingProtonError(_("%s is in use by a running game.") % rolling.label))
        return
    system.remove_folder(proton.get_rolling_path(rolling), completion, error)


class RollingProtonUpdater(ComponentUpdater):
    """Brings an installed rolling build up to date along with Qtris's other components."""

    def __init__(self, rolling: RollingProton, release: str) -> None:
        self.rolling = rolling
        self.release = release
        self.downloader: SimpleDownloader | None = None
        self.progress = ProgressInfo(0, _("Updating %s") % rolling.label)

    @property
    def name(self) -> str:
        return self.rolling.label

    @classmethod
    def find(cls) -> list[ComponentUpdater]:
        """Return updaters for the installed rolling builds a newer release is out for."""
        updaters: list[ComponentUpdater] = []
        for rolling in proton_releases.ROLLING:
            if not is_installed(rolling):
                continue
            try:
                release = get_latest_release(rolling)
            except RollingProtonError as ex:
                logger.warning("Unable to check %s for updates: %s", rolling.label, ex)
                continue
            if needs_update(rolling, release) and not is_in_use(rolling):
                updaters.append(cls(rolling, release))
        return updaters

    def get_progress(self) -> ProgressInfo:
        if self.downloader is not None:
            return ProgressInfo(
                self.downloader.progress_fraction, _("Downloading %s") % self.release, self.downloader.cancel
            )
        return self.progress

    def install_update(self, updater: RuntimeUpdater) -> None:
        try:
            installed = install_release(self.rolling, self.release, self._download)
        except (RollingProtonError, OSError) as error:
            self.progress = ProgressInfo.ended(_("Could not update %s") % self.name)
            updater.check_path.unlink(missing_ok=True)
            raise QtrisError(_("Could not update %s: %s") % (self.name, error)) from error
        if installed:
            self.progress = ProgressInfo.ended(_("Updated %s to %s") % (self.name, self.release))
        else:
            self.progress = ProgressInfo.ended(_("%s will be updated later") % self.name)

    def _download(self, url: str, destination: str) -> bool:
        downloader = SimpleDownloader(url, destination, overwrite=True)
        self.downloader = downloader
        try:
            downloader.start()
            return downloader.join() and system.path_exists(destination, exclude_empty=True)
        finally:
            self.downloader = None
