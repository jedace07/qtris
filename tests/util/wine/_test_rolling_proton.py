"""Proton-CachyOS Latest: one folder that Qtris keeps on the newest release."""

import hashlib
import json
import tarfile
from pathlib import Path

import pytest

from qtris.util.wine import proton, proton_releases, rolling_proton
from qtris.util.wine.proton_releases import PROTON_CACHYOS_LATEST, get_rolling

OLD = "proton-cachyos-11.0-20261014-slr"
NEW = "proton-cachyos-11.0-20261015-slr"
ROLLING = get_rolling(PROTON_CACHYOS_LATEST)


@pytest.fixture
def builds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Serve a build of each release as a local archive, and keep Proton in tmp_path."""
    archives = {}
    for release in (OLD, NEW):
        script = tmp_path / f"{release}.sh"
        script.write_text(f"#!/bin/sh\n# {release}\n", encoding="utf-8")
        archive = tmp_path / "served" / f"{release}-x86_64.tar.xz"
        archive.parent.mkdir(exist_ok=True)
        with tarfile.open(archive, "w:xz") as tar:
            tar.add(script, arcname=f"{release}-x86_64/proton")
        archives[release] = archive
    checksums = {
        f"checksum:{release}": hashlib.sha512(path.read_bytes()).hexdigest() for release, path in archives.items()
    }
    monkeypatch.setattr(proton, "get_proton_dir", lambda: str(tmp_path / "proton"))
    monkeypatch.setattr(
        proton_releases, "get_proton_release_urls", lambda release: (str(archives[release]), "checksum:" + release)
    )
    monkeypatch.setattr(proton_releases, "get_proton_checksum", lambda url: checksums[url])
    monkeypatch.setattr(rolling_proton, "RUNNING_COMMANDS", set())
    return archives


class RunningGame:
    def __init__(self, env: dict[str, str]) -> None:
        self.env = env


def copy(url: str, destination: str) -> bool:
    Path(destination).write_bytes(Path(url).read_bytes())
    return True


def script(tmp_path: Path) -> str:
    return (tmp_path / "proton" / "Proton-CachyOS Latest" / "proton").read_text(encoding="utf-8")


def test_a_release_is_swapped_in_with_its_metadata(builds: dict[str, Path], tmp_path: Path) -> None:
    assert ROLLING is not None
    assert rolling_proton.install_release(ROLLING, OLD, copy)
    assert rolling_proton.get_installed_release(ROLLING) == OLD
    folder = tmp_path / "proton" / "Proton-CachyOS Latest"
    (folder / "user_settings.py").write_text("user_settings = {'PROTON_LOG': '1'}\n", encoding="utf-8")

    assert rolling_proton.install_release(ROLLING, NEW, copy)
    assert NEW in script(tmp_path)
    assert json.loads((folder / ".qtris-rolling.json").read_text(encoding="utf-8"))["release"] == NEW
    assert "PROTON_LOG" in (folder / "user_settings.py").read_text(encoding="utf-8")
    assert [path.name for path in (tmp_path / "proton").iterdir()] == ["Proton-CachyOS Latest"]


def test_a_corrupt_download_keeps_the_installed_release(
    builds: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert ROLLING is not None
    rolling_proton.install_release(ROLLING, OLD, copy)
    monkeypatch.setattr(proton_releases, "get_proton_checksum", lambda url: "0" * 128)
    with pytest.raises(rolling_proton.RollingProtonError):
        rolling_proton.install_release(ROLLING, NEW, copy)
    assert OLD in script(tmp_path)
    assert [path.name for path in (tmp_path / "proton").iterdir()] == ["Proton-CachyOS Latest"]


def test_a_build_in_use_is_left_alone(builds: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert ROLLING is not None
    rolling_proton.install_release(ROLLING, OLD, copy)
    game = RunningGame({"PROTONPATH": proton.get_rolling_path(ROLLING)})
    monkeypatch.setattr(rolling_proton, "RUNNING_COMMANDS", {game})
    assert not rolling_proton.install_release(ROLLING, NEW, copy)
    assert OLD in script(tmp_path)


def test_an_up_to_date_build_is_not_downloaded_again(builds: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    assert ROLLING is not None
    monkeypatch.setattr(proton_releases, "get_proton_releases", lambda family: (OLD,))
    assert rolling_proton.update(ROLLING, copy)
    assert not rolling_proton.update(ROLLING, lambda url, destination: pytest.fail("downloaded again"))


def test_the_component_updater_offers_newer_releases_of_installed_builds(
    builds: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert ROLLING is not None
    monkeypatch.setattr(proton_releases, "get_proton_releases", lambda family: (NEW,))
    assert rolling_proton.RollingProtonUpdater.find() == []  # Not installed: nothing to update
    rolling_proton.install_release(ROLLING, OLD, copy)
    updaters = rolling_proton.RollingProtonUpdater.find()
    assert len(updaters) == 1 and isinstance(updater := updaters[0], rolling_proton.RollingProtonUpdater)
    assert (updater.name, updater.release) == ("Proton-CachyOS Latest", NEW)


def test_the_rolling_build_is_listed_under_its_version(
    builds: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert ROLLING is not None
    rolling_proton.install_release(ROLLING, OLD, copy)
    monkeypatch.setattr(proton, "get_umu_path", lambda: "/umu/umu-run")
    monkeypatch.setattr(proton, "_iter_proton_locations", lambda: iter((proton.get_proton_dir(),)))
    proton.get_proton_versions.cache_clear()
    try:
        assert proton.get_proton_versions() == {PROTON_CACHYOS_LATEST: proton.get_rolling_path(ROLLING)}
        assert proton.list_proton_versions() == []
    finally:
        proton.get_proton_versions.cache_clear()
