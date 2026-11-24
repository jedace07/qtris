"""Update runtime components with their progress announced as plain data."""

import asyncio
import re
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from qtris import settings
from qtris.backend import runtime_updates
from qtris.backend.models import RuntimeUpdateStatus
from qtris.runtime import ComponentUpdater, get_components
from qtris.sysoptions import system_options
from qtris.util.progress_info import ProgressInfo
from qtris.util.upstream import ReleaseSource
from qtris.util.wine import proton, proton_releases, rolling_proton
from tests.conftest import FakeMainLoop


class FakeComponent(ComponentUpdater):
    def __init__(self, name: str, release: threading.Event) -> None:
        self._name = name
        self._release = release
        self.installed = False

    @property
    def name(self) -> str:
        return self._name

    def install_update(self, updater: Any) -> None:
        self._release.wait(5)
        self.installed = True

    def get_progress(self) -> ProgressInfo:
        if self.installed:
            return ProgressInfo.ended(f"<b>Updated {self._name}</b>")
        return ProgressInfo(0.5, f"Downloading {self._name}")


def test_updates_report_progress_and_run_one_at_a_time(
    monkeypatch: pytest.MonkeyPatch, fake_main_loop: FakeMainLoop
) -> None:
    release = threading.Event()
    components = [FakeComponent("dxvk", release), FakeComponent("umu", release)]
    forced: list[bool] = []

    class FakeRuntimeUpdater:
        def __init__(self, force: bool = False) -> None:
            forced.append(force)

        def create_component_updaters(self) -> list[ComponentUpdater]:
            return list(components)

    monkeypatch.setattr(runtime_updates, "RuntimeUpdater", FakeRuntimeUpdater)
    monkeypatch.setattr(runtime_updates, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(runtime_updates, "clear_wine_version_cache", lambda: None)
    statuses: list[RuntimeUpdateStatus] = []
    registration = runtime_updates.RUNTIME_UPDATE_CHANGED.register(statuses.append)

    async def run() -> None:
        first = asyncio.create_task(runtime_updates.update_runtime(force=True))
        await asyncio.sleep(0.05)
        assert (await runtime_updates.get_runtime_update_status()).running
        # A second request while one runs does nothing
        assert await runtime_updates.update_runtime() == ()
        release.set()
        assert await first == ("dxvk", "umu")

    try:
        asyncio.run(run())
        fake_main_loop.run_pending()
    finally:
        registration.unregister()

    assert forced == [True]
    assert statuses[0].running and statuses[0].components[0].status == "Downloading dxvk"
    assert statuses[0].components[0].fraction == 0.5
    final = statuses[-1]
    assert not final.running and [component.status for component in final.components] == ["Updated dxvk", "Updated umu"]
    assert all(component.finished for component in final.components)


def test_nothing_to_update(monkeypatch: pytest.MonkeyPatch, fake_main_loop: FakeMainLoop) -> None:
    class UpToDate:
        def __init__(self, force: bool = False) -> None:
            pass

        def create_component_updaters(self) -> list[ComponentUpdater]:
            return []

    monkeypatch.setattr(runtime_updates, "RuntimeUpdater", UpToDate)
    assert asyncio.run(runtime_updates.update_runtime()) == ()
    fake_main_loop.run_pending()


def test_versions_include_store_tools_and_omit_proton_and_missing_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(settings, "PROTON_DIR", str(tmp_path / "proton"))
    monkeypatch.setattr(runtime_updates, "LINUX_SYSTEM", SimpleNamespace(arch="x86_64"))
    umu = tmp_path / "runtime" / "umu"
    umu.mkdir(parents=True)
    (umu / "umu-run").touch()
    (umu / ".upstream-version").write_text("1.2.3\n", encoding="utf-8")
    database = tmp_path / "runtime" / "umu-games"
    database.mkdir()
    (database / "umu-database.csv").touch()
    winetricks = tmp_path / "runtime" / "winetricks"
    winetricks.mkdir()
    (winetricks / ".upstream-version").write_text("20261124", encoding="utf-8")
    for client, version in (("gogdl", "v1.2.3"), ("legendary", "0.20.34"), ("nile", "v1.1.2")):
        path = tmp_path / "runtime" / client
        path.mkdir()
        (path / client).touch()
        (path / ".upstream-version").write_text(version, encoding="utf-8")
    rolling = proton_releases.ROLLING[0]
    proton_path = Path(proton.get_rolling_path(rolling))
    proton_path.mkdir(parents=True)
    (proton_path / "proton").touch()
    (proton_path / rolling_proton.METADATA_FILE).write_text('{"release": "GE-Proton11-7"}', encoding="utf-8")

    assert dict(asyncio.run(runtime_updates.list_installed_versions())) == {
        "gogdl": "v1.2.3",
        "legendary": "0.20.34",
        "nile": "v1.1.2",
        "umu": "1.2.3",
        "umu-games": "",
    }


def test_runtime_components_come_from_individual_upstreams() -> None:
    components = {component.name: component.source for component in get_components("x86_64")}
    for name, expected in (
        ("umu", "Open-Wine-Components/umu-launcher"),
        ("gogdl", "Heroic-Games-Launcher/heroic-gogdl"),
        ("legendary", "legendary-gl/legendary"),
        ("nile", "imLinguin/nile"),
        ("winetricks", "Winetricks/winetricks"),
    ):
        source = components[name]
        assert isinstance(source, ReleaseSource)
        assert source.repo == expected
    assert not {"dxvk", "vkd3d", "lib32", "lib64", "network-tools"} & components.keys()


@pytest.mark.parametrize(("arch", "asset"), [("x86_64", "legendary_linux_x64"), ("aarch64", "legendary_linux_arm64")])
def test_legendary_uses_upstream_linux_asset_names(arch: str, asset: str) -> None:
    source = next(component.source for component in get_components(arch) if component.name == "legendary")
    assert isinstance(source, ReleaseSource)
    assert source.asset is not None and re.fullmatch(source.asset, asset)


def test_system_options_no_longer_offer_the_qtris_runtime() -> None:
    options = {option["option"] for option in system_options}
    assert not {"disable_runtime", "prefer_system_libs"} & options
